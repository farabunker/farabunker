# Agents P3 — the `/chat/` Surface and Flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the permanent chat product at `/chat/` and the flow runner. `/chat/` is a real surface — a conversation list, an agent picker, a thread that renders every turn including tool-call cards with image thumbnails and library citations, a 202 + poll submission path, and a plain POST + redirect path that works with JavaScript off. It is never an agent builder. Alongside it, a **flow** is a ROW — an ordered list of tool steps with `$`-references between them, run by the same `invoke_tool` the ReAct loop uses, with no LLM deciding anything — reachable through one registered `flow.run` tool whose choices are filled per turn from the flows that exist. Nothing on this box is seeded automatically: shipped agents and flows are a catalogue an operator adopts with a button or a command, and a row they adopt is theirs to edit. P3 also closes the three renderer-facing debts P2's ledger deferred: an open `ToolInvocation` row reads as *in progress*, never as an error; the blank-`text`-on-failure path renders the recorded error instead; and a turn that ends without its tool call finishing closes that row rather than leaving it open forever.

**Architecture:** Fourteen tasks (twelve at review round 2; the 2026-08-28 owner rulings added the data-model task and the auth-seams task, and split the flow declarations into a catalogue task). Task 2 fixes the audit debt in `agents/runtime/` (one migration, one new module, two call sites) so every later renderer has honest inputs. Tasks 3–11 build `agents/chat` bottom-up: the app and its mount, the index, the pure rendering layer, the thread, the POST path with a shared preflight, the poll view with its progressive-enhancement script, and the error surfaces. Tasks 12–13 build flows: the code-declared `FlowSpec` (Task 5), the runner with `$`-reference resolution (Task 12), and registration plus the resident flow and the planner's role walk (Task 13). Task 14 installs the guards, sweeps the docs, and runs the gate matrix and the verification ladder to fresh pixels.

**Tech Stack:** Python 3.12/3.13, Django 5, PostgreSQL on the branch preview port 5433, pytest + pytest-django. Plain Django views and `JsonResponse` — **not** DRF — for every new view, matching `tools/vision/views.py` (the repo's only existing HTML-page-plus-poll surface); `tools/rag/views.py`'s `AskView`/`AskJobStatusView` are DRF `APIView`s and are the *contract* precedent (202 shape, never-500, `setup_url`), not the framework precedent. No new Python dependencies. No static files: inline `<style>` in `{% block extra_style %}` and one inline `<script>`, per the repo's zero-static-files posture.

**Spec:** docs/superpowers/specs/2026-08-25-agents-and-tools-design.md — §6.5, §7.4, §8, §10, §11, §12.4, its "Corrections from plan authoring (2026-08-25)" section, and its "Long-term requirement: enterprise control and MCP interop (2026-08-27)" addendum. Plus `docs/superpowers/plans/2026-08-27-agents-p2-runtime.md`'s deviations D1–D8, its three review rounds, and `.superpowers/sdd/2026-08-27-agents-p2-runtime/progress.md`'s deferred items.

## Global Constraints

- **Depends on P0, P1, and P2 having landed.** P2 is complete on this branch at `3a63928`. Every path in this plan is written against **that tree** — `agents/models.py`, `agents/limits.py`, `agents/resident.py`, `agents/apps.py`, `agents/runtime/{prompt,invoke,loop,bindings,jobs,delegate}.py`, `agents/management/commands/{agent_turn,sync_agents}.py` (this phase replaces `sync_agents` with `install_defaults` — ruling 2), `agents/contracts/`, `models/contracts/`, `models/registry/`, `models/queue/`, `tools/rag/`, `tools/vision/`, `foundation/`. **Re-verify every `file:line` citation against the tree before executing a task** — a citation that has moved is a signal to re-read, not to guess. The spec still cites pre-regroup paths (`modules/rag/views.py`, `console/inference/bindings.py`, `console/jobs/worker.py`); every one of them has moved, and this plan names the post-regroup path.
- **Orchestrator gates — the full matrix, every task, no exceptions.** Two `FARABUNKER_FEATURES` states × two collection orders:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q                                            # configured collection order
  .venv/bin/pytest -q scripts agents foundation models tools     # reversed
  ```

  `pytest.ini` reads `testpaths = tools models foundation agents scripts`. **P3 changes it not at all** — every new test lands under `agents/` (including `agents/chat/tests/`, which `agents` already collects) or `foundation/`. A step that appears to need a `testpaths` edit is a step that put a test in the wrong place.
- **Private test DB per role. Never `test_farabunker`.** Implementers use `farabunker_impl` on the branch preview Postgres at **5433** (`docs/DEV.md:213-232`). **A second concurrent implementer uses `farabunker_ctl`** — P2's ledger records a real create/drop race between two parallel fixers on one database, and the controller's standing ruling from that incident is that any second concurrent role gets `farabunker_ctl` explicitly. Never a bare `test_farabunker`, never `5432`.
- **`agents/contracts/` still imports NO Django.** P3 adds nothing to that package. `agents/defaults.py` (Task 5) is the same *kind* of module as `agents/resident.py` — pure declarations, with its one row writer (`install_default`) importing Django **inside its body** — and is pinned by the same AST test shape. It is not inside `contracts/` and is not covered by `contracts/tests/test_purity.py`; Task 5 writes its own pin. Task 1 DOES touch `agents/contracts/tools.py` (`PRINCIPAL_KINDS` gains `"open"`) and that change is pure: no Django, no import, one tuple entry.
- **No `conftest.py`, anywhere.** `find . -name conftest.py` returns nothing and must keep returning nothing. New per-package helper module this phase: **`agents/chat/tests/_helpers.py`** (Task 3). Autouse fixtures stay *defined* per test module and delegate their bodies to `_helpers`.
- **The helper-duplication rule, and P3's one ruling on it.** P2's Global Constraints ruled that `_helpers.py` modules are **duplicated per app, never imported across apps** (`make_job_ctx` exists five times on purpose), with the carve-out that `agents/runtime/tests/_helpers.py` imports from `agents/tests/_helpers.py` because "that is one app, not two". `agents.chat` **is** a second Django app (Task 3), so the letter of that rule would force a sixth copy of `make_agent`/`make_conversation`/`make_turn`/`bind_chat_role`. **RULING: it does not.** The boundary that rule actually polices is the **column** — `tools/rag` vs `tools/vision` vs `models/registry` vs `agents` — because that is where one team's test scaffolding becoming load-bearing for another's is a real hazard. `agents/chat` is the same column, the same top-level package directory, and ships in the same commit series as the module it borrows from. `agents/chat/tests/_helpers.py` therefore imports the row builders and `bind_chat_role` from `agents/tests/_helpers.py` and defines only what is genuinely its own (the HTTP-layer queue doubles and the thread builders). **The cross-COLUMN rule is unchanged and still pinned** — no `agents/chat` test imports from `tools/*/tests/` or `models/*/tests/`, and Task 14 keeps that gate honest.
- **A fixture in `_helpers.py` is not visible until a test module imports it by name.** There is no `conftest.py`, so pytest never discovers a fixture defined in a helper module. Every test module that uses one writes an explicit re-export at the top — `from agents.chat.tests._helpers import fake_turn_queue  # noqa: F401` — and that import IS the registration. A fixture that is used but not imported fails with `fixture 'x' not found`, which is the honest failure. This applies to every fixture named in this plan: `bound_chat_role`, `fake_turn_queue`, `fake_queue_down`, `fake_queued_job`, and `fake_running_job`. `_snapshot_tools` is the one exception in form only — per P2's convention it stays *defined* as an `@pytest.fixture(autouse=True)` in each test module that registers a tool, with a two-line body delegating to `agents/tests/_helpers.py::snapshot_tools`/`restore_tools`; the module-local definition is its own registration.
- **THE VISION-FLAG RULE** (`tools/rag/tests/_helpers.py:9-38`, enforced by `tools/rag/tests/test_flag_hygiene.py`, which sweeps `tools`, `models`, `foundation`, and `agents` — and therefore already sweeps `agents/chat/tests/`): any test that overrides `settings.FARABUNKER_FEATURES` (directly, via the `settings` fixture, or via `override_settings`) **AND** performs an actual HTTP request or URL resolution in that same test (Django's test `Client`, `reverse()`) **MUST** keep `"vision"` in the overridden set. `config/urls.py` builds its `vision/` mount conditionally, once, at import time; Django resolves the URLconf lazily on the first request/`reverse()` in the process and never re-evaluates it, so one unlucky test poisons every later one. **Every chat test does HTTP or `reverse()`, so NO test in this phase overrides the flag at all.** Where P3 needs to prove behaviour on a vision-off install, it does it two narrower ways, both of which are the preferred shape whenever a test's real subject is the registry rather than the install: pop the key from `_TOOLS` under a snapshot/restore fixture (Task 13), or patch the *renderer's own* `reverse` to raise `NoReverseMatch` (Task 7).
- **Never-500, and never a fabricated answer.** Four obligations, all honesty obligations rather than defensiveness:
  - `turn_status` is **always 200 for a readable turn**. Queued, running, done, failed and cancelled all report state in the response *body*. Exactly two non-200s exist: **404** for a turn id that does not exist, and **503** for `QueueUnavailable` when the queue genuinely had to be consulted. This is `AskJobStatusView`'s rule (`tools/rag/views.py:1150-1160`) applied to a turn.
  - Every page view renders. A thread whose agent's chat role is unbound renders the thread with a banner, **not** a 503 page — the 503 belongs to the POST that would have queued work, not to reading what already happened.
  - The renderers never invent an outcome. An unfinished `ToolInvocation` reads as *in progress*; a `Turn.error` is shown verbatim and never a traceback (`models/queue/worker.py` already sets that rule for job failures).
  - `run_flow` never fabricates a step it did not run and never silently substitutes `None` for a reference that did not resolve.
- **No model names or versions.** Not in code, not in a `ToolSpec.description`, not in a `FlowSpec.description`, not in a template, not in a comment, not in a test name, not in this plan. The repository is going public and ADR 0010's third amendment (`docs/adr/0010-model-management-framework.md:290-380`) forbids the platform from naming a model for the operator. The chat page names a **role** and a **connection's operator-given name**, never a model id it chose to surface on its own.
- **ADR import law, post-regroup (spec §3.3), with P2's reading of rule 2 and the rule-3 gate P2 Task 14 installed:**
  - **Rule 1 — pure leaves are universally importable.** `foundation/format.py`, `foundation/files.py`, everything under `models/contracts/`, and everything under `agents/contracts/`. Any column, any direction.
  - **Rule 2 — Django apps are column-private.** `tools/rag`, `tools/vision`, `models/registry`, `models/queue`, `foundation/ops`, `foundation/setup`, `agents`, and **now `agents.chat`** are not importable across a column boundary, with exactly one exception: a `tools/*` **or** `agents/*` app MAY import `models.registry.bindings`, and only that module. `models.registry.models` and `models.registry.views` stay off-limits with no exception. This is pinned by `foundation/ops/tests/test_import_law.py::test_agents_reaches_models_registry_through_bindings_and_nothing_else`, which walks `git ls-files -- agents` — **so it already covers `agents/chat`.** `agents/chat/views/conversations.py` imports `models.registry.bindings.picker_options` and nothing else from that package.
  - **Rule 3 — cross-column *work* goes through a seam, never an import. `agents/**` never imports `tools.*` — not at module scope and not in a function body.** `foundation/ops/tests/test_import_law.py::test_no_agents_module_imports_a_tools_package` walks every tracked non-test `.py` under `agents/` and every `ast` import node in it. **This is the single hardest constraint on `agents/chat`**, and it has one concrete consequence the templates must honour: a tool card renders a vision output by **reversing a URL NAME** (`agents.contracts.artifacts.artifact_url_name` returns `"vision-output-file"` / `"rag-document-file"`), never by importing `tools.vision.models` or calling into `tools.vision.services`. A URL name is a string; reversing it is a Django-level lookup, not an import.
  - **Never `import models`.** Always `from models.<sub> import ...`.
- **`reverse()` on a feature-gated URL name can raise, and the renderer must survive it.** `vision-output-file` and `vision-input-file` only exist when `"vision"` is in `FARABUNKER_FEATURES` (`config/urls.py:19-20`). A conversation that recorded `output:12` on a box where vision was later turned off must still render — the artifact shows as a plain reference, not a broken link and not a 500. `agents/chat/rendering.py` catches `NoReverseMatch` at exactly one place, and Task 7 pins it.
- **Registration imports no implementation module and touches no database.** `AgentsConfig.ready()` (Task 13) gains exactly one line: `register_tool(FLOW_RUN)`, importing `agents.runtime.flowtool`. That module is admissible because its own module-scope imports are the two pure leaves `ready()` already pays for — `agents.contracts.tools` and `models.contracts.operations` — and its one Django-touching dependency (`agents.visibility`) is imported **inside** `narrowed_flow_spec`, which `ready()` never calls. Same lazy-service-import discipline the spec's correction §4 records for every `tools.py`, and Task 13 adds `agents/runtime/flowtool.py` to `_REGISTRATION_MODULES` so the AST sweep enforces it rather than the comment. `ready()` must still **not** import `agents.runtime.flow`, `agents.runtime.loop`, `agents.models`, `agents.chat.*`, or anything under `tools/` — `ToolSpec.runner` is a dotted-path **string** for exactly that reason. **One registered spec for every flow on the box**: the rows reach the model through that spec's per-turn `choices`, which is what keeps this method free of the database while flows are rows (ruling 1).
- **A tool runner must never block on a queue job.** `agents/runtime/flow.py` joins `RUNTIME_MODULES` in `foundation/ops/tests/test_column_boundaries.py` **in Task 12, the task that creates it** (as `audit.py` does in Task 2 and `preflight.py` in Task 9), and is swept for `get_job` like every other runtime module; Task 14 only verifies the list is complete. **A VIEW is not a tool runner** — `turn_status` calls `get_job` once per request and returns; it holds no execution slot, exactly as `agents/management/commands/agent_turn.py` does not and as `tools/vision/views.py::queue_job_status` already does. The guard's swept list therefore covers neither, and Task 14 makes that exclusion explicit rather than implicit.
- **Only ONE migration in this phase: `agents/0002_flow_owners_and_invocation_job`** (Task 1). It carries the whole phase's schema — the `Flow` model (ruling 1), `Agent.owner_kind`/`owner_key` (ruling 4b), and `ToolInvocation.queue_job_id` — because three migrations for one phase is three chances to deploy half of it. If a step appears to need any other migration, STOP and report.
- **Tests and docs ship with every task** (ADR 0008, `docs/adr/0008-engineering-standards.md:15-30`). TDD, in this order, every time: write the failing test, **run it and read the failure**, write the minimal implementation, run it green, update the docs, commit.
- **Doubles are seam-level, never method-level.** The queue double for a view test patches `enqueue`/`get_job` **on the module under test** (`agents.chat.service`), the way `agents/tests/_helpers.py::_patch_queue` already patches them on `agents.management.commands.agent_turn`. Nothing patches a view's own method, and nothing patches `models.contracts.bindings.resolve` — a real `ModelConnection` + `RoleBinding` row (`bind_chat_role`) is cheaper and more honest than a patch, and `resolve` is code under test.
- **Baseline: measure it, do not predict it.** Task 2 starts by recording `pytest -q --collect-only | tail -1` on this branch's HEAD; that number is this phase's floor. Every later task's expectation is a **delta**: the whole suite green, and the collected count up by exactly what that task's own per-file run reported. Never a hand-computed total — `@pytest.mark.parametrize` expands, so an arithmetic prediction is wrong more often than the code is. A count that goes *down*, or that differs between the configured and reversed collection orders, is a real defect and must be chased.
- **Branch-only.** No commits to `main`, no merges to `main`, no deploy from Tasks 1–13. Task 14 owns the ladder.
- **Commit trailers.** Every commit ends with:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```
- **Verification doctrine.** No "done"/"works"/"fixed"/"complete"/"shipped" language about `/chat/` or about flows until Task 14's ladder finishes with **fresh pixels on the owner's live system**. P3 is the first phase of this design that ships a page, so its ladder has a real browser rung and its Rung 4 is a screenshot of `/chat/` on the owner's live `:8000` showing a real conversation with a rendered citation. A green suite is Rung 1 and is not a claim of completion.
- **Restart rules bite this phase.** `docs/DEV.md:279-290`: "a change to ingest, queue, or job-kind code does not reach them until you restart them". `agents/runtime/**` — including the new `flow.py` — is job-kind code, and `agents/defaults.py` is read by `ready()` and by the install path. **Every smoke test in Task 14 restarts `watcher` and `worker` first** (`web` auto-reloads; that section says so). **No deploy step writes rows any more** (ruling 2): a deploy that changes the catalogue changes what is *offered*, and `manage.py install_defaults` is an explicit, idempotent thing an operator runs — or a button they press — never something a release does on their behalf.
- **NO CODE PATH WRITES A ROW THE OPERATOR DID NOT ASK FOR (ruling 2).** Not `AppConfig.ready()`, not a data migration, not a page render, not a deploy step. The only two writers of a shipped default are `agents/chat/views/defaults.py::default_install` and `manage.py install_defaults`, both of which route through `agents.defaults.install_default`, which is **create-if-absent** and never overwrites. Task 5 installs an AST guard that counts those call sites, because a ruling with no guard is a preference.
- **ONE PRINCIPAL POINT, ONE VISIBILITY LAYER (ruling 4).** `agents/chat/principal.py::principal_for_request` is the only request→`Principal` conversion in the codebase; `agents/visibility.py`'s three functions are the only place `agents/chat` reaches `Conversation`, `Agent`, or `Flow` rows for reading. `settings.ACCOUNTS_REQUIRED` (env-driven, default `False`) is the branch point, and its `True` path **raises** rather than degrading to the open principal. In open mode every visibility function returns everything, so none of this changes behaviour today — which is why Task 4 gives it a guard instead of a convention.

### The deviations from the spec, and why — twelve live, two retired

The spec (2026-08-25) predates P0/P1/P2, the 2026-08-27 addendum, and the 2026-08-28 owner rulings. Where the owner's direction and the real P2 tree disagree with it, **they win** and the deviation is recorded here rather than re-argued in each task.

**Twelve live (P3-D1, D4–D14) and two retired (~~D2~~, ~~D3~~).** The two retired ones were this plan's own, and the 2026-08-28 rulings struck them because the spec was right and the plan was wrong — see `## Amendment (2026-08-28)`. They are left in the table struck through rather than deleted, so a reader who remembers them finds out what happened rather than wondering whether they were ever considered.

| # | Spec says | P3 does | Why |
|---|---|---|---|
| **P3-D1** | §7 preamble: new Django app `agents/chat`, `name = "agents.chat"`, `label = "chat"` | **Unchanged — `agents/chat` IS its own Django app**, `label = "chat"`, no models, no migrations, no `ready()` body | Kept deliberately even though P2's D1 folded `agents/runtime` into the `agents` app. The reasons differ: `agents/runtime` holds *models* that must live in `agents/migrations/`, while `agents/chat` holds none — what it needs is **template discovery** (`APP_DIRS: True` finds `agents/chat/templates/chat/` only for an installed app) and its own `tests/` package with its own helpers. `agents/README.md` (P2-shipped) already documents it this way. Cost: one entry in `INSTALLED_APPS` and one in `foundation/ops/tests/test_app_labels.py::EXPECTED_LABELS`. **`chat` contributes nothing to `EXPECTED_TABLES`** — a label that owns no model owns no table. (That dict does change this phase, but for the `agents` label: Task 1 adds `agents.flow`.) |
| ~~**P3-D2**~~ | §7.4: `Flow` is a Django model | **RETIRED by owner ruling 1 (2026-08-28). The spec was right and this plan was wrong.** Flows are rows, exactly like agents. | The deviation argued that `ready()` cannot touch the database, so a row-declared flow could not be registered as a tool. True — and it turned out to be an argument about the *tool*, not about the *flow*. Ruling 1 registers **one** tool, `flow.run`, whose `flow` param's choices are filled per turn from enabled rows, and whose runner loads the row by slug at call time. `ready()` still touches no database; the flow is still a row. See P3-D11. |
| ~~**P3-D3**~~ | §6.5: one registered `flow.run` with params `flow` and `input` | **RETIRED by owner ruling 1 (2026-08-28). The spec was right; `flow.<slug>` is gone.** ONE registered tool, `flow.run`. | The deviation preferred `flow.<slug>` because a named tool reads better in a prompt than a generic runner with a key the model must guess. Ruling 1 answers that objection without the per-flow registration: `flow`'s **choices are filled per turn from the enabled `Flow` rows**, so the model sees the real slugs enumerated in its schema rather than guessing at them — a better prompt than `flow.<slug>` gave, and one that works for rows nobody declared in code. This is what makes flows-as-rows possible at all: N rows cannot become N registered specs without DB access in `ready()`. See P3-D11. |
| **P3-D11** | §6.5 leaves the `flow` param's options unstated | `flow.run`'s `flow` param is a **`"choice"` whose `choices` are empty at registration and filled PER TURN** from `visible_flows(principal)`, by narrowing the spec where the turn's tool schemas are built | The seam already exists and is already used this way: `_input_schema` emits `enum` only for a `"choice"` with non-empty `choices` and deliberately emits none for an engine-owned choice with empty ones (`agents/contracts/toolschema.py:70-71`), which is exactly the "options the engine supplies" case `Param.choices` documents (`models/contracts/operations.py:45-47`). The registered spec stays a frozen module-level constant; a `dataclasses.replace` with the live slugs happens where the per-turn tool list is built. `validate_tool_args` then rejects an unknown slug by name (`operations.py:349-350`) instead of the runner discovering it. |
| **P3-D12** | §7.5 and P2's D5: `sync_resident_agents` upserts every declared resident on every `manage.py sync_agents` | **Nothing writes a row automatically (ruling 2).** The declarations become a catalogue; `install_default(kind, slug, principal)` is create-if-absent; `manage.py install_defaults` replaces `sync_agents`; the page offers an "Add the default X" POST button. | The owner's ruling, and the mechanism it protects is ruling 3's: once an operator may edit a row that came from a shipped default, a sync that re-applies the declaration on every deploy would silently revert their work. Consent-gated seeding and user-owned residents are the same decision seen from two sides. |
| **P3-D13** | §7.5: a resident row is not editable, and `sync_resident_agents` needs no bypass (P2 added `_from_resident_sync` for the edit guard, its deviation D6) | **The edit-lock is removed entirely (ruling 3).** `resident=True` is an **origin marker**; `slug` stays immutable; `_from_resident_sync` is deleted; `install_defaults --reset <slug>` restores the shipped text. | A shipped default is a starting point, not a mirror. P2's lock existed to stop a row drifting from the code that declared it — a real concern while the code was the source of truth. Ruling 2 moves the source of truth to the row, so the lock now protects nothing and prevents the ordinary act of tuning a prompt. P2's D6 is retired with it. |
| **P3-D14** | The spec has no principal for an unauthenticated request | `Principal("open", "box")`, from `agents/chat/principal.py::principal_for_request`; `"open"` joins `PRINCIPAL_KINDS`; every `Agent`/`Flow`/`Conversation` create stamps `owner_kind`/`owner_key` | Ruling 4. A real principal kind rather than a `None` special case, so the audit row, `granted_tools`, and a future grants table all have a value to store and to join on — and so the day accounts arrive is the day a *different* kind starts appearing, not the day a null becomes non-null across three tables nobody can backfill. |
| **P3-D4** | §6.5, §12.4, and P2's D7: `agent.turn` gains `"mode": "flow"` and a `"flow"` payload key — "a flow as a turn" | **The payload keeps `"mode"` with its one legal value `"chat"`. Flow-as-TURN is not built.** | Nothing in P3 produces such a payload. `/chat/` picks an **agent**, not a flow; there is no flow-builder UI (§14 gap 2) and no flow picker in the owner's decision list. Building the second entry point now would add a `plan_turn` branch that declares no chat role, a `run_turn` branch that builds no LLM, and a payload key with no caller — which is **D7's own argument** ("a key with exactly one possible value is not forward compatibility, it is a field nobody can read honestly") applied one level up. Flow-as-tool ships, is granted, and is proven end to end; flow-as-turn is a named gap that costs one small task whenever a surface asks for it. |
| **P3-D5** | §6.5: `run_flow(flow_key: str, input: dict, ctx: ToolContext) -> ToolResult` | **`run_flow(args: dict, ctx: ToolContext) -> ToolResult`**, reading the slug from `args["flow"]`, loading the `Flow` ROW through `visible_flows(ctx.principal)`, and taking the inputs from `args["input"]`; the internal worker is `_run_steps(flow, inputs, ctx)` and takes the row | A registered runner's signature is fixed by the contract at `(args, ctx)` (`agents/contracts/tools.py`'s `ToolSpec.runner` docstring), and `run_flow` **is** the registered runner. Under ruling 1 the flow is named by an ARGUMENT rather than by `ctx.tool_key`, because there is one tool for every flow and the slug is what the model chose from the enumerated `choices`. The spec's three-argument shape survives as `_run_steps`, with the row standing in for the key — which is where every test that cares about flow semantics rather than tool plumbing points. |
| **P3-D6** | §8.4: RAG citations come from `Turn.data["citations"]` | The renderer reads **`data["citations"]` OR `data["results"]`** | A real spec inconsistency, verified against the tree: `tools/rag/tools.py::run_ask` returns `data={"citations": ...}` (`:270-273`) but `run_search` returns `data={"results": ..., "hybrid": ...}` (`:210-213`). Both lists carry `title`, `locator_text`, `score`, `document_id` — the four keys §8.4 names — so one renderer serves both. Reading only `citations` would silently render every `rag.search` card without its sources, which is the failure the citation block exists to prevent. Recorded here; **the spec is not edited**. |
| **P3-D7** | §8.5: "a **per-conversation** model picker" | A **per-turn** picker, submitted with the turn form and carried across redirects in the query string (`?connection=<pk>`) | `Conversation` has no `connection` column and P3 adds no migration for one. The query-string rule is `tools/vision/views.py::_create_url`'s own, stated there: "the pick lives in the query string, not a session, so a link is a complete description of what the page will show — which is what makes the chooser links, the redirect after a submission, and a bookmark all agree." The payload's `connection` has always been per-turn (`agents/runtime/bindings.py::resolve_chat` takes it per call), so this is the picker matching the mechanism rather than inventing durable state for it. |
| **P3-D9** | §8.3: a cancelled turn's body is `{"state"}` — "nothing else to say" | **`{"state", "error"}`** | There IS something else to say, and P2 wrote it: `on_turn_terminal` stores `"Cancelled from the queue before it ran."` on the turn row. The spec's sentence was written before that hook existed. Dropping the field would make the page render a blank cancelled card while the row it read holds the explanation. |
| **P3-D10** | §6.5 puts the flow runner in `agents/runtime/flows.py` (one module) | **`agents/defaults.py`** (the shipped catalogue and the shared JSON validator/parser) **+ `agents/runtime/flow.py`** (the runner) **+ `agents/runtime/flowtool.py`** (the one `flow.run` spec and its per-turn narrowing) | Three modules because they answer three different questions and have three different import budgets: the catalogue is read by `install_default` and by `Flow.save()`; the runner imports `agents.runtime.invoke`, which imports `agents.models`; the spec module is read by `AppConfig.ready()` and so may pay no Django import and touch no database. One module cannot be all three. Singular `flow.py` on the runtime side so a grep for one never lands on the other. |
| **P3-D8** | §8.3: `turn_status`'s done body is keyed on a job state | **`turn_status` reports `Turn.State` values — `queued`/`running`/`done`/`failed`/`cancelled` — never queue states** | The queue's vocabulary is `queued`/`running`/**`succeeded`**/`failed`/`cancelled` (`models/contracts/queue.py:69-80`); the turn's is `queued`/`running`/**`done`**/`failed`/`cancelled` (`agents/models.py::Turn.State`). P2's ledger records a live 960-second hang caused by exactly this drift (the CLI polled for `"done"` against the queue and never saw it; fixed at `3a63928` by exporting `TERMINAL_STATES` through the seam). The page polls a **turn**, so it reports turn states. The GUARD IS SERVER-SIDE: Task 10 pins that `_BODY_BUILDERS` is total over `Turn.State`, so a sixth state fails where the bodies are decided. No claim is made about the script's own vocabulary — the JS reads whatever `state` the body carries, and a plan that promised a template-side pin it does not write would be worse than one that says which end is guarded. |

### Named gaps this phase opens

**P3-G1 — `agent.<slug>` stays catalogue-registered while `flow.run` becomes row-driven, and that asymmetry is deliberate.** Ruling 1 made flows rows reachable through one tool with per-turn choices; the identical move is available for agents-as-tools (`agent.run` with a `flow`-shaped `agent` choice, filled from `visible_agents`), and it would let an operator's own agent be delegated to — which `agents/resident.py`'s own docstring already names as a deferral, because `ready()` may not read rows.

**It is NOT built now**, for two reasons worth writing down. First, scope: P3 is already fourteen tasks and the rulings added three of them; doing agents-as-tools the same way means touching `delegate.py`, `_tool_roles`'s closure walk, `MAX_AGENT_DEPTH`'s enforcement-by-omission, and the recursion guard, all of which P2 proved live and none of which flows share. Second, sequencing: **delegation is a grant decision** in a way running a flow is not — an agent that may delegate to any visible agent is an agent whose reach is the union of everybody's, which is a question Identity & Auth answers and this phase cannot. Recorded as the **P4 / auth-phase symmetry item**: when grants move to their own table, `agent.run` and `flow.run` become the same shape, and `agent.<slug>` retires.

### What P3 does NOT touch

| Item | Status |
|---|---|
| `tools/vision/tools.py`'s double preflight; `build_generate_spec()`'s rebuild-on-registration | **VISION-OWNED.** Noted, not planned, not fixed in passing. P3 renders whatever `vision.generate` recorded and never reshapes it. |
| `tools/vision/services.py`'s `from models.contracts.bindings import resolve` module-attribute leak (P2 Task 14 minor) | **VISION-OWNED backlog.** |
| `STATIC_ROOT` (P2 Task 14, owner decision) and `docs/DEV.md:159-162`'s stale snippet | **Deferred to P4** by P2's ledger. Not reopened here. |
| `plan_turn` possibly over-declaring a role for a multi-role tool that `_roles_resolve` later drops (P2 Task 9 minor) | **Still open, still deferred.** P3 *extends* `_tool_roles` for flows (Task 13) and inherits the same tolerance; it does not attempt the narrowing, which needs a run-time/enqueue-time reconciliation this phase has no reason to design. |
| Agent-builder UI, flow-builder UI, authentication, streaming, cooperative cancel of a *running* turn, turn resume, file uploads into a turn | **§14 gaps 1, 2, 4, 5, 6, 7, 10.** All still out of scope. |

### Open mode, stated once and explicitly (ruling 4)

There are no accounts on this box (§14 gap 4: "`/chat/` is unauthenticated, like every other surface"), and `settings.ACCOUNTS_REQUIRED` is `False`. P3 does **not** treat that as an absence to work around; it treats it as a posture with a name, and gives it a principal, an owner, and a filter point — all three of which are real today and none of which change shape when accounts arrive.

- **One request → principal point.** `agents/chat/principal.py::principal_for_request` is the only place in the codebase that turns a request into a `Principal`, and in open mode it returns `OPEN_PRINCIPAL` — `Principal("open", "box")`, a real kind in `PRINCIPAL_KINDS` (Task 1), not a `None` special case. `ACCOUNTS_REQUIRED = True` **raises** there rather than degrading to it.
- **Every create records the acting principal.** `Conversation`, `Agent`, and `Flow` all carry `owner_kind`/`owner_key`, and every row P3 writes stamps them — including a shipped default adopted from the Add button, which is owned by whoever adopted it. A row written with blanks is a row a later filter cannot reason about, and backfilling one is a migration nobody has the information to write.
- **One filter point per kind.** `agents/visibility.py`'s `visible_conversations` / `visible_agents` / `visible_flows` are where "what may this principal see" is answered, for the page **and** for the runtime (`flow.run`'s per-turn choices and `plan_turn`'s role walk both ask them). In open mode they return everything, which is the true answer for a box with nobody to hide anything from. **When Identity & Auth lands, those three functions grow a filter and nothing else does** — not a migration, not a second listing view, and not a template. Task 4's guard fails the build if any module under `agents/chat` reaches those managers directly.
- **The runtime is in scope too.** Because `visible_flows` is asked with the acting principal, flipping `ACCOUNTS_REQUIRED` changes which flows an agent can run, not merely which rows a page lists. That is the intended coupling and it is documented at both call sites rather than discovered later.

---

### Task 1: the P3 data model — `Flow`, owner columns, the audit stamp, and the end of the resident lock

**Ruling 1 (flows are rows), ruling 3 (user-owned residents), and ruling 4b (owner columns) are all schema, so they land together in ONE migration.** Doing them separately would mean three migrations for a phase the Global Constraints promise one, and would leave the tree in a state where a `Flow` row exists that nothing owns.

The shape change this task makes to P2 is the one worth reading twice. P2 gave `Agent` a **resident edit-lock**: `save()` refused any field change on a `resident=True` row unless `sync_resident_agents` passed `_from_resident_sync=True`, and `delete()` refused outright. That was correct under P2's model, where a resident row was a *projection of code* and a drifted row would make the code lie about what was running. **Ruling 3 replaces that model.** A shipped default is now a starting point an operator adopts, not a mirror the platform maintains: `resident=True` becomes an **origin marker** — "this row started life as a shipped default" — and nothing more. The operator owns the row from the moment it exists. `--reset <slug>` is how they get the original back, and it is a deliberate act with the shipped text in front of them.

What survives from the lock: **`slug` is immutable.** A slug is the key `flow.run` resolves, the key a granted `agent.<slug>` tool names, and the key `--reset` matches on; renaming one in place would silently repoint every reference to it.

**Files:**
- `agents/models.py` (the `Flow` model; `Agent.owner_kind`/`owner_key`; `ToolInvocation.queue_job_id`; `save()`/`delete()` rewritten)
- `agents/migrations/0002_flow_owners_and_invocation_job.py` (new, generated)
- `agents/contracts/tools.py` (`PRINCIPAL_KINDS` gains `"open"`)
- `agents/tests/test_models.py`, `agents/tests/test_resident.py` (P2's lock tests rewritten — ruling 3)
- `agents/contracts/tests/test_tools.py` (the principal-kind pin)
- `foundation/ops/tests/test_app_labels.py` (`EXPECTED_TABLES` gains the new table)
- `agents/README.md`

**Interfaces (exact signatures):**

```python
# agents/contracts/tools.py -- PURE changes, no Django (test_purity.py runs the
# package in a subprocess with no DJANGO_SETTINGS_MODULE at all)
PRINCIPAL_KINDS = ("open", "resident_agent", "user_agent", "api_client")

# ONE instance, beside the kind it is built from. Frozen and hashable, so a
# module-level constant is safe to share.
OPEN_PRINCIPAL = Principal("open", "box")
```

```python
# agents/models.py
class Flow(models.Model):
    slug = models.CharField(max_length=64)          # CI-unique
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    inputs = models.JSONField(default=list, blank=True)
    steps = models.JSONField(default=list, blank=True)
    resident = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        constraints = [models.UniqueConstraint(Lower("slug"), name="uniq_flow_slug_ci")]
        indexes = [models.Index(fields=["owner_kind", "owner_key"], name="agents_flow_owner")]

    def save(self, *args, **kwargs) -> None: ...   # validates inputs/steps, slug immutable

# Agent gains the same two owner columns, under its own explicitly NAMED
# index `agents_agent_owner` -- every index in this app is named (Django
# auto-names are 30-character hashes that change when a field list does,
# and `foundation/ops/tests/test_app_labels.py` pins table names for the
# same reason). `ToolInvocation` gains `queue_job_id` (see Task 2 for
# why).
```

**Steps:**

- [ ] **Record the baseline.** `.venv/bin/pytest -q --collect-only | tail -1` on this branch's HEAD. That number is P3's floor; every later task reports a delta, never a hand-computed total.
- [ ] **Re-verify the three P2 file:line citations this task edits** against HEAD `120c083`: `agents/contracts/tools.py:75` (`PRINCIPAL_KINDS`), `agents/models.py:36` (`_RESIDENT_MUTABLE_FIELDS`), `:86` (`save`), `:108` (`delete`), `:152` (`_refuse_resident_edit`), and `agents/tests/test_models.py:81-110` (the five lock tests). A citation that has moved is a signal to re-read, not to guess.
- [ ] Add `"open"` to `PRINCIPAL_KINDS` with the reason at the constant:
  ```python
  # "open" is the principal an UNAUTHENTICATED box acts as -- one
  # `Principal("open", "box")`, minted by
  # `agents.chat.principal.principal_for_request` and by nothing else.
  # It is a real principal kind rather than a `None` special case, so
  # every downstream reader -- `granted_tools`, the audit row, a future
  # grants table -- has a value to store and to join on, and so the day
  # accounts arrive is the day a DIFFERENT kind starts appearing, not the
  # day a null becomes non-null everywhere at once.
  PRINCIPAL_KINDS = ("open", "resident_agent", "user_agent", "api_client")
  ```
  Then declare `OPEN_PRINCIPAL = Principal("open", "box")` immediately below the `Principal` class, with:
  ```python
  # The principal an UNAUTHENTICATED box acts as. ONE instance, declared
  # beside the kind it is built from rather than in whichever module
  # happened to need it first -- `agents.chat.principal` and
  # `manage.py install_defaults` both hand it out, and two independently
  # constructed "open" principals would be the beginning of a second
  # spelling of who this box is. Frozen and hashable, so sharing one is
  # safe.
  OPEN_PRINCIPAL = Principal("open", "box")
  ```
  Add cases to `agents/contracts/tests/test_tools.py` pinning that `Principal("open", "box")` constructs, that `OPEN_PRINCIPAL` equals it, and that an unknown kind still raises. **`agents/contracts/` imports no Django and this change adds none** — `contracts/tests/test_purity.py` imports the package in a subprocess with no settings module and will catch a slip on the first run.
- [ ] Write the failing tests in `agents/tests/test_models.py`. **Rewrite P2's five lock tests rather than adding beside them** — a test asserting the old refusal and a test asserting the new permission cannot both be right, and leaving the first as `xfail` would leave the plan's own ruling ambiguous in the code:
  ```python
  class TestAgentOwnership:
      """RULING 3 (2026-08-28): a shipped default is a STARTING POINT an
      operator adopts, not a mirror the platform maintains.

      P2 held the opposite -- `resident=True` locked every field and
      `delete()` refused outright, because a resident row was a
      projection of `agents/resident.py` and a drifted row would make the
      code lie about what was running. The owner's ruling replaces that
      model: `resident` is now an ORIGIN MARKER ("this row started as a
      shipped default"), the operator owns the row, and `install_defaults
      --reset <slug>` is how the original comes back -- a deliberate act
      with the shipped text in front of them.
      """

      def test_a_row_that_came_from_a_shipped_default_is_freely_editable(self):
          agent = make_agent(resident=True)
          agent.system_prompt = "my own words"
          agent.save()
          agent.refresh_from_db()
          assert agent.system_prompt == "my own words"
          # And it still remembers where it came from.
          assert agent.resident is True

      def test_a_row_that_came_from_a_shipped_default_is_deletable(self):
          """P2 refused this because `Conversation.agent` is PROTECT and a
          delete would orphan history. PROTECT still holds -- a row with
          conversations still cannot be deleted, and that is the DATABASE
          saying so, which is the honest place for it."""
          make_agent(resident=True, slug="scratch").delete()
          assert Agent.objects.filter(slug="scratch").count() == 0

      def test_an_agent_with_conversations_still_cannot_be_deleted(self):
          """The guard that was doing the real work all along.

          `from django.db.models.deletion import ProtectedError` -- it is
          not in `django.db.models`' top level namespace, and importing it
          from the wrong place is the kind of error a test file gets away
          with until the first red run.
          """
          agent = make_agent(resident=True)
          make_conversation(agent=agent)
          with pytest.raises(ProtectedError):
              agent.delete()

      def test_the_slug_is_immutable(self):
          """The ONE piece of the lock that survives. A slug is what
          `flow.run` resolves, what an `agent.<slug>` grant names, and
          what `--reset` matches; renaming one in place would silently
          repoint every reference to it."""
          agent = make_agent(slug="general")
          agent.slug = "renamed"
          with pytest.raises(ValueError) as exc:
              agent.save()
          assert "general" in str(exc.value) and "renamed" in str(exc.value)

      def test_from_resident_sync_is_gone(self):
          """Anti-vacuous pin on the removal itself: a bypass keyword left
          in place would be a second, invisible way to save."""
          with pytest.raises(TypeError):
              make_agent().save(_from_resident_sync=True)

      def test_a_new_agent_records_its_owner(self):
          agent = make_agent(owner_kind="open", owner_key="box")
          assert (agent.owner_kind, agent.owner_key) == ("open", "box")
  ```
- [ ] Run them; read the failures. The `_from_resident_sync` test passes today for the wrong reason (the keyword exists), so **check it fails only after the removal**, not before.
- [ ] Rewrite `Agent.save()` and delete `_RESIDENT_MUTABLE_FIELDS`, `_refuse_resident_edit`, and the `delete()` override:
  ```python
      def save(self, *args, **kwargs) -> None:
          """Validate `tool_keys` (ruling R1), then refuse a slug change.

          RULING 3 (2026-08-28) removed P2's resident edit-lock and its
          `_from_resident_sync` bypass. `resident=True` is now an ORIGIN
          MARKER, not a lock: it records that this row started life as a
          shipped default, and the operator owns it from that moment.
          `manage.py install_defaults --reset <slug>` restores the
          shipped text; nothing else ever rewrites a row behind the
          operator's back.

          The slug stays immutable because it is a KEY, not a label:
          `flow.run` resolves one, an `agent.<slug>` grant names one, and
          `--reset` matches on one.
          """
          self._validate_tool_keys()
          if self.pk:
              stored_slug = (
                  type(self).objects.filter(pk=self.pk)
                  .values_list("slug", flat=True).first()
              )
              if stored_slug is not None and stored_slug != self.slug:
                  raise ValueError(
                      f"An agent's slug is immutable: {stored_slug!r} cannot become "
                      f"{self.slug!r}. Grants, tool keys, and `install_defaults "
                      f"--reset` all resolve it. Create a new agent instead."
                  )
          super().save(*args, **kwargs)
  ```
  and update the class docstring: the three-conditions paragraph stays; the resident paragraph is replaced by the origin-marker one.
- [ ] Add `owner_kind`/`owner_key` to `Agent`, with the comment naming ruling 4b and pointing at `Conversation`'s identical pair (P2 shipped those on the owner's 2026-08-27 ruling for the same reason: "my agents" becomes a filter, not a migration).
- [ ] Add `ToolInvocation.queue_job_id` exactly as Task 2's rationale requires — the column is declared here because this phase has ONE migration; Task 2 is where it is written to and read.
- [ ] Write the `Flow` model. Its `save()` validator is the row half of ruling 1:
  ```python
      def save(self, *args, **kwargs) -> None:
          """Validate `inputs`/`steps` against the SAME rules the shipped
          catalogue is checked by, then refuse a slug change.

          `agents.defaults.validate_flow_json(inputs, steps)` is the one
          validator (Task 5). A row and a shipped default that were
          checked by two different rules would be two different things
          wearing one name, and the row is the thing that actually runs.

          It checks SHAPE only -- every `$` reference syntactically
          resolvable against the steps that PRECEDE it, no step naming an
          `agent.*` or a `flow.*` key. Whether a step's tool is REGISTERED
          is an install fact checked at run time (ruling R1's tolerance:
          a vision step on a box with the vision flag off is a not-here,
          not a fault).
          """
  ```
- [ ] **Add `"agents.flow": "agents_flow"` to `EXPECTED_TABLES` in `foundation/ops/tests/test_app_labels.py` — and watch it go RED first.** That pin is a LITERAL, typed by hand from a real tree, precisely so it cannot be a derivation compared to itself (its own module docstring explains why, and records the red run that proved it). So: add the `Flow` model, run `.venv/bin/pytest -q foundation/ops/tests/test_app_labels.py`, **read the failure naming `agents.flow` as an unexpected model**, and only then add the line. A pin updated before the code it pins is a pin that proved nothing.
- [ ] Generate the migration: `.venv/bin/python manage.py makemigrations agents --name flow_owners_and_invocation_job`. Confirm it holds exactly one `CreateModel` (Flow) and four `AddField`s (`agent.owner_kind`, `agent.owner_key`, `toolinvocation.queue_job_id`, and nothing else) plus the two indexes and the `uniq_flow_slug_ci` constraint. `.venv/bin/python manage.py migrate --plan` must show it as the only pending migration in the project.
- [ ] **Strip the two `_from_resident_sync=True` call sites** in `agents/resident.py` (`:171` in the upsert loop and `:176` in the retire loop, re-verify both against HEAD). Deleting the keyword from `save()` without this leaves `sync_resident_agents` raising `TypeError` on its first call, which would take the whole P2 suite red inside this task. **Behaviour-neutral**: the bypass existed only to get past the edit guard this task removes, so the calls do exactly what they did before, minus the keyword. The FUNCTION itself stays until Task 5 replaces it with `install_default` — deleting it here would drag `manage.py sync_agents`, its command module, and its docs into a task about the data model.
- [ ] Rewrite `agents/tests/test_resident.py`'s expectations for the lock removal (the sync's own behaviour is unchanged; only what the rows will accept afterwards has changed), and **leave `sync_resident_agents` itself alone in this task**. A test that asserts the old sync's behaviour stays green here and is rewritten in Task 5, the task that changes the behaviour.
- [ ] Run `.venv/bin/pytest -q agents` — green.
- [ ] Run the **full gate matrix** (all four commands). Record the collected count and the delta.
- [ ] Update `agents/README.md`: the data-model table gains `Flow`; the resident paragraph is rewritten for ruling 3 (origin marker, operator-owned, `--reset`); the owner columns are named as ruling 4b's auth-ready seam.
- [ ] Commit: `feat(agents): flows are rows, residents are owned, and every row records its principal`.

---

### Task 2: `agents/runtime/audit.py` — an open invocation is IN PROGRESS, and a dead turn closes its rows

P2's ledger left three renderer-facing debts, all from the same root cause: `invoke_tool` creates the `ToolInvocation` row **before** the runner runs, with `outcome=ERROR` as a placeholder that `_finish` overwrites (`agents/runtime/invoke.py`). Three consequences, observed live on 2026-08-28 during job 69:

1. **An in-flight call reads as an error.** `finished_at IS NULL` means "still running", but `outcome` already says `error`. A renderer that trusted `outcome` alone would show a red card for a tool that is working perfectly.
2. **A crash mid-call leaves the row open forever.** Nothing ever closes it. The live incident produced exactly this row when the preview Postgres entered recovery.
3. **`ToolInvocation.text` is blank on every failure path.** `_finish` writes `row.text = text if result is not None else ""`, so the message lives in `row.error` — and a renderer reading `text` shows nothing at all for the one card that most needs words.

This task fixes all three **before** any renderer exists, so Tasks 7–11 are written against honest inputs rather than around dishonest ones.

The correlation mechanism is one new column. `ToolInvocation` deliberately has no FK to a `Turn` (2026-08-27 addendum consequence 3: an external MCP `tools/call` has no turn at all), and a crashed call has no `Turn` pointing at it either — the TOOL turn is written **after** the runner returns — so "close this turn's open rows" cannot be answered by walking `Turn.invocation`. `queue_job_id` answers it exactly, is available to `invoke_tool` for free (`ctx.job.job_id`), and is the same not-an-FK shape `Turn.queue_job_id` and `tools/vision/models.py:135-143`'s `GenerationJob.queue_job_id` already use for the same import-law reason.

**Files:**
- `agents/runtime/audit.py` (new)
- `agents/models.py` (**docstring only** — the `queue_job_id` column and the migration are Task 1's, since this phase has one migration)
- `agents/runtime/invoke.py` (stamp the column at both row-creation sites)
- `agents/runtime/jobs.py` (`on_turn_terminal` closes open rows)
- `agents/runtime/loop.py` (`run_turn`'s except closes open rows)
- `agents/runtime/tests/test_audit.py` (new)
- `agents/runtime/tests/test_invoke.py`, `agents/runtime/tests/test_jobs.py`, `agents/runtime/tests/test_loop.py` (extended)
- `agents/runtime/README.md`

**Interfaces (exact signatures):**

```python
# agents/runtime/audit.py
RUNNING = "running"

def invocation_state(invocation) -> str:
    """`"running"` while `finished_at IS NULL`, else the row's own
    `outcome`; `""` for `None`."""

def invocation_message(invocation, turn_text: str = "") -> str:
    """The operator-readable sentence for one tool call."""

def close_open_invocations(queue_job_id, *, reason: str) -> int:
    """Close every unfinished `ToolInvocation` stamped with
    `queue_job_id`. Returns how many rows moved. Never raises."""
```

```python
# agents/models.py -- declared in Task 1, written and read here
queue_job_id = models.BigIntegerField(null=True, blank=True, db_index=True)
```

**Steps:**

- [ ] Write the failing test file `agents/runtime/tests/test_audit.py`:
  ```python
  """`agents.runtime.audit` -- the three invocation-state truths every
  renderer depends on (P2 ledger, deferred to P3).

  These are not cosmetic. An open row that reads as an error would make
  a working tool call render red, and the live 2026-08-28 incident
  proved a crashed call leaves a row open forever with nothing to close
  it.
  """
  from __future__ import annotations

  import pytest
  from django.utils import timezone

  from agents.models import ToolInvocation
  from agents.runtime.audit import (
      RUNNING, close_open_invocations, invocation_message, invocation_state,
  )

  pytestmark = pytest.mark.django_db


  def _open_row(**overrides):
      fields = dict(
          principal_kind="resident_agent", principal_key="general",
          tool_key="rag.search", args={"query": "q"},
          outcome=ToolInvocation.Outcome.ERROR, queue_job_id=7,
      )
      fields.update(overrides)
      return ToolInvocation.objects.create(**fields)


  class _BoomManager:
      """A manager stand-in whose `filter` raises, so the never-raises
      guarantee is proven against a real exception rather than asserted."""

      def filter(self, *args, **kwargs):
          raise RuntimeError("database is gone")


  class TestInvocationState:
      def test_an_unfinished_row_reads_as_running_not_as_its_placeholder_outcome(self):
          """`invoke_tool` creates the row with `outcome=ERROR` as a
          placeholder it overwrites in `_finish`. Until then the ONLY
          honest reading is `finished_at`."""
          row = _open_row()
          assert row.outcome == ToolInvocation.Outcome.ERROR
          assert invocation_state(row) == RUNNING

      def test_a_finished_row_reads_as_its_own_outcome(self):
          row = _open_row(outcome=ToolInvocation.Outcome.OK,
                          finished_at=timezone.now())
          assert invocation_state(row) == ToolInvocation.Outcome.OK

      def test_no_invocation_at_all_is_the_empty_string_not_an_error(self):
          """`Turn.invocation` is SET_NULL, so a pruned audit row leaves
          a TOOL turn whose outcome is genuinely unknown. Rendering that
          as a failure would invent one."""
          assert invocation_state(None) == ""


  class TestInvocationMessage:
      def test_a_failure_reads_its_error_because_text_is_blank_on_that_path(self):
          """`invoke.py::_finish` writes `row.text` ONLY when a
          `ToolResult` came back, so every failure path stores its words
          in `error`. A renderer reading `text` would show an empty
          card."""
          row = _open_row(outcome=ToolInvocation.Outcome.ERROR,
                          text="", error="the engine refused the request",
                          finished_at=timezone.now())
          assert invocation_message(row) == "the engine refused the request"

      def test_the_turns_own_text_wins_when_there_is_one(self):
          """`Turn.text` is what the MODEL was told, discards clause and
          all (`loop._tool_message_text`), so it is strictly the fuller
          sentence."""
          row = _open_row(outcome=ToolInvocation.Outcome.OK, text="two results",
                          finished_at=timezone.now())
          told = "two results\n\n(Only rag.search was run this step.)"
          assert invocation_message(row, told) == told

      def test_an_open_row_says_it_is_still_running_rather_than_nothing(self):
          assert "still running" in invocation_message(_open_row()).lower()


  class TestCloseOpenInvocations:
      def test_it_closes_only_the_named_jobs_unfinished_rows(self):
          mine = _open_row(queue_job_id=7)
          other_job = _open_row(queue_job_id=8)
          already_done = _open_row(queue_job_id=7,
                                   outcome=ToolInvocation.Outcome.OK,
                                   text="fine", finished_at=timezone.now())

          moved = close_open_invocations(7, reason="the turn stopped")

          assert moved == 1
          mine.refresh_from_db()
          other_job.refresh_from_db()
          already_done.refresh_from_db()
          assert mine.finished_at is not None
          assert mine.error == "the turn stopped"
          assert other_job.finished_at is None
          # An OK row is never rewritten: that call finished, and this
          # helper's whole subject is calls that did not.
          assert already_done.outcome == ToolInvocation.Outcome.OK
          assert already_done.error == ""

      def test_a_null_job_id_closes_nothing_and_does_not_raise(self):
          """A turn whose `queue_job_id` was never stamped has nothing to
          correlate on. Closing every open row on the box instead would
          be far worse than closing none."""
          _open_row(queue_job_id=None)
          assert close_open_invocations(None, reason="x") == 0

      def test_it_never_raises_even_when_the_write_itself_fails(self, monkeypatch):
          """Both callers are terminal paths already handling a failure.
          A helper that raised there would replace the real reason a turn
          died with its own."""
          monkeypatch.setattr(
              "agents.runtime.audit.ToolInvocation.objects", _BoomManager(),
          )
          assert close_open_invocations(7, reason="x") == 0
  ```
- [ ] Run it: `.venv/bin/pytest -q agents/runtime/tests/test_audit.py`. Read the failure — a collection-time `ModuleNotFoundError: agents.runtime.audit`, and once that exists a `TypeError` on the unknown `queue_job_id` field. Both are the right red.
- [ ] `ToolInvocation.queue_job_id` already exists (Task 1 declared it, with the comment explaining that correlation is its only job). Extend that class's docstring with one paragraph:
  ```
      TWO FIELDS DECIDE HOW A ROW READS, AND `outcome` IS ONLY ONE OF
      THEM. This row is created BEFORE its runner runs, with
      `outcome=ERROR` as a placeholder `invoke_tool._finish` overwrites.
      `finished_at IS NULL` therefore means "still running", whatever
      `outcome` currently says, and every reader goes through
      `agents.runtime.audit.invocation_state` rather than reading
      `outcome` directly. `text` is likewise blank on every failure path
      (`_finish` writes it only when a `ToolResult` came back), so the
      words live in `error`.
  ```
- [ ] Confirm `makemigrations agents --check --dry-run` reports **nothing to do**: this task changes no schema, and a migration appearing here means a field edit crept in that Task 1 should have carried.
- [ ] Write `agents/runtime/audit.py`:
  ```python
  """How an audit row READS -- the one place `ToolInvocation` is
  interpreted, and the one place an unfinished one is closed.

  `agents.runtime.invoke.invoke_tool` creates a `ToolInvocation` BEFORE
  it runs the runner, with `outcome=ERROR` as a placeholder it
  overwrites once it knows better. That is the right shape -- a row
  exists even if the process dies -- but it means `outcome` alone is not
  a readable state, and any renderer reading it alone would show a red
  card for a call that is working. `finished_at` is the discriminator,
  and `invocation_state` is the only place that rule lives.

  It also means a crashed call leaves a row open forever. Observed live
  on 2026-08-28: a delegate's `agent.library` invocation was open when
  the database entered recovery, and nothing existed to close it.
  `close_open_invocations` is that something, called from the two
  terminal paths -- `run_turn`'s own `except` (the handler ran and
  raised) and `on_turn_terminal` (the handler never started).

  Pure reads and one conditional UPDATE. It never raises: both callers
  are already handling a failure, and a helper that raised there would
  replace the real reason a turn died with its own.
  """
  from __future__ import annotations

  import logging

  from django.utils import timezone

  from agents.models import ToolInvocation

  logger = logging.getLogger(__name__)

  # Not a member of `ToolInvocation.Outcome`, deliberately: an outcome is
  # what a FINISHED call produced, and "running" is the absence of one.
  # Putting it in the enum would put a non-outcome in the column every
  # later report is built on.
  RUNNING = "running"

  _STILL_RUNNING = "This tool call is still running."


  def invocation_state(invocation) -> str:
      """`"running"`, one of `ToolInvocation.Outcome`'s five values, or
      `""` for no row at all.

      `""` is not an error either: `Turn.invocation` is `SET_NULL`, so a
      pruned audit row leaves a TOOL turn whose outcome is genuinely
      unknown, and rendering that as a failure would invent one.
      """
      if invocation is None:
          return ""
      if invocation.finished_at is None:
          return RUNNING
      return invocation.outcome


  def invocation_message(invocation, turn_text: str = "") -> str:
      """The sentence to show for one tool call.

      `turn_text` -- `Turn.text`, what the MODEL was told -- wins
      whenever it exists, because `agents.runtime.loop._tool_message_text`
      builds it from the runner's own words PLUS the discarded-calls
      clause, so it is strictly the fuller sentence.

      Falls back to `invocation.error`, NOT to `invocation.text`:
      `invoke.py::_finish` writes `text` only when a `ToolResult` came
      back, so on every refusal, param error, and raise it is blank and
      the words are in `error`.
      """
      if turn_text:
          return turn_text
      if invocation is None:
          return ""
      if invocation.finished_at is None:
          return _STILL_RUNNING
      return invocation.error or invocation.text or ""


  def close_open_invocations(queue_job_id, *, reason: str) -> int:
      """Close every unfinished `ToolInvocation` stamped with
      `queue_job_id`, and return how many moved.

      ONE conditional UPDATE, filtered on `finished_at__isnull=True`, so
      a call that finished in the window between this being scheduled
      and running is never rewritten -- the same shape
      `on_turn_terminal`'s own `state__in` guard uses, for the same
      reason.

      `outcome` is left at whatever the row holds, which for an open row
      is always the `ERROR` placeholder `invoke_tool` created it with. A
      call interrupted before it reported did in fact not succeed, and
      inventing a sixth outcome class for it would put a value in the
      column that no runner can ever produce.

      A falsy job id closes NOTHING. A turn whose `queue_job_id` was
      never stamped has nothing to correlate on, and closing every open
      row on the box instead would be far worse than closing none.

      NEVER RAISES.
      """
      if not queue_job_id:
          return 0
      try:
          return ToolInvocation.objects.filter(
              queue_job_id=queue_job_id, finished_at__isnull=True,
          ).update(finished_at=timezone.now(), error=reason)
      except Exception:  # noqa: BLE001 -- never mask the failure being cleaned up after
          logger.exception(
              "agents: could not close the open tool-invocation rows for queue job %r",
              queue_job_id,
          )
          return 0
  ```
- [ ] Run `.venv/bin/pytest -q agents/runtime/tests/test_audit.py` — green. Record the per-file collected count.
- [ ] Stamp the column in `agents/runtime/invoke.py`. In **both** `ToolInvocation.objects.create(...)` calls (`invoke_tool` and `invoke_unknown_tool`) add `queue_job_id=getattr(tool_ctx.job, "job_id", None),`, with this comment above the first:
  ```python
      # Stamped at CREATION, not at finish: the whole point of this
      # column is to find a row whose call never reached `_finish`
      # (`agents.runtime.audit.close_open_invocations`). `getattr` rather
      # than `tool_ctx.job.job_id` because a `ToolContext` built by a
      # test double -- or by a future MCP edge with no job at all --
      # carries none, and an audit write must never be the thing that
      # raises.
  ```
- [ ] Add to `agents/runtime/tests/test_invoke.py` (read the module first and reuse its existing spec/fixture style — do not introduce a second way to build a `ToolSpec` in a file that already has one):
  ```python
  def test_every_invocation_records_the_job_it_ran_inside(self):
      """Without this stamp `close_open_invocations` has nothing to
      correlate on, and a crashed call stays open forever (the live
      2026-08-28 incident)."""
      ctx = make_tool_ctx(job=make_job_ctx(job_id=4242))
      outcome = invoke_tool(STUB_SPEC, {"query": "q"}, ctx)
      assert ToolInvocation.objects.get(pk=outcome.invocation_id).queue_job_id == 4242

  def test_an_unknown_tool_call_is_stamped_too(self):
      """A second creation site that forgot the stamp would leave
      exactly the rows hardest to explain."""
      ctx = make_tool_ctx(job=make_job_ctx(job_id=4242))
      outcome = invoke_unknown_tool("no__such", {}, ctx)
      assert ToolInvocation.objects.get(pk=outcome.invocation_id).queue_job_id == 4242
  ```
- [ ] Teach `on_turn_terminal` (`agents/runtime/jobs.py`) to close open rows. Add `from agents.runtime.audit import close_open_invocations` at the top and this module-level constant:
  ```python
  _INTERRUPTED = (
      "The turn running this tool call ended before the call reported an outcome."
  )
  ```
  then append to the function body, after the existing `Turn.objects.filter(...).update(...)`:
  ```python
      # The turn's OWN audit rows, closed on the same terminal event.
      # This reads one column before it writes, which the paragraph above
      # rules out for the TURN's state -- and the distinction is real:
      # the read here is for CORRELATION (`queue_job_id` is immutable
      # once stamped), not for a state this hook then conditionally
      # overwrites. The write itself is still one conditional UPDATE,
      # filtered on `finished_at IS NULL`.
      job_id = (
          Turn.objects.filter(pk=turn_id)
          .values_list("queue_job_id", flat=True).first()
      )
      close_open_invocations(job_id, reason=_INTERRUPTED)
  ```
  and extend the docstring with:
  ```
      IT ALSO CLOSES THE TURN'S OPEN TOOL-INVOCATION ROWS. A job
      cancelled while still queued has none; a job orphaned twice may.
      Leaving one open makes it read as "still running" forever
      (`agents.runtime.audit.invocation_state`), which is the one reading
      worse than an honest failure.
  ```
- [ ] Teach `run_turn`'s `except` (`agents/runtime/loop.py`) the same, **inside the existing inner `try`** that already guards the FAILED writeback, so a dead database still cannot mask the original exception:
  ```python
          try:
              Turn.objects.filter(pk=payload["turn"]).update(
                  state=Turn.State.FAILED, error=str(exc),
              )
              # The handler RAN, so `on_turn_terminal` will not fire
              # (`Worker._execute`'s `handler_started` gate) -- which
              # makes this the only place a mid-call crash's open audit
              # row can be closed. `ctx.job_id`, not the turn's stamped
              # column: the handler is holding the live JobContext.
              close_open_invocations(
                  ctx.job_id,
                  reason="The turn running this tool call failed before the call "
                         "reported an outcome.",
              )
          except Exception:
  ```
  with `from agents.runtime.audit import close_open_invocations` at the top of the module.
- [ ] Add to `agents/runtime/tests/test_jobs.py`:
  ```python
  def test_on_turn_terminal_closes_the_turns_open_invocation_rows(self):
      """The live 2026-08-28 incident: a delegate's call was open when
      its turn died, and nothing closed it."""
      turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                       queue_job_id=99)
      open_row = ToolInvocation.objects.create(
          principal_kind="resident_agent", principal_key="general",
          tool_key="rag.search", outcome=ToolInvocation.Outcome.ERROR,
          queue_job_id=99,
      )

      on_turn_terminal({"turn": turn.pk}, "cancelled")

      open_row.refresh_from_db()
      assert open_row.finished_at is not None

  def test_on_turn_terminal_leaves_another_jobs_open_row_alone(self):
      """Anti-vacuous pin: the correlation IS the point. A hook that
      closed every open row on the box would silently kill a concurrent
      turn's live call."""
      turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                       queue_job_id=99)
      other = ToolInvocation.objects.create(
          principal_kind="resident_agent", principal_key="library",
          tool_key="rag.ask", outcome=ToolInvocation.Outcome.ERROR,
          queue_job_id=100,
      )

      on_turn_terminal({"turn": turn.pk}, "failed")

      other.refresh_from_db()
      assert other.finished_at is None
  ```
- [ ] Add to whichever module already owns `run_turn`'s failure path (read `agents/runtime/tests/test_loop.py` and put it beside its siblings):
  ```python
  def test_a_handler_that_raises_closes_its_own_open_invocation_rows(self):
      """`on_turn_terminal` does NOT fire when the handler ran and raised
      (`handler_started`), so this except is the only closer on this
      path -- and the original exception must still propagate."""
  ```
  Drive it with a scripted `FakeToolLLM` whose second `chat` call raises, after the first tool call has opened its row; assert the row closed, the turn is FAILED, and the raised exception reached the caller unchanged.
- [ ] Add `"agents/runtime/audit.py"` to `RUNTIME_MODULES` in `foundation/ops/tests/test_column_boundaries.py`, **in this task rather than in Task 14**: a runtime module that exists but is not swept is a rule that silently does not apply to it, and the list is only ever correct if it grows in the same commit as the file.
- [ ] Run `.venv/bin/pytest -q agents foundation/ops` — green.
- [ ] Run the **full gate matrix** (all four commands). Green in both flag states and both collection orders. Record the collected count and confirm it is the baseline plus this task's per-file delta.
- [ ] Update `agents/runtime/README.md`: add `audit.py` to its module table — "how an audit row reads (`finished_at IS NULL` is *running*, not *error*) and the one conditional UPDATE that closes a turn's open rows" — and add a short **Reading an invocation** section stating both rules (`invocation_state`; `error` not `text` on a failure) so the next renderer author finds them without re-deriving them from an incident.
- [ ] Commit: `fix(agents): an open tool-invocation row is in progress, and a dead turn closes its rows`.

---

### Task 3: the `agents/chat` app — package, mount, nav, base template, helpers

The scaffold, and nothing else. It ships one real page (an empty index) so the mount, the nav link, the template base, and the test helpers are all proven by a request that actually returns 200 before any behaviour is layered on.

**§8.1 says `/chat/` is UNGATED, and gives the reason: `docs/DEV.md:235-246` fixes the two supported suite states at `'vision,media'` and `'vision'`. A third feature token would either break that contract or leave the whole surface untested in one of the two states.** Verify against `config/urls.py` before editing: the file has exactly one conditional mount (`if "vision" in settings.FARABUNKER_FEATURES`) and **five** unconditional ones (`admin/`, `rag/`, `inference/`, `queue/`, `setup/`). `/chat/` becomes the sixth. Count them before editing rather than trusting this sentence.

**Files:**
- `agents/chat/__init__.py`, `agents/chat/apps.py`, `agents/chat/urls.py` (new)
- `agents/chat/views/__init__.py`, `agents/chat/views/conversations.py` (new)
- `agents/chat/templates/chat/base.html` (new)
- `agents/chat/tests/__init__.py`, `agents/chat/tests/_helpers.py`, `agents/chat/tests/test_mount.py` (new)
- `agents/chat/README.md` (new)
- `config/settings.py` (`INSTALLED_APPS`)
- `config/urls.py` (the mount)
- `foundation/templates/_shell.html` (the nav link and its block)
- `foundation/ops/tests/test_app_labels.py` (`EXPECTED_LABELS`)

**Interfaces (exact signatures):**

```python
# agents/chat/apps.py
class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agents.chat"
    label = "chat"
    # NO ready(). Nothing to register.
```

```python
# agents/chat/urls.py -- the full table, filled in across Tasks 6-11
urlpatterns = [
    path("", ChatIndexView.as_view(), name="chat-index"),
    path("start/", conversation_start, name="chat-start"),
    path("c/<uuid:conversation_id>/", ConversationView.as_view(), name="chat-conversation"),
    path("c/<uuid:conversation_id>/turn/", turn_create, name="chat-turn"),
    path("c/<uuid:conversation_id>/delete/", conversation_delete,
         name="chat-conversation-delete"),
    path("turns/<int:turn_id>/", turn_status, name="chat-turn-status"),
]
```

**Steps:**

- [ ] **Check the nav before touching it.** Run `grep -rn "top-nav\|nav_current\|Image generation" --include='*.py' --include='*.html' .` and read every hit. Confirm no test asserts the nav's link ORDER or its exact link count before adding what will be the **ninth** entry (eight exist today: Ask, Search, Document library, History, Queue, Model setup, Setup, and the feature-gated Image generation).
- [ ] Write the failing test `agents/chat/tests/test_mount.py`:
  ```python
  """The mount, the label, and the nav -- proven by a real request.

  NO `FARABUNKER_FEATURES` OVERRIDE ANYWHERE IN THIS PACKAGE. Every test
  here uses `reverse()` or the test `Client`, which makes it exactly the
  kind of test THE VISION-FLAG RULE (`tools/rag/tests/_helpers.py:9-38`)
  exists to police: one flag override without `"vision"` in the set
  poisons URL resolution for the whole process. `/chat/` is ungated, so
  no test here has any reason to touch the flag at all.
  """
  from __future__ import annotations

  import pytest
  from django.apps import apps
  from django.urls import reverse

  pytestmark = pytest.mark.django_db


  class TestTheAppExists:
      def test_the_chat_app_is_installed_under_its_own_label(self):
          """Its own label, not `agents` -- template discovery
          (`APP_DIRS: True` finds `agents/chat/templates/`) and a future
          model both depend on it (spec section 7 preamble, deviation
          P3-D1)."""
          assert apps.get_app_config("chat").name == "agents.chat"

      def test_the_chat_app_owns_no_models(self):
          """P3 adds no chat table and no chat migration. A model
          appearing here is a migration nobody planned."""
          assert list(apps.get_app_config("chat").get_models()) == []


  class TestTheMount:
      def test_chat_index_resolves_and_renders(self, client):
          response = client.get(reverse("chat-index"))
          assert response.status_code == 200

      def test_the_mount_is_ungated(self):
          """Section 8.1: not behind a FARABUNKER_FEATURES token,
          because `docs/DEV.md:235-246` fixes the two supported suite
          states and a third token would leave this surface untested in
          one of them. Read as source, so a later `if` around the mount
          fails here rather than silently in one gate state."""
          from pathlib import Path

          from django.conf import settings

          text = (Path(settings.BASE_DIR) / "config" / "urls.py").read_text()
          mount = 'path("chat/", include("agents.chat.urls"))'
          assert mount in text
          head, _, tail = text.partition(mount)
          # The one conditional block in this file is the vision mount at
          # the very end; the chat mount must sit ABOVE it, inside the
          # unconditional list.
          assert "if \"vision\" in settings.FARABUNKER_FEATURES" in tail
          assert "if \"vision\" in settings.FARABUNKER_FEATURES" not in head


  class TestTheNav:
      def test_every_page_links_to_chat(self, client):
          """The shell owns ONE nav partial so every page agrees on
          every link (`foundation/templates/_shell.html:20-31`). A link
          added to one page's own template would be the drift that
          partial exists to prevent."""
          response = client.get(reverse("rag-ask-page"))
          assert reverse("chat-index") in response.content.decode()

      def test_the_chat_page_marks_its_own_nav_entry_current(self, client):
          response = client.get(reverse("chat-index"))
          body = response.content.decode()
          assert f'href="{reverse("chat-index")}" class="current"' in body
  ```
- [ ] Run it. Read the failure: `django.urls.exceptions.NoReverseMatch: Reverse for 'chat-index' not found`. That is the right red.
- [ ] Create the package. `agents/chat/__init__.py` empty; `agents/chat/apps.py`:
  ```python
  """The chat surface's Django app (spec section 7 preamble, deviation P3-D1).

  Its OWN app with its OWN label, unlike `agents/runtime/` which P2's
  deviation D1 folded into the `agents` app. The reasons differ and both
  are mechanical: `agents/runtime` holds MODELS that must land in
  `agents/migrations/`, while this package holds none. What it needs
  instead is TEMPLATE DISCOVERY -- `TEMPLATES[0]["APP_DIRS"] = True`
  (`config/settings.py`) finds `agents/chat/templates/chat/` only for an
  installed app -- and its own `tests/` package with its own helpers.

  NO `ready()`. There is nothing to register: the `chat.converse` role,
  the `agent.turn` job kind, and every tool spec belong to
  `agents/apps.py::AgentsConfig`, which already runs. An empty `ready()`
  here would be a hook somebody later fills in with a registration that
  belongs one level up.

  NO MODELS, on purpose. `label = "chat"` is declared explicitly anyway,
  per the contract spec section 3.5 pins: every AppConfig in this project
  sets `label`, so a package move can never rename a table. A future chat
  model gets a table named `chat_*` and a migration of its own, and
  nothing about that has to be decided now.
  """
  from __future__ import annotations

  from django.apps import AppConfig


  class ChatConfig(AppConfig):
      default_auto_field = "django.db.models.BigAutoField"
      name = "agents.chat"
      label = "chat"
  ```
- [ ] Add `"agents.chat",` to `INSTALLED_APPS` in `config/settings.py`, immediately after `"agents",`, with a comment:
  ```python
      "agents",
      # The `/chat/` surface. Its own app for its own templates and tests;
      # it owns no model and no migration (agents/chat/apps.py says why).
      "agents.chat",
  ```
- [ ] Add `"chat"` to `EXPECTED_LABELS` in `foundation/ops/tests/test_app_labels.py`, and extend that constant's comment:
  ```python
  # "chat" joined in P3 Task 3 (agents/chat/apps.py) -- like "agents"
  # before it, a genuinely NEW app rather than a rename, so it is an
  # addition to this set rather than evidence the pin's mechanism
  # failed. `agents.chat` itself owns no model, so it contributes no
  # entry to EXPECTED_TABLES -- but P3 Task 1 adds `agents.flow` to that
  # dict for the `agents` app, so do not read this line as "P3 changes
  # no tables".
  EXPECTED_LABELS = frozenset({
      "agents", "chat", "rag", "vision", "inference", "jobs", "setup", "ops",
  })
  ```
- [ ] Write a minimal `agents/chat/views/__init__.py` and `agents/chat/views/conversations.py` carrying only `ChatIndexView` for now:
  ```python
  # agents/chat/views/__init__.py
  """The chat surface's views, split by what they act on.

  A package rather than one module because the six views divide cleanly
  into three subjects -- the conversation list, one thread, and one turn
  -- and each carries a paragraph of contract (the 202 shape, the
  never-500 rule, the no-JS path) that reads better beside its own view
  than in a 600-line file. `urls.py` imports every name from HERE, so the
  split is an implementation detail of this package and moving a view
  between modules never touches a URL.

  THE IMPORT DIRECTION INSIDE THIS PACKAGE IS ONE-WAY: `turns.py` ->
  `thread.py` -> (nothing). `turn_create`'s 503 path re-renders the whole
  thread, so it imports `thread_context`; `thread.py` imports NOTHING
  from `turns.py`, which is why the poller's three constants live in
  `agents/chat/service.py` (a module both import) rather than in
  `turns.py` where the view that uses them is. A constant reached by an
  import back up the chain is a cycle waiting for its second caller.
  `conversations.py` likewise imports `service.py`, never `turns.py`.
  """
  from agents.chat.views.conversations import ChatIndexView

  __all__ = ["ChatIndexView"]
  ```
  ```python
  # agents/chat/views/conversations.py
  """The conversation list and its lifecycle."""
  from __future__ import annotations

  from django.views.generic import TemplateView


  class ChatIndexView(TemplateView):
      """GET /chat/ -- every conversation on this box, and the form that
      starts a new one.

      EVERY conversation, with no owner filter, and that is the honest
      pre-auth behaviour rather than an oversight. There are no users on
      this box (spec section 14 gap 4: `/chat/` is unauthenticated like
      every other surface). `Conversation.owner_kind`/`owner_key` exist
      (P2, ledger ruling 2026-08-27) and stay blank; when Identity & Auth
      lands, THIS QUERYSET is the line that grows a filter -- not a
      migration, and not a second listing view.
      """

      template_name = "chat/index.html"
  ```
  and the smallest `chat/index.html` that extends the base (the real one lands in Task 6).
- [ ] Write `agents/chat/templates/chat/base.html`:
  ```html
  {% extends "_shell.html" %}
  {% comment %}
  App-local base for the /chat/ pages. Extends the shared project shell
  (foundation/templates/_shell.html) so the design tokens, dark-mode
  overrides, and the global nav live in ONE place -- exactly as
  tools/rag/templates/rag/base.html and tools/vision/templates/vision/
  base.html do.

  Template inheritance creates no Python import, so `models/contracts/`
  purity and column-privacy are unaffected by this file extending a
  project-level template.

  Marking the shared nav's "Chat" entry current is done the way every
  other page does it -- by overriding the matching `nav_current_*` block
  with `current`. It sits on this BASE rather than on each leaf page
  because every page under /chat/ belongs to that one nav destination.

  Every rule the thread and its fragments need lives HERE, not on a
  page: `_turn_card.html` and `_tool_card.html` are rendered by the
  conversation page AND standalone by `turn_status`'s poll, and a page
  cannot own rules a fragment two views render.
  {% endcomment %}
  {% block nav_current_chat %}current{% endblock %}
  {% block extra_style %}
    :root { --page-max-width: 900px; }
    .chat-wrap { max-width: var(--page-max-width); margin: 0 auto; }
    .banner {
      border: 1px solid var(--border); border-radius: 8px; background: var(--panel);
      padding: 0.75rem 1rem; margin-bottom: 1rem;
    }
    .banner.warn { border-color: var(--accent); }
    .banner a { color: var(--accent); }
    .muted { color: var(--muted); font-size: 0.85rem; }
    {% block chat_style %}{% endblock %}
  {% endblock %}
  {% block content %}
  <div class="chat-wrap">
    {% block chat_content %}{% endblock %}
  </div>
  {% endblock %}
  ```
- [ ] Write `agents/chat/urls.py` with only the index route for now, and the docstring:
  ```python
  """URL routes for the chat surface, mounted UNGATED at /chat/
  (config/urls.py).

  Ungated for the reason spec section 8.1 gives: `docs/DEV.md:235-246`
  fixes the two supported suite states at 'vision,media' and 'vision',
  and a third feature token would either break that contract or leave
  this whole surface untested in one of them.
  """
  from django.urls import path

  from agents.chat.views import ChatIndexView

  urlpatterns = [
      path("", ChatIndexView.as_view(), name="chat-index"),
  ]
  ```
- [ ] Mount it in `config/urls.py`, in the unconditional list directly after the `rag/` line:
  ```python
      path("rag/", include("tools.rag.urls")),
      # The conversation surface. UNGATED, exactly like /rag/ -- spec
      # section 8.1: a third FARABUNKER_FEATURES token would leave this
      # page untested in one of the two supported suite states
      # (docs/DEV.md:235-246).
      path("chat/", include("agents.chat.urls")),
  ```
- [ ] Add the nav entry to `foundation/templates/_shell.html`, **first** in the `<nav class="top-nav">` list:
  ```html
    <a href="{% url 'chat-index' %}" class="{% block nav_current_chat %}{% endblock %}">Chat</a>
  ```
  and update that file's own `{% comment %}` header, which enumerates the nav destinations in two places — add "Chat" to both lists and note it is ungated for the same reason Setup is (it is not behind a feature flag, so `{% url %}` always resolves).
- [ ] Write `agents/chat/tests/__init__.py` (empty) and `agents/chat/tests/_helpers.py`:
  ```python
  """Shared test helpers for `agents/chat/tests`.

  Plain importable module -- **not** a `conftest.py` (the repo forbids
  them anywhere). Each test module imports what it needs explicitly;
  autouse fixtures stay *defined* per test module but delegate their
  bodies to the functions below.

  IT IMPORTS ROW BUILDERS FROM `agents/tests/_helpers.py`, AND THAT IS A
  RULING, NOT A SLIP. P2's rule is "helpers are duplicated per APP, never
  imported across apps", with `agents/runtime/tests/_helpers.py` carved
  out as "one app, not two". `agents.chat` is a second Django app but the
  SAME COLUMN and the same top-level package directory, which is the
  boundary that rule actually polices -- one COLUMN's test scaffolding
  becoming load-bearing for another's. A sixth copy of `make_agent` /
  `make_conversation` / `make_turn` / `bind_chat_role` inside `agents/`
  would be duplication with no boundary to justify it. Nothing here
  imports from `tools/*/tests/` or `models/*/tests/`, and that rule is
  unchanged.
  """
  from __future__ import annotations

  import pytest

  from agents.tests._helpers import (           # noqa: F401 -- re-exported on purpose
      bind_chat_role, bound_chat_role, make_agent, make_conversation, make_turn,
  )


  def make_thread(**overrides):
      """A conversation with a finished USER + ASSISTANT pair -- the
      minimum a thread page has anything to render."""
      from agents.models import Turn

      conversation = overrides.pop("conversation", None) or make_conversation()
      make_turn(conversation=conversation, role=Turn.Role.USER, text="hello")
      make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                text="hi", state=Turn.State.DONE)
      return conversation
  ```
  Ship **only** what a test in this package actually calls today; later tasks add to it when they need more. A helper written before its caller is dead code with a docstring.
- [ ] Run `.venv/bin/pytest -q agents/chat` — green. Then `.venv/bin/pytest -q foundation/ops/tests/test_app_labels.py` — green with the new label.
- [ ] Run the **full gate matrix**. Green both flag states, both orders. Record the count.
- [ ] Write `agents/chat/README.md`: what `/chat/` is (a permanent chat product, not an agent builder), the URL table, the pre-auth behaviour stated in full (every turn runs as the agent's own principal; `owner_kind`/`owner_key` stay blank; the index lists every conversation on the box; the one queryset that changes when auth lands), and a note that the app owns no model.
- [ ] Commit: `feat(chat): the /chat/ app, mounted ungated, with its nav entry`.

---

### Task 4: `principal_for_request`, `ACCOUNTS_REQUIRED`, and one visibility function per kind

Ruling 4's seams, built now while there is one caller of each, so Identity & Auth is a fill-in rather than a sweep. **Accounts are OFF and nothing here authenticates anybody.** What it does is make sure there is exactly one place that answers "who is acting" and exactly one place that answers "what may they see" — because the expensive version of this phase is the one where those answers are spread across six views and a later phase has to find them all.

Three rules, and the third is the one a guard has to enforce:

1. **One request → principal point.** `agents/chat/principal.py::principal_for_request`. In open mode it returns `Principal("open", "box")` — a real principal kind (Task 1), not a `None` special case, so the audit row, `granted_tools`, and a future grants table all have a value to store.
2. **One branch point.** `settings.ACCOUNTS_REQUIRED`, env-driven, default `False`. The `True` path raises `NotImplementedError` naming the phase. That is not a stub to be embarrassed about: it is the difference between a seam and a claim, and a plan that quietly returned an open principal under `ACCOUNTS_REQUIRED = True` would ship a security hole wearing a setting's name.
3. **One visibility function per kind**, and **no module under `agents/chat` touches `Conversation`/`Agent`/`Flow` `.objects` directly except `visibility.py`.** In open mode every function returns everything, so this task changes no behaviour at all — which is exactly why the guard has to exist. A rule with no current consequence is a rule nobody notices breaking.

**Files:**
- `agents/chat/principal.py`, `agents/visibility.py` (new)
- `config/settings.py` (`ACCOUNTS_REQUIRED`)
- `agents/chat/tests/test_principal.py`, `agents/chat/tests/test_visibility.py` (new)
- `foundation/ops/tests/test_column_boundaries.py` (the direct-queryset guard)
- `docs/DEV.md`, `agents/chat/README.md`

**Interfaces (exact signatures):**

```python
# agents/chat/principal.py
def principal_for_request(request) -> Principal:
    """THE ONLY request -> principal point in the codebase."""
```

```python
# agents/visibility.py -- one function per kind, and the ONLY place
# agents/chat reaches these three managers.
def visible_conversations(principal): ...
def visible_agents(principal): ...
def visible_flows(principal): ...
def owner_fields(principal) -> dict:
    """`{"owner_kind": ..., "owner_key": ...}` for a create."""

def create_conversation(principal, agent):
    """A new `Conversation` owned by `principal`. THE create lives here
    too, so `agents/chat` reaches these managers for nothing at all."""
```

```python
# config/settings.py
ACCOUNTS_REQUIRED = os.environ.get("ACCOUNTS_REQUIRED", "0") == "1"
```

**Steps:**

- [ ] Add the setting, matching the file's own env idiom (`DEBUG = os.environ.get("DEBUG", "1") == "1"`, `config/settings.py:25`), with the comment:
  ```python
  # Identity & Auth's branch point, declared BEFORE that phase so there is
  # one place to flip rather than a sweep. `False` is this box's real
  # posture today: /chat/ is unauthenticated exactly like /rag/, /vision/,
  # /inference/ and /queue/ (spec section 14 gap 4 -- it inherits that gap
  # and does not widen it). `True` is NOT implemented and
  # `agents.chat.principal.principal_for_request` raises rather than
  # degrading to the open principal: a setting that silently did nothing
  # would be worse than no setting at all.
  ACCOUNTS_REQUIRED = os.environ.get("ACCOUNTS_REQUIRED", "0") == "1"
  ```
- [ ] Write the failing test `agents/chat/tests/test_principal.py`:
  ```python
  """`principal_for_request` -- the one request-to-principal point.

  NO FARABUNKER_FEATURES OVERRIDE (see test_mount.py).
  """
  class TestOpenMode:
      def test_it_returns_the_shared_open_principal(self, rf):
          """The SAME object `agents/contracts/tools.py` declares, not an
          equal one built here -- see the AST guard below."""
          assert principal_for_request(rf.get("/chat/")) is OPEN_PRINCIPAL

      def test_the_open_principal_is_the_same_object_shape_a_tool_call_records(self):
          """It reaches `ToolInvocation.principal_kind` unchanged, so the
          audit trail says `open` for work an unauthenticated box did --
          which is the true statement, and the one a later `user`
          principal will sit beside rather than replace."""
          assert Principal("open", "box").kind in PRINCIPAL_KINDS

  class TestAccountsRequired:
      def test_the_true_path_raises_and_names_the_phase(self, rf, settings):
          """NOT a degraded fallback. A setting whose `True` branch
          quietly returned the open principal would be a security hole
          wearing a setting's name -- the caller believes it is
          authenticating and it is not."""
          settings.ACCOUNTS_REQUIRED = True
          with pytest.raises(NotImplementedError) as exc:
              principal_for_request(rf.get("/chat/"))
          assert "Identity & Auth" in str(exc.value)
  ```
  Note: this module overrides `ACCOUNTS_REQUIRED`, **not** `FARABUNKER_FEATURES`, so the vision-flag rule does not apply — and it uses `rf` (RequestFactory) rather than the test `Client`, so it triggers no URL resolution at all.
- [ ] Write `agents/chat/principal.py`:
  ```python
  """Who is acting on this request.

  ONE function, and it is the only place in the codebase that turns an
  HTTP request into a `Principal`. Every view reaches it; none of them
  builds a principal themselves. When Identity & Auth lands, the work is
  inside THIS function -- read the session, resolve the account, mint
  `Principal("user", <id>)` -- and every caller is already correct.

  `agents.runtime.bindings.principal_for(agent)` is its sibling and its
  opposite: that one answers "who is this AGENT acting as" for a turn
  the runtime executes, this one answers "who is the PERSON in front of
  the browser". They are two different questions and they must not be
  collapsed while one of them has no real answer yet.
  """
  from __future__ import annotations

  from django.conf import settings

  # NOT constructed here. `OPEN_PRINCIPAL` is declared in
  # `agents/contracts/tools.py` beside the kind it is built from, so this
  # module and `manage.py install_defaults` hand out the SAME object
  # rather than two independently constructed answers to "who is this
  # box". The AST guard below pins that.
  from agents.contracts.tools import OPEN_PRINCIPAL, Principal


  def principal_for_request(request) -> Principal:
      """The principal acting on `request`.

      Open mode -- this box's real posture today -- is one principal for
      the whole machine. `/chat/` is unauthenticated exactly like every
      other surface on it (spec section 14 gap 4); it inherits that gap
      and does not widen it.
      """
      if settings.ACCOUNTS_REQUIRED:
          raise NotImplementedError(
              "ACCOUNTS_REQUIRED=1 needs the Identity & Auth phase, which is not "
              "built. Set it to 0, or build that phase -- this function must never "
              "answer with the open principal while it is on."
          )
      return OPEN_PRINCIPAL
  ```
- [ ] Add the principal-construction guard to `foundation/ops/tests/test_import_law.py`. **Three production files may construct one**; tests are exempt and are filtered out by the same `_is_test_file` helper the neighbouring sweeps use:
  ```python
  # Where a `Principal(...)` may be CONSTRUCTED. Everywhere else asks one
  # of these for it. THREE files, each answering a different question
  # (tests are exempt and swept out separately):
  _PRINCIPAL_CONSTRUCTORS = frozenset({
      "agents/contracts/tools.py",        # the type and OPEN_PRINCIPAL itself
      "agents/chat/principal.py",         # who is on this REQUEST
      "agents/runtime/bindings.py",       # who is this AGENT acting as
  })


  def test_a_principal_is_only_constructed_in_the_three_files_that_may():
      """RULING 4a, enforced rather than described.

      A principal's KIND is the field a future grants table joins on, so
      a fifth place minting one is a fifth opinion about who is acting --
      and in open mode every one of them would look correct, because
      every one of them would say "open". The failure only surfaces once
      accounts exist and one call site is still hardcoding a kind, which
      is far too late.

      AST, over `git ls-files -- agents`, skipping test files (tests
      construct principals freely and that is fine -- they are not the
      thing a later phase has to audit). `ast.Call` with
      `func=Name(id="Principal")`, which catches it in a function body as
      readily as at module scope.
      """
  ```
  plus the anti-vacuous half: a synthetic `Principal("open", "x")` source really is flagged by the same walk, and the exclusion set is asserted non-empty and to name files that exist.
- [ ] Write the failing test `agents/chat/tests/test_visibility.py`: in open mode each function returns every row of its kind including rows owned by somebody else; `owner_fields(Principal("open", "box"))` returns `{"owner_kind": "open", "owner_key": "box"}`; and an **anti-vacuous pin** that the functions really query their own model (create two rows, assert both come back) rather than returning a constant.
- [ ] Write `agents/visibility.py`:
  ```python
  """What one principal may see, per kind, in one place.

  THREE FUNCTIONS AND ONE RULE: every list, detail, POST, and delete under
  `agents/chat` reaches its rows through one of these. In open mode they
  return everything, so this module changes NO behaviour today -- which is
  precisely why `foundation/ops/tests/test_column_boundaries.py` grows a
  guard that no other module under `agents/chat` touches `Conversation`,
  `Agent`, or `Flow` `.objects` directly. A rule with no current
  consequence is a rule nobody notices breaking, and the whole value of
  writing it now is that Identity & Auth edits three functions instead of
  auditing a package.

  It lives at the COLUMN root, not inside `agents/chat`, for the same
  reason `agents/models.py` does: a future MCP edge and a future
  management command need the same answer, and neither of them is a chat
  view.

  The 2026-08-27 addendum's consequence 2 is the shape being copied here:
  retrieval keeps exactly ONE filter point, and a visibility scope is one
  more filter argument THERE rather than a second copy in each caller.
  This is that discipline applied to the agent column's own three tables.
  """
  from __future__ import annotations

  from agents.models import Agent, Conversation, Flow


  def visible_conversations(principal):
      """Every conversation this principal may read.

      Open mode: ALL of them, and that is the honest pre-auth behaviour
      rather than an oversight -- there are no users on this box, so
      there is nobody for a conversation to be hidden from. THIS
      FUNCTION IS WHERE `owner_kind`/`owner_key` GROW A FILTER when
      accounts arrive; not a migration, not a second listing view, not a
      template.
      """
      return Conversation.objects.select_related("agent").all()


  def visible_agents(principal):
      """Every ENABLED agent this principal may run.

      `enabled=False` is not a visibility rule and is applied here
      anyway, deliberately: a disabled agent is one nobody may start a
      conversation with, which is the same question this function
      answers, and a caller that had to remember the second filter would
      one day forget it.
      """
      return Agent.objects.filter(enabled=True)


  def visible_flows(principal):
      """Every ENABLED flow this principal may run.

      ASKED WITH THE ACTING PRINCIPAL, NOT THE VIEWER (ruling, 2026-08-28).
      Its three callers are all in the RUNTIME, not on a page:
      `narrowed_flow_spec` filling `flow.run`'s choices for a turn,
      `flow_row_roles` walking their steps' roles at plan time, and
      `run_flow` loading the row a model named. Each is handed
      `ctx.principal` -- the principal the TURN is running as, which is
      `principal_for(agent)`: the conversation's agent for a root turn,
      and the DELEGATE's own principal inside an `agent.<slug>` call
      (`agents/runtime/delegate.py` builds a fresh `ToolContext`). So a
      delegate sees the flows IT may run, not the flows its caller may.
      That is the right answer and it is the one a grants table will
      want, but it is worth stating because it is not the obvious one.

      CONSEQUENCE, DOCUMENTED RATHER THAN DISCOVERED: flipping
      `ACCOUNTS_REQUIRED` changes what an AGENT can run, not merely what
      a page lists. The runtime is inside this filter point, deliberately
      -- a visibility rule that stopped at the view would be a rule an
      agent could walk around by being asked nicely.
      """
      return Flow.objects.filter(enabled=True)


  def owner_fields(principal) -> dict:
      """The two columns to stamp on a row this principal is creating.

      A dict rather than two arguments so a call site cannot pass one and
      forget the other, and so a third owner column later is a change
      here rather than at every create.
      """
      return {"owner_kind": principal.kind, "owner_key": principal.key}


  def create_conversation(principal, agent):
      """A new conversation owned by `principal`.

      RULING (2026-08-28): the CREATE lives here too, not in the view.
      The earlier draft kept `Conversation.objects.create(...)` in
      `conversations.py` and carved a hole in the guard for it -- and a
      guard with an exception is a guard somebody will widen. Keeping it
      here makes the rule flat and checkable in one sentence: NO module
      under `agents/chat` touches these three managers, for any reason.

      It also puts the ownership stamp where ownership is decided. A
      view that could create a row could create one without
      `owner_fields`, and that row would be invisible to every filter
      Identity & Auth later adds -- the exact failure these columns
      exist to prevent.
      """
      return Conversation.objects.create(agent=agent, **owner_fields(principal))
  ```
- [ ] Add the guard to `foundation/ops/tests/test_column_boundaries.py`:
  ```python
  _VISIBILITY_MODELS = ("Conversation", "Agent", "Flow")


  def test_no_chat_module_queries_the_three_owned_models_directly():
      """RULING 4c. `agents/visibility.py` is the ONE place `agents/chat`
      reaches `Conversation`, `Agent`, or `Flow` rows.

      In open mode those functions return everything, so nothing about
      today's behaviour depends on this -- and that is the whole reason
      the rule needs a guard rather than a convention. When Identity &
      Auth adds a filter, it adds it in three functions; a view that had
      grown its own `Agent.objects.filter(...)` would silently keep
      showing everybody everything, and would do it on the one page
      where that matters most.

      AST, not a substring scan: `Attribute(value=Name(id=<Model>),
      attr="objects")` catches `Agent.objects` wherever it appears, in a
      function body as readily as at module scope, and does not trip over
      the word "objects" in a docstring.
      """
  ```
  The exclusion is **flat and per-module**: `agents/visibility.py`, and nothing else. No carve-out for creates, no carve-out for "just this one query" — `visibility.create_conversation` exists precisely so the rule needs none, and a guard with an exception is a guard somebody widens. Plus the anti-vacuous half: a synthetic `Agent.objects.filter(x=1)` source really is flagged by the same walk, and the exclusion set is asserted to name exactly one file, which exists.
- [ ] Run `.venv/bin/pytest -q agents/chat foundation/ops` — green.
- [ ] Run the **full gate matrix**. Record the count.
- [ ] Update `agents/chat/README.md` (the three seams and what each will become) and `docs/DEV.md` (a short **Accounts are off** note: what `ACCOUNTS_REQUIRED` is, that `1` raises today, and which phase implements it).
- [ ] Commit: `feat(agents): one request-to-principal point, one visibility function per kind, accounts off`.

---

### Task 5: the shipped-defaults catalogue, `install_default`, and `manage.py install_defaults`

Ruling 2, and it is the ruling that changes how this platform treats the operator's database. **No code path writes a row the operator did not ask for.** P2's `manage.py sync_agents` created three agents on every deploy; ruling 2 retires it. What ships instead is a **catalogue** — code-declared `AgentSpec`s and `FlowSpec`s that describe what the platform *offers* — plus one idempotent, create-if-absent installer that a person triggers, either from a button on a page or from a command.

Three properties, and each rules out a shortcut somebody will otherwise take:

- **Create-if-absent, never overwrite.** `install_default` returns the existing row untouched when one is there. It is safe to call twice, safe to call from a button somebody double-clicks, and it can never silently revert an edit the operator made. `--reset <slug>` is the ONLY path that rewrites an existing row, and it names the slug explicitly.
- **The catalogue is not a registry.** A `FlowSpec` is used for exactly two things: rendering the "Add the default X" offer, and validating row JSON (`validate_flow_json`, called by `Flow.save()`). **Nothing resolves a flow through it at run time** — `flow.run` loads the row (Task 12). A catalogue that could also be executed would be a second source of truth for what a flow is, and the row would lose.
- **The principal that installs, owns.** `install_default(kind, slug, principal)` stamps `owner_kind`/`owner_key` from the caller (ruling 4b), so a default adopted from the chat page is owned by whoever adopted it. In open mode that is `Principal("open", "box")`, which is the true statement about a box with no accounts.

**Files:**
- `agents/defaults.py` (new — the catalogue, the JSON validator, `install_default`)
- `agents/resident.py` (`sync_resident_agents` removed; `RESIDENT_AGENTS` moves to `agents/defaults.py`)
- `agents/management/commands/install_defaults.py` (new), `agents/management/commands/sync_agents.py` (deleted)
- `agents/tests/test_defaults.py` (new), `agents/tests/test_resident.py` (rewritten)
- `docs/DEV.md`, `agents/README.md`

**Interfaces (exact signatures):**

```python
# agents/defaults.py -- PURE DATA plus ONE row writer. No Django import at
# module scope; `install_default` takes the model classes it needs the same
# way `sync_resident_agents` took `AgentModel`.
class FlowDeclarationError(ValueError): ...

@dataclass(frozen=True)
class FlowInput:
    key: str; label: str; description: str = ""; required: bool = False

@dataclass(frozen=True)
class FlowStep:
    tool: str; args: dict = field(default_factory=dict)

@dataclass(frozen=True)
class FlowSpec:
    slug: str; name: str; description: str
    inputs: tuple[FlowInput, ...]; steps: tuple[FlowStep, ...]
    def __post_init__(self) -> None: ...          # calls validate_flow_json

DEFAULT_AGENTS: tuple[AgentSpec, ...]             # moved from agents/resident.py
DEFAULT_FLOWS: tuple[FlowSpec, ...]

def validate_flow_json(inputs: list, steps: list, *, label: str = "this flow") -> None:
    """Shape-check a flow's JSON. The ONE validator, called by
    `FlowSpec.__post_init__` AND by `Flow.save()`."""

def catalogue(kind: str) -> tuple: ...
def default_for(kind: str, slug: str): ...
def install_default(kind: str, slug: str, principal, *, reset: bool = False) -> tuple:
    """`(row, created)`. Create-if-absent and idempotent; `reset=True` is
    the ONLY path that rewrites an existing row."""
def missing_defaults(kind: str, installed_slugs) -> tuple: ...
```

**Steps:**

- [ ] Write the failing test `agents/tests/test_defaults.py`. Cover:

  **`TestValidateFlowJson`** — the rules move here verbatim from the retired declaration-time validator, and are now shared with `Flow.save()`: at least one step; no step naming an `agent.*` or `flow.*` key (**the recursion guard, closed by declaration**: a flow calling an agent that could call the flow is a cycle the shared budget would stop only after spending it); every `$` reference syntactically well-formed and pointing **backwards** (§7.4: "resolvable against the steps that precede it"); `$input.<key>` naming a declared input; a reference field in `text`/`data`/`artifacts`; a reference nested inside a list or dict validated too; and the anti-vacuous pin that `"$5.00"` is a **value**, not a reference — a validator that refused it would make a whole class of legitimate argument undeclarable.

  **`TestTheSameValidatorGuardsRowsAndCatalogue`** — `Flow(steps=[{"tool": "agent.library"}]).save()` raises, and the message is the same one `FlowSpec` raises for the same JSON. *One validator, or a row and a shipped default become two different things wearing one name.*

  **`TestInstallDefault`** — creates the row and returns `created=True`; a second call returns `created=False` and the row is **byte-identical** (assert `updated_at` is unchanged, which is what makes "never overwrites" a fact rather than a hope); an operator edit survives a re-install; `reset=True` restores the shipped text **and** logs; the row is stamped with the installing principal; an unknown slug raises naming what the catalogue holds; `resident=True` is set on install and is an **origin marker only** (ruling 3 — assert the row is editable afterwards).

  **`TestPurity`** — the same AST walk `agents/tests/test_resident.py` runs over `agents/resident.py`, pointed at `agents/defaults.py`: **no `django` import at MODULE scope** (`install_default` imports Django inside its own body, which is what lets `Flow.save()` and `AppConfig`-adjacent code import the declarations without paying for the app registry), and **no `tools.*` import anywhere** (import-law rule 3, walked over the whole tree so a lazy in-body import is caught too). Plus the anti-vacuous half: a synthetic module-scope `from django.db import models` really is flagged.

  **`TestTheCatalogueIsNotARegistry`** — an anti-vacuous structural pin: `agents/defaults.py` contains no `register_tool` call and `agents/apps.py::ready()` does not import it. *The catalogue describes offers; the registry holds tools; conflating them is how a shipped default becomes un-removable.*

  **`TestNothingWritesRowsByItself`** — the guard ruling 2 exists for: `install_default` is called from exactly two places in the tree (`agents/chat/views/defaults.py` and `agents/management/commands/install_defaults.py`), found by an AST sweep over `git ls-files -- agents`, and `agents/apps.py` calls it never. **A ruling with no guard is a preference.**

- [ ] Run them; read the failures.
- [ ] Write `agents/defaults.py`. Move `AgentSpec` and `RESIDENT_AGENTS` here from `agents/resident.py` (renamed `DEFAULT_AGENTS`), add the flow types, the validator, and:
  ```python
  def install_default(kind: str, slug: str, principal, *, reset: bool = False):
      """Create the shipped default `slug` if it is absent. Returns
      `(row, created)`.

      CREATE-IF-ABSENT, AND THAT IS THE WHOLE CONTRACT (ruling 2). An
      existing row is returned UNTOUCHED -- not refreshed, not merged,
      not "updated where it differs". Safe to call twice, safe behind a
      button somebody double-clicks, and incapable of reverting an edit
      the operator made. P2's `sync_resident_agents` did the opposite
      (it re-applied the code declaration on every deploy) and that is
      precisely what ruling 2 retires: nothing writes the operator's
      database because a deploy happened.

      `reset=True` is the ONE path that rewrites an existing row. It
      names a single slug, it is reached only from an explicit
      `install_defaults --reset <slug>`, and it logs what it replaced --
      because it is the only operation here that can destroy work.

      THE INSTALLING PRINCIPAL OWNS THE ROW (ruling 4b). In open mode
      that is `Principal("open", "box")`, which is the true statement
      about a box with no accounts -- not a placeholder to be filled in
      later, but the correct answer for this posture.

      Takes no model class arguments and imports Django INSIDE its body,
      so the module's declarations stay importable by anything.
      """
  ```
- [ ] Reduce `agents/resident.py` to `agent_tool_specs()` and `AGENT_TOOL_PREFIX` — the two things `apps.py::ready()` genuinely needs — importing `DEFAULT_AGENTS` from `agents/defaults.py`. **Delete `sync_resident_agents`.** Its docstring's "a slug that has disappeared from `RESIDENT_AGENTS` is DISABLED" behaviour goes with it: under ruling 2 a row the operator installed is theirs, and a deploy that removed a shipped default must not reach in and disable their copy of it.
- [ ] Write `agents/management/commands/install_defaults.py` and **delete `sync_agents.py`**:
  ```python
  """`python manage.py install_defaults [--reset <slug>]` -- the CLI
  equivalent of the "Add the default X" button.

  THE EXPLICIT PATH, not an automatic one (ruling 2). It replaces
  `manage.py sync_agents`, which created three agents on every deploy
  that ran it. Nothing about a deploy now writes the operator's rows:
  this command exists so a person who prefers a shell to a button has
  one, and it does exactly what the button does.

  Idempotent by construction -- it calls `install_default`, which is
  create-if-absent -- so running it twice is a no-op and running it after
  an operator edited a row leaves the edit alone. `--reset <slug>` is the
  one destructive option and it names its target.

  Runs as `OPEN_PRINCIPAL`, which it IMPORTS, NEVER CONSTRUCTS
  (`from agents.contracts.tools import OPEN_PRINCIPAL`) -- the same
  object `agents.chat.principal.principal_for_request` hands out, so a
  row installed from the shell and one installed from the page are owned
  identically because they were installed by the same actor, not by two
  actors that happen to match. Task 4's AST guard is what keeps that
  true: this module is not in `_PRINCIPAL_CONSTRUCTORS`.
  """
  ```
  Add the import explicitly: `from agents.contracts.tools import OPEN_PRINCIPAL`, and pass it to `install_default` as the `principal`.
  Report per kind: created, already present, and (with `--reset`) what was replaced.
- [ ] **Sweep every reference to `sync_agents`.** `grep -rn "sync_agents" --include='*.py' --include='*.md' .` — `docs/DEV.md:294-304`'s "A resident-agent change also needs `sync_agents`" subsection, `agents/README.md`, `docs/ROADMAP.md`, `agents/runtime/README.md`, `agents/management/commands/agent_turn.py`'s no-agent `CommandError` copy, and P2's own test modules. **The name must not survive anywhere.** Rewrite the DEV.md subsection completely: **a code change to the catalogue changes what is OFFERED, not what is installed** — an operator who already installed `general` keeps their `general`, and a new shipped default appears on the page as an offer with an Add button. That is a genuinely different operational story from P2's and the docs must tell the new one, not a patched version of the old.
- [ ] Run `.venv/bin/pytest -q agents` — green.
- [ ] Run the **full gate matrix**. Record the count.
- [ ] Update `agents/README.md` (the catalogue vs. the rows; the two installers; `--reset`) and `docs/ROADMAP.md`'s deploy note.
- [ ] Commit: `feat(agents): shipped defaults are a catalogue an operator installs, never a deploy that writes rows`.

---

### Task 6: the conversation list, the offers, and starting a thread

The index becomes real: every conversation on the box, and a "new conversation" box with an agent picker, a model picker, and a message field. Starting a conversation with a message posts the first turn through the same code path every later turn uses — there is exactly one turn-starting function in this app, and Task 9 writes it. Until then, `conversation_start` creates the thread and redirects; Task 9 adds the "and post the first turn" half.

**Files:**
- `agents/chat/views/conversations.py` (`ChatIndexView` fleshed out, `conversation_start`)
- `agents/chat/views/__init__.py`, `agents/chat/urls.py`
- `agents/chat/views/defaults.py` (new — the "Add the default X" POST)
- `agents/chat/templates/chat/index.html`, `_picker.html`, `_offers.html` (new)
- `agents/chat/tests/test_index.py` (new — it carries `TestInstalledAgentsAndOffers` and `TestInstallingADefault` too: the offers list and the Add button are the index's own behaviour, and splitting them across two modules would put the two halves of one page's contract in two files)
- `agents/chat/README.md`

**Ruling 2's page half.** The index declares its resident dependencies and, when they are missing, says so honestly and offers to install them — it never installs anything by looking at it. A GET writes no row, ever. The offer is a **CSRF-protected POST button inside a form**, so it works with JavaScript off like everything else on this surface, and it lands on `install_default(kind, slug, principal)` (Task 5), which is create-if-absent and idempotent — a double-click is a no-op, not a duplicate.

**The picker shows two lists, and the distinction is the product.** *Installed* agents are rows the operator owns and can start a conversation with. *Available* ones are shipped defaults not yet installed, each with an Add button. A page that merged them would have to either hide what the platform offers or pretend an offer is a thing you can talk to.

**Every row read goes through `agents/visibility.py`** (ruling 4c). In open mode those functions return everything, so nothing about today's behaviour depends on it — which is exactly why Task 4's guard exists.

**Interfaces (exact signatures):**

```python
# agents/chat/views/conversations.py
CONVERSATION_LIST_LIMIT = 100

class ChatIndexView(TemplateView):
    template_name = "chat/index.html"
    def get_context_data(self, **kwargs) -> dict: ...

@require_POST
def conversation_start(request): ...
```

```python
# agents/chat/views/defaults.py
@require_POST
def default_install(request):
    """POST /chat/defaults/install/ -- adopt one shipped default.
    Body: `kind` in {"agent", "flow"}, `slug`. Redirects back."""
```

```python
# agents/chat/pickers.py  (new -- the ONE place models.registry is reached)
def chat_picker_options(selected: str = "") -> list[dict] | None:
    """`models.registry.bindings.picker_options` for the chat capability
    and the `chat.converse` role. Never raises."""
```

**Steps:**

- [ ] Write the failing test `agents/chat/tests/test_index.py`:
  ```python
  """The conversation list and starting a thread.

  NO FARABUNKER_FEATURES OVERRIDE (see test_mount.py's docstring).
  """
  from __future__ import annotations

  import pytest
  from django.urls import reverse

  from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
      bound_chat_role, make_agent, make_conversation,
  )
  from agents.contracts.tools import OPEN_PRINCIPAL
  from agents.models import Agent, Conversation

  pytestmark = pytest.mark.django_db


  def _offers_section(body: str) -> str:
      """Just the "not installed yet" block, so a test asserting a slug
      is ABSENT from the offers cannot pass because the slug is also
      absent from the whole page -- or fail because it is present in the
      picker, which is where an installed default is supposed to be."""
      start = body.index('id="offers"')
      return body[start:body.index("</section>", start)]


  class TestTheList:
      def test_it_lists_every_conversation_on_the_box(self, client):
          """PRE-AUTH BEHAVIOUR, stated as a test so it is a decision
          rather than an accident: there are no users yet, so there is no
          owner filter. `owner_kind`/`owner_key` exist and stay blank;
          when Identity & Auth lands this queryset grows a filter."""
          agent = make_agent(slug="general")
          mine = make_conversation(agent=agent, title="mine")
          theirs = make_conversation(agent=agent, title="theirs",
                                     owner_kind="user", owner_key="someone")

          body = client.get(reverse("chat-index")).content.decode()

          assert "mine" in body and "theirs" in body

      def test_a_started_conversation_records_the_acting_principal(self, client):
          """RULING 4b. In open mode that is `Principal("open", "box")` --
          the TRUE statement about a box with no accounts, not a
          placeholder. P3's first draft wrote blanks here; the ruling
          replaces that: a row with no owner is a row a later filter
          cannot reason about, and backfilling one is a migration nobody
          has the information to write."""
          make_agent(slug="general")
          client.post(reverse("chat-start"), {"agent": "general"})
          conversation = Conversation.objects.get()
          assert (conversation.owner_kind, conversation.owner_key) == ("open", "box")

      def test_the_list_is_read_through_visibility(self, monkeypatch, client):
          """RULING 4c, pinned behaviourally as well as structurally
          (Task 4's AST guard is the other half): if the view stopped
          calling `visible_conversations`, this fails."""
          calls = []
          import agents.chat.views.conversations as module

          real = module.visible_conversations
          monkeypatch.setattr(module, "visible_conversations",
                              lambda p: calls.append(p) or real(p))
          client.get(reverse("chat-index"))
          assert calls == [OPEN_PRINCIPAL]

      def test_newest_first(self, client):
          agent = make_agent(slug="general")
          older = make_conversation(agent=agent, title="older")
          newer = make_conversation(agent=agent, title="newer")
          body = client.get(reverse("chat-index")).content.decode()
          assert body.index("newer") < body.index("older")

      def test_an_empty_box_says_so_rather_than_rendering_nothing(self, client):
          body = client.get(reverse("chat-index")).content.decode()
          assert "No conversations yet" in body


  class TestInstalledAgentsAndOffers:
      def test_only_enabled_installed_agents_can_be_talked_to(self, client):
          make_agent(slug="general", name="General assistant")
          make_agent(slug="retired", name="Retired agent", enabled=False)
          body = client.get(reverse("chat-index")).content.decode()
          assert "General assistant" in body
          assert "Retired agent" not in body

      def test_a_fresh_box_offers_the_shipped_defaults_and_writes_nothing(
          self, client
      ):
          """RULING 2, and the sentence that matters: a GET installs
          NOTHING. A fresh box shows an honest empty state naming what
          the platform offers, each with an Add button -- it does not
          quietly create three agents because somebody opened a page.
          """
          response = client.get(reverse("chat-index"))
          assert response.status_code == 200
          body = response.content.decode()
          assert "Add the default" in body
          assert "General assistant" in body          # offered, by name
          assert Agent.objects.count() == 0           # and NOT installed

      def test_an_installed_default_stops_being_offered(self, client):
          """The two lists are disjoint by construction
          (`missing_defaults(kind, installed_slugs)`), so a default can
          never appear as both a thing you can talk to and a thing you
          can add."""
          body = client.get(reverse("chat-index")).content.decode()
          assert body.count("Add the default") >= 1
          client.post(reverse("chat-default-install"),
                      {"kind": "agent", "slug": "general"})
          body = client.get(reverse("chat-index")).content.decode()
          assert "general" not in _offers_section(body)

      def test_the_empty_state_names_the_command_too(self, client):
          """The button is the primary path; the CLI equivalent is named
          for an operator who prefers a shell. `sync_agents` is GONE
          (ruling 2) and must not be named anywhere."""
          body = client.get(reverse("chat-index")).content.decode()
          assert "install_defaults" in body
          assert "sync_agents" not in body


  class TestInstallingADefault:
      def test_the_button_installs_exactly_one_row_and_redirects(self, client):
          response = client.post(reverse("chat-default-install"),
                                 {"kind": "agent", "slug": "general"})
          assert response.status_code == 302
          assert Agent.objects.filter(slug="general").count() == 1

      def test_it_is_owned_by_the_installing_principal(self, client):
          client.post(reverse("chat-default-install"),
                      {"kind": "agent", "slug": "general"})
          agent = Agent.objects.get(slug="general")
          assert (agent.owner_kind, agent.owner_key) == ("open", "box")
          assert agent.resident is True      # origin marker (ruling 3)

      def test_a_double_click_installs_one_row_not_two(self, client):
          """`install_default` is create-if-absent, so the button is safe
          to press twice -- which people do."""
          for _ in range(2):
              client.post(reverse("chat-default-install"),
                          {"kind": "agent", "slug": "general"})
          assert Agent.objects.filter(slug="general").count() == 1

      def test_it_never_overwrites_an_edited_row(self, client):
          """The property ruling 2 exists for. P2's `sync_agents` would
          have reverted this on the next deploy."""
          client.post(reverse("chat-default-install"),
                      {"kind": "agent", "slug": "general"})
          agent = Agent.objects.get(slug="general")
          agent.system_prompt = "my own words"
          agent.save()
          client.post(reverse("chat-default-install"),
                      {"kind": "agent", "slug": "general"})
          agent.refresh_from_db()
          assert agent.system_prompt == "my own words"

      def test_an_unknown_kind_or_slug_is_a_400_and_writes_nothing(self, client):
          assert client.post(reverse("chat-default-install"),
                             {"kind": "agent", "slug": "nope"}).status_code == 400
          assert client.post(reverse("chat-default-install"),
                             {"kind": "nonsense", "slug": "general"}).status_code == 400
          assert Agent.objects.count() == 0

      def test_get_is_not_allowed(self, client):
          """A GET must never install. `@require_POST` is the guard, and
          this is the test that says so out loud."""
          assert client.get(reverse("chat-default-install")).status_code == 405

      def test_the_form_carries_a_csrf_token_so_it_works_with_js_off(self, client):
          body = client.get(reverse("chat-index")).content.decode()
          assert "csrfmiddlewaretoken" in body
          assert f'action="{reverse("chat-default-install")}"' in body


  class TestStarting:
      def test_it_creates_the_thread_and_redirects_to_it(self, client):
          make_agent(slug="general")
          response = client.post(reverse("chat-start"), {"agent": "general"})
          conversation = Conversation.objects.get()
          assert response.status_code == 302
          assert response["Location"] == reverse(
              "chat-conversation", args=[conversation.id]
          )

      def test_an_unknown_agent_is_refused_without_creating_anything(self, client):
          response = client.post(reverse("chat-start"), {"agent": "nope"})
          assert response.status_code == 400
          assert Conversation.objects.count() == 0

      def test_a_disabled_agent_is_refused(self, client):
          """A disabled agent is one the operator turned off. Its past
          conversations stay readable; new ones do not start.
          `visible_agents` applies `enabled=True` for exactly this
          reason, so the picker and this refusal cannot disagree."""
          make_agent(slug="retired", enabled=False)
          response = client.post(reverse("chat-start"), {"agent": "retired"})
          assert response.status_code == 400
          assert Conversation.objects.count() == 0

      def test_get_is_not_allowed(self, client):
          assert client.get(reverse("chat-start")).status_code == 405

      def test_the_picked_connection_is_carried_into_the_thread_url(self, client):
          """Deviation P3-D7: the pick lives in the query string, not in
          a session and not in a column -- `tools/vision/views.py::
          _create_url`'s own rule, so a link is a complete description of
          what the page will show."""
          make_agent(slug="general")
          response = client.post(reverse("chat-start"),
                                 {"agent": "general", "connection": "3"})
          assert response["Location"].endswith("?connection=3")
  ```
- [ ] Run it. Read the failures: `NoReverseMatch` for `chat-start`, then assertion failures on the empty template.
- [ ] Write `agents/chat/pickers.py`:
  ```python
  """The per-turn model picker's options.

  The ONE module in `agents/chat` that imports `models.registry` at all,
  and it imports exactly `bindings` -- import-law rule 2's single
  sanctioned exception, pinned repo-wide by `foundation/ops/tests/
  test_import_law.py::test_agents_reaches_models_registry_through_
  bindings_and_nothing_else`, which walks every tracked file under
  `agents/`.

  `picker_options` is the SAME function `/rag/` and `/vision/` already
  build their choosers from (`models/registry/bindings.py:233`), lifted
  there precisely so a third page would not grow a third near-identical
  copy of the loop. This module adds the chat capability/role pair and
  the never-raises wrapper, and nothing else.
  """
  from __future__ import annotations

  import logging

  from models.contracts.roles import CHAT_CONVERSE_ROLE
  from models.registry.bindings import picker_options

  logger = logging.getLogger(__name__)


  def chat_picker_options(selected: str = "") -> list[dict] | None:
      """The chooser's options, or `None` when there is nothing to pick
      from (no chat-capable connection and no environment override) --
      `picker_options`' own contract, passed through unchanged.

      NEVER RAISES. This is chrome on a page whose real subject is the
      conversation; a registry read that failed must cost the operator a
      picker, never the thread they came to read.
      """
      try:
          return picker_options("chat", CHAT_CONVERSE_ROLE, selected)
      except Exception:  # noqa: BLE001 -- log detail, then degrade to no picker
          logger.exception("chat: could not build the model picker's options")
          return None
  ```
- [ ] Write `ChatIndexView` and `conversation_start` in `agents/chat/views/conversations.py`:
  ```python
  """The conversation list and its lifecycle."""
  from __future__ import annotations

  import logging

  from django.http import HttpResponseBadRequest
  from django.shortcuts import redirect
  from django.urls import reverse
  from django.views.decorators.http import require_POST
  from django.views.generic import TemplateView

  from agents.chat.pickers import chat_picker_options
  from agents.chat.principal import principal_for_request
  # THE ONE thread-URL builder. It lives in `service.py` rather than here
  # because `turns.py` needs it too and must not import this module --
  # the package's import direction is one-way (`views/__init__.py`).
  from agents.chat.service import conversation_url
  # NO `from agents.models import ...` HERE (ruling 4c). Every row this
  # module reads comes through `agents/visibility.py`, and
  # `foundation/ops/tests/test_column_boundaries.py` fails the build if a
  # module under `agents/chat` reaches `Conversation`/`Agent`/`Flow`
  # `.objects` directly. In open mode those functions return everything,
  # so nothing today depends on this -- which is exactly why it needs a
  # guard rather than a convention.
  from agents.defaults import missing_defaults
  from agents.visibility import create_conversation, visible_agents, visible_conversations

  logger = logging.getLogger(__name__)

  # How many threads the index lists. A cap rather than a paginator: this
  # is a single-operator box, the rows are tiny, and a paginator on a
  # list nobody has yet filled is UI nobody asked for. When it needs one
  # it gets one; until then the number is honest and visible.
  CONVERSATION_LIST_LIMIT = 100

  _NOTHING_INSTALLED = (
      "No agents are installed on this box yet. Add one of the defaults below, or "
      "run `manage.py install_defaults`."
  )


  class ChatIndexView(TemplateView):
      """GET /chat/ -- every conversation this principal may see, the
      agents they can talk to, and the shipped defaults they have not
      installed yet.

      A GET INSTALLS NOTHING (ruling 2). A box with no rows renders an
      honest empty state naming what the platform offers, each with an
      "Add the default X" button -- it does not quietly create three
      agents because somebody opened a page. P2's `manage.py sync_agents`
      did create them, on every deploy that ran it, and that is what this
      ruling retires.

      TWO LISTS, AND THE DISTINCTION IS THE PRODUCT. `agents` are rows
      the operator owns and can start a conversation with; `offers` are
      shipped defaults not yet installed. Merging them would mean either
      hiding what the platform offers or pretending an offer is something
      you can talk to. They are disjoint by construction --
      `missing_defaults` is computed FROM the installed slugs.

      Every row is read through `agents/visibility.py` (ruling 4c). In
      open mode `visible_conversations` returns them all, which is the
      honest pre-auth behaviour: there are no users on this box (spec
      section 14 gap 4), so there is nobody for a conversation to be
      hidden from. WHEN IDENTITY & AUTH LANDS, THAT FUNCTION IS WHERE
      THE FILTER GOES -- not a migration, not a second listing view, not
      this class.

      Never 500s. A registry read that fails costs the operator the model
      picker, never the list of what they have already said.
      """

      template_name = "chat/index.html"

      def get_context_data(self, **kwargs) -> dict:
          context = super().get_context_data(**kwargs)
          selected = self.request.GET.get("connection", "")
          principal = principal_for_request(self.request)
          conversations = list(
              visible_conversations(principal)[:CONVERSATION_LIST_LIMIT]
          )
          agents = list(visible_agents(principal))
          context.update(
              conversations=conversations,
              agents=agents,
              # Shipped defaults this box has not adopted. Computed from
              # what IS installed, so the two lists cannot overlap.
              offers=missing_defaults("agent", [a.slug for a in agents]),
              nothing_installed=_NOTHING_INSTALLED if not agents else "",
              picker=chat_picker_options(selected),
              selected_connection=selected,
              setup_url=reverse("inference-console"),
          )
          return context


  def _startable_agent(request, slug: str):
      """The agent named by `slug` that this principal may start a
      conversation with, or `None`.

      Through `visible_agents`, not a direct query (ruling 4c), so the
      picker and this lookup can never disagree about which agents exist
      -- including about `enabled`, which that function applies.
      Case-insensitive, matching `Agent`'s own `uniq_agent_slug_ci`
      constraint and `manage.py agent_turn`'s lookup.
      """
      principal = principal_for_request(request)
      return visible_agents(principal).filter(slug__iexact=(slug or "").strip()).first()


  @require_POST
  def conversation_start(request):
      """POST /chat/start/ -- open a new thread with one agent.

      RECORDS THE ACTING PRINCIPAL (ruling 4b), through
      `agents.visibility.create_conversation` -- which is also the only
      way this module may reach that manager at all (ruling 4c). In open
      mode the owner is `OPEN_PRINCIPAL`, the true statement about a box
      with no accounts rather than a placeholder. A row written with
      blank owner columns is a row a later filter cannot reason about,
      and backfilling one is a migration nobody has the information to
      write.

      The title is set from the first user message when there is one
      (Task 9) and is NEVER generated by a model -- that would be a
      second, invisible model call per conversation (spec section 7.2).

      A bad agent is a 400 and writes nothing. It is a caller error: the
      slug came from a `<select>` this page rendered from live rows, so
      an unknown one means the agent was disabled or removed between the
      render and the submit, and starting a thread with a different agent
      would be worse than saying no.
      """
      agent = _startable_agent(request, request.POST.get("agent"))
      if agent is None:
          return HttpResponseBadRequest(
              "Pick an agent that exists and is enabled. Add one of the defaults on "
              "the chat page, or run `manage.py install_defaults`, if this box has "
              "no agents yet."
          )
      conversation = create_conversation(principal_for_request(request), agent)
      return redirect(
          conversation_url(conversation, connection=request.POST.get("connection", ""))
      )
  ```
  **This module makes no direct manager call at all** — reads go through `visible_*`, the one create goes through `visibility.create_conversation`, and Task 4's guard is therefore a flat per-module exclusion naming `agents/visibility.py` and nothing else. An earlier draft kept the create here behind a carve-out; a guard with an exception is a guard somebody widens, and the ownership stamp belongs where ownership is decided.
- [ ] Write `agents/chat/views/defaults.py`:
  ```python
  """Adopting a shipped default -- the page half of ruling 2.

  ONE POST, and it is the only way a row appears on this box short of the
  command that does the same thing. Not a GET: a page load must never
  write, and `@require_POST` plus a real `<form>` is what makes that
  true for a link somebody bookmarks, a prefetcher, and a crawler alike.

  Idempotent because `install_default` is create-if-absent -- a
  double-clicked button installs one row, and an already-installed
  default is a no-op rather than an error, because the operator asked for
  a state and that state is what they get.
  """
  from __future__ import annotations

  from django.http import HttpResponseBadRequest
  from django.shortcuts import redirect
  from django.urls import reverse
  from django.views.decorators.http import require_POST

  from agents.chat.principal import principal_for_request
  from agents.defaults import install_default

  _KINDS = ("agent", "flow")


  @require_POST
  def default_install(request):
      kind = (request.POST.get("kind") or "").strip()
      slug = (request.POST.get("slug") or "").strip()
      if kind not in _KINDS:
          return HttpResponseBadRequest(
              f"Unknown kind {kind!r}; must be one of {list(_KINDS)}."
          )
      try:
          install_default(kind, slug, principal_for_request(request))
      except ValueError as exc:
          # An unknown slug is a caller error, not a server fault: the
          # button was rendered from the catalogue, so a slug that is not
          # in it means the page is older than the code.
          return HttpResponseBadRequest(str(exc))
      return redirect(reverse("chat-index"))
  ```
- [ ] Create `agents/chat/service.py` holding **only** `conversation_url` (its body is written out in Task 9's interfaces block). The module is created here because this is the first task that needs it; Task 8 adds the three poller constants and Task 9 adds `start_turn`. Its module docstring says what it is for: the HTTP-free half of the chat surface -- everything two views both need and neither should own.
- [ ] Export the three names from `agents/chat/views/__init__.py` and add the `start/` and `defaults/install/` routes (`name="chat-default-install"`) to `agents/chat/urls.py`.
- [ ] Write `agents/chat/templates/chat/_offers.html` — one `<form method="post">` per uninstalled default, each with `{% csrf_token %}`, hidden `kind`/`slug`, the default's name and description, and a submit button reading "Add the default {{ offer.name }}". **A form, not a link**: a GET must never install, and a `<button>` inside a POST form is the only control that works with JavaScript off and cannot be prefetched into a write.
- [ ] Write `agents/chat/templates/chat/_picker.html` — the shared `<select name="connection">` fragment, rendered by both the index's start form and the thread's turn form:
  ```html
  {% comment %}
  The per-turn model chooser (deviation P3-D7). Rendered by the index's
  "new conversation" form and by the thread's message form, from the SAME
  `picker_options` list `/rag/` and `/vision/` build theirs from
  (models/registry/bindings.py:233).

  `None` -- nothing to pick from at all -- renders nothing, not an empty
  select: a chooser with no choices is a control that lies about having
  one.
  {% endcomment %}
  {% if picker %}
  <label class="picker">
    <span class="muted">Model</span>
    <select name="connection">
      {% for option in picker %}
      <option value="{{ option.value }}"{% if option.selected %} selected{% endif %}>{{ option.label }}</option>
      {% endfor %}
    </select>
  </label>
  {% endif %}
  ```
- [ ] Write `agents/chat/templates/chat/index.html`:
  ```html
  {% extends "chat/base.html" %}
  {% comment %}
  The conversation list and the "new conversation" box.

  NOT an agent builder (spec section 8, first paragraph): there is no
  create-agent form, no tool picker, and no flow editor here. Agents come
  from the shipped catalogue when an operator ADDS one (ruling 2), or
  from rows they write by other means until a builder is designed. A GET
  of this page installs nothing.

  It lists EVERY conversation on this box. That is the honest pre-auth
  behaviour, not an oversight -- see ChatIndexView's docstring and
  agents/chat/README.md.

  Works with no JavaScript at all: the form is a plain POST and the
  answer is a redirect.
  {% endcomment %}
  {% block title %}Chat — farabunker{% endblock %}
  {% block chat_style %}
    .start-box { border: 1px solid var(--border); border-radius: 8px;
                 background: var(--panel); padding: 1rem; margin-bottom: 1.5rem; }
    .start-box textarea { width: 100%; min-height: 4.5rem; }
    .start-controls { display: flex; gap: 1rem; align-items: end;
                      flex-wrap: wrap; margin-top: 0.6rem; }
    .thread-list { list-style: none; padding: 0; margin: 0; }
    .thread-list li { border-bottom: 1px solid var(--border); padding: 0.6rem 0; }
    .thread-list a { color: var(--accent); text-decoration: none; }
    .thread-agent { color: var(--muted); font-size: 0.85rem; }
  {% endblock %}
  {% block chat_content %}
  <h1>Chat</h1>

  {% if nothing_installed %}
  <div class="banner warn">{{ nothing_installed }}</div>
  {% endif %}
  {% if offers %}{% include "chat/_offers.html" %}{% endif %}
  {% if agents %}
  {% else %}
  <form class="start-box" method="post" action="{% url 'chat-start' %}">
    {% csrf_token %}
    <textarea name="text" placeholder="Start a new conversation…"></textarea>
    <div class="start-controls">
      <label>
        <span class="muted">Agent</span>
        <select name="agent">
          {% for agent in agents %}
          <option value="{{ agent.slug }}">{{ agent.name }}</option>
          {% endfor %}
        </select>
      </label>
      {% include "chat/_picker.html" %}
      <button type="submit">Start</button>
    </div>
    {% if agents %}<p class="muted">{{ agents.0.description }}</p>{% endif %}
  </form>
  {% endif %}

  {% if conversations %}
  <ul class="thread-list">
    {% for conversation in conversations %}
    <li>
      <a href="{% url 'chat-conversation' conversation.id %}">{{ conversation.title|default:"Untitled conversation" }}</a>
      <div class="thread-agent">{{ conversation.agent.name }} — {{ conversation.updated_at }}</div>
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="muted">No conversations yet.</p>
  {% endif %}
  {% endblock %}
  ```
- [ ] Run `.venv/bin/pytest -q agents/chat/tests/test_index.py` — green.
- [ ] Run the **full gate matrix**. Green. Record the count.
- [ ] Update `agents/chat/README.md` with the index's behaviour and the `CONVERSATION_LIST_LIMIT` decision (a cap, not a paginator, and why).
- [ ] Commit: `feat(chat): the conversation list, the agent picker, and starting a thread`.

---

### Task 7: `agents/chat/rendering.py` — turns into cards, with no HTTP in sight

Every judgement the thread makes about what a turn *means* lives here, in plain functions over rows, so it is tested without a request and reused unchanged by the poll view's fragment render. The templates that follow (Task 8) do no thinking at all: they iterate dicts.

Four rules this module exists to hold, three of them straight from P2's ledger:

1. **An open `ToolInvocation` is `running`, never an error** — through `agents.runtime.audit.invocation_state`, never by reading `outcome` directly.
2. **A failed call's words come from `error`, not `text`** — through `invocation_message`.
3. **`reverse()` on a vision URL name can raise.** `vision-output-file` exists only when `"vision"` is in `FARABUNKER_FEATURES` (`config/urls.py:19-20`). A conversation that recorded `output:12` on a box where vision was later turned off must still render, with the artifact shown as a plain reference. One `try/except NoReverseMatch`, in one place.
4. **Citations come from `data["citations"]` OR `data["results"]`** — deviation P3-D6. `run_ask` writes the first key, `run_search` the second, and both lists carry the four fields §8.4 names.

And one structural rule: **a delegate's turns are written at `depth >= 1` BEFORE the parent's own TOOL turn**, because `run_loop` calls `invoke_tool` (which runs the whole delegate loop, writing its turns) and only then creates the parent's TOOL row. So the nesting rule is *buffer the deep turns, attach them to the next depth-0 turn* — verify this against `agents/runtime/loop.py`'s ordering before writing the grouper, and pin it with a test that builds the rows in that real order.

**Files:**
- `agents/chat/rendering.py` (new)
- `agents/chat/tests/test_rendering.py` (new)

**Interfaces (exact signatures):**

```python
# agents/chat/rendering.py
ARG_VALUE_MAX = 200

def thread_cards(conversation, *, queue_job_id: int | None = None) -> list[dict]:
    """Every turn of `conversation` as a render-ready card, in index
    order, with `depth > 0` turns nested under the turn they belong to.
    `queue_job_id` narrows it to the turns ONE job wrote -- what the poll
    view hands back when a turn finishes (Task 10)."""

def turn_card(turn, nested: list[dict] | None = None) -> dict: ...
def tool_card(turn) -> dict: ...
def artifact_links(references) -> tuple[list[dict], list[dict]]:
    """`(images, files)` for a turn's artifact reference strings."""
def citations_of(turn) -> list[dict]: ...
```

Card dict, exactly (a fixed key set — a card with a missing key is a template `{% if %}` that silently never fires):

```python
{
    "turn": Turn, "index": int, "role": str, "state": str, "depth": int,
    "text": str, "error": str, "pending": bool,
    "tool": dict | None, "nested": list[dict],
    "images": list[dict], "files": list[dict],
}
```

Tool-card dict, exactly:

```python
{
    "key": str, "label": str, "agent": str, "state": str, "message": str,
    "args": list[tuple[str, str]], "discarded": list[str],
    "citations": list[dict], "images": list[dict], "files": list[dict],
}
```

**Steps:**

- [ ] Re-read `agents/runtime/loop.py`'s `run_loop` and confirm the write order: `invoke_tool(...)` first (a delegate writes its own depth-1 turns inside it), then `Turn.objects.create(role=TOOL, depth=depth, ...)`. Write the confirmed ordering into the grouper's docstring as the reason it buffers rather than looks ahead.
- [ ] Write the failing test `agents/chat/tests/test_rendering.py`:
  ```python
  """`agents.chat.rendering` -- what a turn MEANS, decided once, over
  rows, with no request anywhere.

  Every judgement the thread makes lives here so it can be tested without
  HTTP and reused byte-identically by the poll view's fragment render. A
  template that decided any of this itself would be a second copy that
  drifts the first time one of them is edited.
  """
  from __future__ import annotations

  import pytest
  from django.urls import NoReverseMatch, reverse
  from django.utils import timezone

  from agents.chat.rendering import (
      artifact_links, citations_of, thread_cards, tool_card,
  )
  from agents.chat.tests._helpers import make_agent, make_conversation, make_turn
  from agents.models import ToolInvocation, Turn
  from agents.runtime.audit import RUNNING

  pytestmark = pytest.mark.django_db


  def _invocation(**overrides):
      fields = dict(
          principal_kind="resident_agent", principal_key="general",
          tool_key="rag.search", args={"query": "attention"},
          outcome=ToolInvocation.Outcome.OK, text="two results",
          finished_at=timezone.now(),
      )
      fields.update(overrides)
      return ToolInvocation.objects.create(**fields)


  def _tool_turn(conversation, *, invocation=None, **overrides):
      fields = dict(
          conversation=conversation, role=Turn.Role.TOOL, text="two results",
          state=Turn.State.DONE, invocation=invocation,
          tool_call={"tool": "rag.search", "args": {"query": "attention"},
                     "agent": "general", "id": "", "discarded": []},
      )
      fields.update(overrides)
      return make_turn(**fields)


  class TestToolCardState:
      def test_an_unfinished_call_renders_as_running_not_as_an_error(self):
          """P2 ledger, the whole reason this module exists: the audit
          row is CREATED with `outcome=ERROR` as a placeholder, so a card
          that read `outcome` would show red for a working call."""
          turn = _tool_turn(make_conversation(),
                            invocation=_invocation(finished_at=None,
                                                   outcome=ToolInvocation.Outcome.ERROR))
          assert tool_card(turn)["state"] == RUNNING

      def test_a_failed_call_shows_the_recorded_error_when_there_is_no_turn_text(self):
          """P2 ledger: `ToolInvocation.text` is blank on every failure
          path, so a card reading `text` shows an empty box for the one
          card that most needs words."""
          turn = _tool_turn(
              make_conversation(), text="",
              invocation=_invocation(outcome=ToolInvocation.Outcome.ERROR,
                                     text="", error="the engine timed out"),
          )
          card = tool_card(turn)
          assert card["state"] == ToolInvocation.Outcome.ERROR
          assert card["message"] == "the engine timed out"

      def test_a_turn_with_no_invocation_at_all_is_unknown_not_failed(self):
          """`Turn.invocation` is SET_NULL. A pruned audit row leaves an
          unknown outcome, and rendering that as a failure invents one."""
          assert tool_card(_tool_turn(make_conversation()))["state"] == ""

      def test_discarded_calls_are_named_because_the_model_thinks_it_made_them(self):
          turn = _tool_turn(
              make_conversation(), invocation=_invocation(),
              tool_call={"tool": "rag.search", "args": {}, "agent": "general",
                         "id": "", "discarded": [{"tool": "rag.ask", "args": {}}]},
          )
          assert tool_card(turn)["discarded"] == ["rag.ask"]

      def test_a_long_argument_is_truncated_rather_than_dominating_the_thread(self):
          turn = _tool_turn(
              make_conversation(), invocation=_invocation(),
              tool_call={"tool": "rag.search", "args": {"query": "x" * 500},
                         "agent": "general", "id": "", "discarded": []},
          )
          key, value = tool_card(turn)["args"][0]
          assert key == "query"
          assert len(value) <= 204 and value.endswith("…")


  class TestArtifacts:
      def test_a_vision_output_becomes_an_image_pointing_at_the_streaming_view(self):
          """`vision-output-file` streams strictly by primary key and
          clamps its Content-Type (`tools/vision/views.py:980-1000`), so
          the chat never learns or exposes a filesystem path."""
          images, files = artifact_links(["output:12"])
          assert files == []
          assert images[0]["url"] == reverse("vision-output-file", args=[12])

      def test_a_document_becomes_a_link_to_the_rag_file_view(self):
          images, files = artifact_links(["document:7"])
          assert images == []
          assert files[0]["url"] == reverse("rag-document-file", args=[7])

      def test_a_malformed_reference_is_dropped_not_rendered_as_a_broken_link(self):
          """`parse_artifact` raises for anything that is not
          `<kind>:<digits>`. This value reached the row from a tool
          runner, and a link that cannot resolve is worse than no link."""
          assert artifact_links(["nonsense", "output:", ":12", ""]) == ([], [])

      def test_a_reference_whose_url_is_not_mounted_still_renders_as_text(
          self, monkeypatch
      ):
          """THE VISION-OFF CASE. `vision-output-file` only exists when
          `"vision"` is in FARABUNKER_FEATURES (config/urls.py:19-20), so
          a conversation that recorded `output:12` on a box where vision
          was later turned off must still render.

          Patched at the renderer's own `reverse` rather than by
          overriding the flag: this test uses `reverse()` itself, and THE
          VISION-FLAG RULE makes a flag override here a process-wide URL
          resolution hazard for every later test in the run.
          """
          def _unmounted(*args, **kwargs):
              raise NoReverseMatch("vision is not mounted")

          monkeypatch.setattr("agents.chat.rendering.reverse", _unmounted)
          images, _files = artifact_links(["output:12"])
          assert images[0]["url"] == ""
          assert images[0]["reference"] == "output:12"


  class TestCitations:
      def test_rag_ask_citations_are_read_from_the_citations_key(self):
          turn = _tool_turn(make_conversation(), invocation=_invocation(),
                            data={"citations": [{"document_id": "7", "title": "a.pdf",
                                                 "locator_text": ", p. 5", "score": 0.81}]})
          entry = citations_of(turn)[0]
          assert entry["title"] == "a.pdf"
          assert entry["locator_text"] == ", p. 5"
          assert entry["score_display"] == "0.810"
          assert entry["url"] == reverse("rag-document-file", args=[7])

      def test_rag_search_results_are_read_from_the_results_key(self):
          """DEVIATION P3-D6, and a real spec inconsistency: section 8.4
          names only `data["citations"]`, but `tools/rag/tools.py::
          run_search` writes `data["results"]`. Reading only the first
          key would render every search card with no sources at all."""
          turn = _tool_turn(make_conversation(), invocation=_invocation(),
                            data={"results": [{"document_id": "7", "title": "a.pdf",
                                               "locator_text": "", "score": 0.5,
                                               "score_display": "0.500"}],
                                  "hybrid": False})
          assert citations_of(turn)[0]["title"] == "a.pdf"

      def test_a_citation_with_a_non_numeric_document_id_renders_without_a_link(self):
          """`retrieval._document_url_for` guards the same way
          (retrieval.py:362-379): a stray `file_id` in stored chunk
          metadata must not mint a URL nothing can reverse."""
          turn = _tool_turn(make_conversation(), invocation=_invocation(),
                            data={"citations": [{"document_id": "not-an-id",
                                                 "title": "a.pdf", "score": None}]})
          entry = citations_of(turn)[0]
          assert entry["url"] == "" and entry["title"] == "a.pdf"

      def test_a_turn_with_no_data_has_no_citations_and_does_not_raise(self):
          assert citations_of(_tool_turn(make_conversation())) == []

      def test_source_path_never_reaches_the_card(self):
          """`tools/rag/retrieval.py::_vector_citations` puts
          `source_path` -- a real filesystem path on the server -- in
          every citation dict, and `tools/rag/tools.py::run_ask` hands
          the dict back UNFILTERED, so it is genuinely in `Turn.data`.

          This function builds a FIXED FOUR-KEY dict, which is what keeps
          the path out of the page. Pinned rather than trusted: the
          `artifacts` vocabulary exists precisely so no layer below the
          view learns a path the caller cannot see
          (`agents/contracts/artifacts.py`), and a renderer that started
          spreading the source dict would undo that in one line."""
          turn = _tool_turn(
              make_conversation(), invocation=_invocation(),
              data={"citations": [{"document_id": "7", "title": "a.pdf",
                                   "source_path": "/srv/farabunker/media/a.pdf",
                                   "score": 0.8}]},
          )
          card = citations_of(turn)[0]
          assert set(card) == {"title", "locator_text", "score_display", "url"}
          assert "/srv/farabunker" not in repr(card)


  class TestThreadCards:
      def test_turns_come_back_in_index_order(self):
          conversation = make_conversation()
          make_turn(conversation=conversation, role=Turn.Role.USER, text="q")
          make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                    state=Turn.State.DONE)
          assert [c["role"] for c in thread_cards(conversation)] == ["user", "assistant"]

      def test_a_delegates_turns_nest_under_the_delegating_tool_turn(self):
          """The ORDER is the mechanism, and it comes from `run_loop`:
          `invoke_tool` runs the whole delegate loop (writing its depth-1
          turns) BEFORE the parent's own TOOL row is created. So the deep
          turns precede their parent, and the grouper buffers them rather
          than looking ahead."""
          conversation = make_conversation()
          make_turn(conversation=conversation, role=Turn.Role.USER, text="q")
          _tool_turn(conversation, depth=1, text="library searched",
                     invocation=_invocation(principal_key="library"))
          parent = _tool_turn(
              conversation, depth=0, text="the library said…",
              invocation=_invocation(tool_key="agent.library"),
              tool_call={"tool": "agent.library", "args": {"task": "t"},
                         "agent": "general", "id": "", "discarded": []},
          )
          make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                    state=Turn.State.DONE)

          cards = thread_cards(conversation)

          assert [c["role"] for c in cards] == ["user", "tool", "assistant"]
          assert cards[1]["turn"].pk == parent.pk
          assert [n["tool"]["key"] for n in cards[1]["nested"]] == ["rag.search"]

      def test_an_orphaned_deep_turn_still_renders_rather_than_vanishing(self):
          """A delegate that crashed before its parent's TOOL row was
          written leaves depth-1 turns with nothing to nest under.
          Dropping them would hide work that really happened."""
          conversation = make_conversation()
          _tool_turn(conversation, depth=1, invocation=_invocation())
          cards = thread_cards(conversation)
          assert len(cards) == 1 and cards[0]["depth"] == 1

      def test_a_queued_assistant_turn_is_marked_pending_and_carries_no_text(self):
          conversation = make_conversation()
          turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                           state=Turn.State.QUEUED)
          card = thread_cards(conversation)[-1]
          assert card["pending"] is True and card["state"] == Turn.State.QUEUED

      def test_a_failed_turn_carries_its_error_and_never_a_traceback(self):
          conversation = make_conversation()
          make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                    state=Turn.State.FAILED, error="the worker stopped responding")
          card = thread_cards(conversation)[-1]
          assert card["error"] == "the worker stopped responding"
          assert "Traceback" not in card["error"]

      def test_it_issues_a_bounded_number_of_queries(self, django_assert_num_queries):
          """A thread renders every turn's invocation and every turn's
          tool spec. Without `select_related` that is one query per turn,
          which turns a long conversation into a slow page for no
          reason."""
          conversation = make_conversation()
          for _ in range(5):
              _tool_turn(conversation, invocation=_invocation())
          with django_assert_num_queries(1):
              thread_cards(conversation)
  ```
- [ ] Run it. Read the failure — `ModuleNotFoundError: agents.chat.rendering`.
- [ ] Write `agents/chat/rendering.py`:
  ```python
  """Rows -> render-ready dicts. Every judgement the thread makes about
  what a turn MEANS lives here, and the templates iterate.

  No request, no `HttpResponse`, no template: this module is pure enough
  to test over rows alone, and `agents.chat.views.turns.turn_status`
  reuses it byte-identically to render one finished card into its poll
  body. A template that decided any of this itself would be a second
  copy that drifts the first time one of them is edited.

  IT IMPORTS NOTHING FROM `tools.*`. Import-law rule 3 forbids it outright
  (`foundation/ops/tests/test_import_law.py::test_no_agents_module_
  imports_a_tools_package` walks every import node in every file under
  `agents/`), and nothing here needs to: an artifact is a REFERENCE
  STRING, `agents.contracts.artifacts.artifact_url_name` maps its kind to
  a URL NAME, and Django reverses the name. No layer below the view ever
  learns a filesystem path -- the same rule `tools/vision/services.py`'s
  `job_json` already enforces for the vision page.

  FOUR TRUTHS ABOUT AN AUDIT ROW, three of them from P2's ledger:

  1. `finished_at IS NULL` means RUNNING, whatever `outcome` says --
     `invoke_tool` creates the row with `outcome=ERROR` as a placeholder.
     Read through `agents.runtime.audit.invocation_state`, never directly.
  2. A failure's words are in `error`, not `text` (`invoke.py::_finish`
     writes `text` only when a `ToolResult` came back).
  3. `reverse()` CAN RAISE HERE. `vision-output-file` exists only when
     the vision feature is on, and a conversation outlives a feature
     flag. One `try/except NoReverseMatch`, in `_url_for`, and the
     artifact then renders as plain text.
  4. Citations live under `data["citations"]` (`rag.ask`) OR
     `data["results"]` (`rag.search`) -- deviation P3-D6. Both lists
     carry `title`, `locator_text`, `score`, `document_id`.
  """
  from __future__ import annotations

  import json
  import logging

  from django.urls import NoReverseMatch, reverse

  from agents.contracts.artifacts import artifact_url_name, parse_artifact
  from agents.models import Turn
  from agents.runtime.audit import invocation_message, invocation_state

  logger = logging.getLogger(__name__)

  # How much of one tool argument the card shows. A prompt or a query can
  # be paragraphs long, and a card that rendered all of it would bury the
  # answer it sits above. The full value is always in the row.
  ARG_VALUE_MAX = 200

  _IMAGE_KINDS = ("output", "input")


  def thread_cards(conversation, *, queue_job_id: int | None = None) -> list[dict]:
      """Every turn of `conversation`, in index order, as cards --
      `depth > 0` turns nested under the turn they belong to.

      THE NESTING RULE COMES FROM THE WRITE ORDER, not from a guess.
      `agents.runtime.loop.run_loop` calls `invoke_tool` FIRST -- which,
      for an `agent.<slug>` spec, runs the delegate's entire loop and
      writes its depth-1 turns -- and only THEN creates the parent's own
      TOOL row. Deep turns therefore always PRECEDE the turn they belong
      to, so this buffers them and attaches the buffer to the next
      depth-0 turn rather than looking ahead.

      A buffer left over at the end (a delegate that crashed before its
      parent's row existed) is rendered at top level rather than dropped:
      hiding work that really happened would be the worse failure.

      `queue_job_id` NARROWS IT TO ONE JOB'S OUTPUT, which is what the
      poll view needs: a finished turn is not one card, it is the TOOL
      cards the loop wrote plus the assistant answer, and a poller that
      swapped in only the answer would leave the tool calls invisible
      until a manual refresh. Both `run_loop` and `_run_turn` stamp
      `queue_job_id` on every row they write (`agents/runtime/loop.py`),
      and the USER turn -- written by the view, before any job exists --
      carries none, so this filter selects exactly the job's own output
      and never the message that provoked it. Nesting still works: the
      delegate's depth-1 turns carry the same job id.
      """
      turns = conversation.turns.select_related("invocation").all()
      if queue_job_id is not None:
          turns = turns.filter(queue_job_id=queue_job_id)
      buffered: list[dict] = []
      cards: list[dict] = []
      for turn in turns:
          card = turn_card(turn)
          if turn.depth:
              buffered.append(card)
              continue
          cards.append(turn_card(turn, nested=buffered))
          buffered = []
      cards.extend(buffered)
      return cards


  def turn_card(turn, nested: list[dict] | None = None) -> dict:
      """One turn as a fixed-key dict.

      FIXED KEYS, always present. A card that omitted a key on some paths
      would make a template `{% if %}` silently never fire, which is the
      class of bug a renderer is least likely to notice.
      """
      images, files = artifact_links(turn.artifacts or ())
      return {
          "turn": turn,
          "index": turn.index,
          "role": turn.role,
          "state": turn.state,
          "depth": turn.depth,
          "text": turn.text,
          "error": turn.error,
          # A turn the page should keep watching. Only an ASSISTANT turn
          # is ever non-`done` (`agents/models.py::Turn`'s own docstring),
          # so this is exactly the placeholder the poller is waiting on.
          "pending": turn.state in (Turn.State.QUEUED, Turn.State.RUNNING),
          "tool": tool_card(turn) if turn.role == Turn.Role.TOOL else None,
          "nested": nested or [],
          "images": images,
          "files": files,
      }


  def tool_card(turn) -> dict:
      """One TOOL turn as a card: what ran, with what, how it ended, and
      what it produced."""
      call = turn.tool_call or {}
      key = call.get("tool") or ""
      images, files = artifact_links(turn.artifacts or ())
      return {
          "key": key,
          "label": _tool_label(key),
          "agent": call.get("agent") or "",
          # NEVER `turn.invocation.outcome` directly -- see truth 1.
          "state": invocation_state(turn.invocation),
          "message": invocation_message(turn.invocation, turn.text),
          "args": [(k, _short(v)) for k, v in sorted((call.get("args") or {}).items())],
          # Recorded, never silent: a model reasoning about three calls it
          # thinks it made is reasoning about a turn that did not happen
          # (`loop._tool_message_text` tells the model the same thing).
          "discarded": [d.get("tool") or "" for d in (call.get("discarded") or [])],
          "citations": citations_of(turn),
          "images": images,
          "files": files,
      }


  def citations_of(turn) -> list[dict]:
      """The library sources a tool turn recorded.

      `data["citations"]` (`tools/rag/tools.py::run_ask`) OR
      `data["results"]` (`::run_search`) -- deviation P3-D6. Both carry
      `title`, `locator_text`, `score`, `document_id`
      (`tools/rag/retrieval.py::_vector_citations` and
      `::_search_result_for`), which are exactly the four fields spec
      section 8.4 names.
      """
      data = turn.data if isinstance(turn.data, dict) else {}
      entries = data.get("citations") or data.get("results") or []
      out = []
      for entry in entries:
          if not isinstance(entry, dict):
              continue
          out.append({
              "title": entry.get("title") or "(untitled)",
              "locator_text": entry.get("locator_text") or "",
              "score_display": _score_display(entry),
              "url": _document_url(entry.get("document_id")),
          })
      return out


  def artifact_links(references) -> tuple[list[dict], list[dict]]:
      """`(images, files)` for a turn's artifact reference strings.

      An unparseable reference is DROPPED. `parse_artifact` raises for
      anything that is not `<kind>:<digits>`; this value reached the row
      from a tool runner, and a link that cannot resolve is worse than no
      link at all.
      """
      images: list[dict] = []
      files: list[dict] = []
      for reference in references:
          try:
              kind, pk = parse_artifact(reference)
          except ValueError:
              logger.info("chat: dropping unparseable artifact reference %r", reference)
              continue
          entry = {"reference": reference, "kind": kind,
                   "url": _url_for(artifact_url_name(kind), pk)}
          (images if kind in _IMAGE_KINDS else files).append(entry)
      return images, files


  def _url_for(url_name: str, pk: int) -> str:
      """The reversed URL, or `""` when that route is not mounted.

      THE ONE PLACE `NoReverseMatch` IS CAUGHT. `vision-output-file` and
      `vision-input-file` exist only when `"vision"` is in
      `FARABUNKER_FEATURES` (`config/urls.py:19-20`), and a conversation
      outlives a feature flag. `""` makes the template render the
      artifact as plain text -- honest about what it is and honest that
      there is nothing here to open.
      """
      try:
          return reverse(url_name, args=[pk])
      except NoReverseMatch:
          logger.info(
              "chat: %r is not mounted on this install; rendering the artifact "
              "as plain text.", url_name,
          )
          return ""


  def _document_url(document_id) -> str:
      """`rag-document-file` for a numeric id, else `""`.

      Guarded with `isdigit()` the same way `tools/rag/retrieval.py::
      _document_url_for` guards its own: a stray non-numeric `file_id` in
      stored chunk metadata must not mint a URL nothing can reverse.
      """
      if document_id is None or not str(document_id).isdigit():
          return ""
      return _url_for("rag-document-file", int(document_id))


  def _score_display(entry: dict) -> str:
      """`run_search` already formats one (`score_display`); `run_ask`'s
      citations carry the raw `score` only. Formatted the same way either
      way so two cards in one thread never disagree about precision."""
      if entry.get("score_display"):
          return str(entry["score_display"])
      score = entry.get("score")
      return f"{score:.3f}" if isinstance(score, (int, float)) else ""


  def _tool_label(key: str) -> str:
      """The registered spec's operator-facing label, or the key.

      `_TOOLS.get`, not `get_tool`, deliberately: `get_tool` RAISES for an
      absent key, and a conversation can outlive the tool it called (a
      feature turned off, a tool retired). The key is always readable and
      is never a worse label than a crash.
      """
      from agents.contracts.tools import _TOOLS

      spec = _TOOLS.get(key)
      return spec.label if spec is not None else key


  def _short(value) -> str:
      """One argument value, truncated, JSON for anything not a string."""
      text = value if isinstance(value, str) else json.dumps(value, default=str)
      return text if len(text) <= ARG_VALUE_MAX else text[:ARG_VALUE_MAX].rstrip() + "…"
  ```
- [ ] Run `.venv/bin/pytest -q agents/chat/tests/test_rendering.py` — green. If `django_assert_num_queries(1)` fails, fix the queryset (`select_related("invocation")`), never the assertion.
- [ ] Run the **full gate matrix**. Green. Record the count.
- [ ] Update `agents/chat/README.md` with a **What a card knows** section listing the four truths and pointing at `agents/runtime/audit.py` as their authority.
- [ ] Commit: `feat(chat): rendering — turns become cards, with an open call reading as in progress`.

---

### Task 8: the thread view and its templates

The page a person actually reads: the conversation, every turn in order, tool cards with thumbnails and citations, delegated turns in a collapsed disclosure, and a message form that is a plain `<form method="post">` and works with no JavaScript at all. Task 9 gives that form a destination; this task renders everything a thread already contains.

`_turn_card.html` is rendered **twice** — inline by this page, and standalone by `turn_status`'s poll body (Task 10). That is why it takes a card dict and nothing else, and why every rule it needs lives in `chat/base.html` rather than on this page (the same reason `tools/vision/templates/vision/base.html` owns the job-card rules: "a page cannot own rules a fragment three views render").

**Files:**
- `agents/chat/views/thread.py` (new), `agents/chat/views/__init__.py`, `agents/chat/urls.py`
- `agents/chat/templates/chat/conversation.html`, `_turn_card.html`, `_tool_card.html` (new)
- `agents/chat/templates/chat/base.html` (the thread's CSS)
- `agents/chat/tests/test_thread.py` (new)

**Interfaces (exact signatures):**

```python
# agents/chat/views/thread.py
class ConversationView(TemplateView):
    template_name = "chat/conversation.html"
    def get_context_data(self, **kwargs) -> dict: ...

def thread_context(request, conversation, *, selected: str | None = None) -> dict:
    """Everything both the page and a 503 re-render need. One builder,
    so an error response can never show a different thread than a
    success would."""
```

**Steps:**

- [ ] Write the failing test `agents/chat/tests/test_thread.py`:
  ```python
  """The thread page. NO FARABUNKER_FEATURES OVERRIDE (test_mount.py)."""
  from __future__ import annotations

  import uuid

  import pytest
  from django.urls import reverse
  from django.utils import timezone

  from agents.chat.tests._helpers import (   # noqa: F401
      bound_chat_role, make_agent, make_conversation, make_thread, make_turn,
  )
  from agents.models import ToolInvocation, Turn

  pytestmark = pytest.mark.django_db


  class TestTheThread:
      def test_it_renders_the_whole_conversation_in_order(self, client):
          conversation = make_thread()
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert body.index("hello") < body.index("hi")

      def test_an_unknown_conversation_is_a_404_not_a_500(self, client):
          response = client.get(
              reverse("chat-conversation", args=[uuid.uuid4()])
          )
          assert response.status_code == 404

      def test_the_page_names_its_agent(self, client):
          conversation = make_conversation(agent=make_agent(name="General assistant"))
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert "General assistant" in body

      def test_the_message_form_is_a_plain_post_that_needs_no_javascript(self, client):
          """Progressive enhancement, not JS-dependence: the form must be
          complete and submittable before a single script runs."""
          conversation = make_conversation()
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert f'action="{reverse("chat-turn", args=[conversation.id])}"' in body
          assert 'method="post"' in body
          assert "csrfmiddlewaretoken" in body

      def test_the_picked_connection_survives_into_the_form(self, client):
          """Deviation P3-D7: `?connection=` is what makes the redirect
          after a submission and a bookmark agree."""
          conversation = make_conversation()
          url = reverse("chat-conversation", args=[conversation.id])
          body = client.get(url, {"connection": "3"}).content.decode()
          assert 'name="connection"' in body


  class TestToolCardsOnThePage:
      def _tool_turn(self, conversation, **overrides):
          invocation = ToolInvocation.objects.create(
              principal_kind="resident_agent", principal_key="general",
              tool_key="rag.search", outcome=ToolInvocation.Outcome.OK,
              text="two results", finished_at=timezone.now(),
          )
          fields = dict(
              conversation=conversation, role=Turn.Role.TOOL, text="two results",
              state=Turn.State.DONE, invocation=invocation,
              tool_call={"tool": "rag.search", "args": {"query": "attention"},
                         "agent": "general", "id": "", "discarded": []},
          )
          fields.update(overrides)
          return make_turn(**fields)

      def test_a_citation_links_to_the_document_file_view(self, client):
          """Spec section 8.4: citations link to `rag-document-file`."""
          conversation = make_conversation()
          self._tool_turn(conversation,
                          data={"citations": [{"document_id": "7", "title": "a.pdf",
                                               "locator_text": ", p. 5", "score": 0.8}]})
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert reverse("rag-document-file", args=[7]) in body
          assert "a.pdf" in body and ", p. 5" in body

      def test_a_vision_output_renders_as_an_img_pointing_at_the_output_view(
          self, client
      ):
          """Spec section 8.4: an `output:<id>` artifact is an `<img>`
          whose src is `vision-output-file`, which streams strictly by pk
          and clamps its Content-Type -- so the chat never learns or
          exposes a filesystem path."""
          conversation = make_conversation()
          self._tool_turn(conversation, artifacts=["output:12"],
                          tool_call={"tool": "vision.generate", "args": {},
                                     "agent": "general", "id": "", "discarded": []})
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert f'<img src="{reverse("vision-output-file", args=[12])}"' in body

      def test_a_source_path_in_the_citation_data_never_reaches_the_page(
          self, client
      ):
          """The rendered counterpart of `test_rendering.py`'s
          `test_source_path_never_reaches_the_card`. `run_ask` hands its
          citation dicts back unfiltered and they really do carry a
          server filesystem path; the page is the last place that could
          leak one, so it is asserted where a person would see it."""
          conversation = make_conversation()
          self._tool_turn(conversation,
                          data={"citations": [{"document_id": "7", "title": "a.pdf",
                                               "source_path": "/srv/farabunker/media/a.pdf",
                                               "score": 0.8}]})
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert "a.pdf" in body
          assert "/srv/farabunker" not in body

      def test_the_tool_arguments_are_shown(self, client):
          conversation = make_conversation()
          self._tool_turn(conversation)
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert "attention" in body

      def test_a_running_tool_call_says_so_rather_than_showing_an_error(self, client):
          """P2 ledger, end to end through the template this time."""
          conversation = make_conversation()
          invocation = ToolInvocation.objects.create(
              principal_kind="resident_agent", principal_key="general",
              tool_key="rag.search", outcome=ToolInvocation.Outcome.ERROR,
          )
          self._tool_turn(conversation, invocation=invocation, text="")
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert "still running" in body.lower()


  class TestDelegatedTurns:
      def test_they_render_inside_a_collapsed_disclosure(self, client):
          """Spec section 8.4: `depth > 0` turns render inside a
          collapsed disclosure under the delegating turn -- a delegate is
          a subroutine, and its steps are auditable without being the
          thread."""
          conversation = make_conversation()
          make_turn(conversation=conversation, role=Turn.Role.TOOL, depth=1,
                    text="the library searched", state=Turn.State.DONE,
                    tool_call={"tool": "rag.search", "args": {}, "agent": "library",
                               "id": "", "discarded": []})
          make_turn(conversation=conversation, role=Turn.Role.TOOL, depth=0,
                    text="library answered", state=Turn.State.DONE,
                    tool_call={"tool": "agent.library", "args": {"task": "t"},
                               "agent": "general", "id": "", "discarded": []})
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert "<details" in body
          assert "the library searched" in body
          # Collapsed: no `open` attribute on the disclosure.
          assert "<details open" not in body


  class TestFailedAndCancelledTurns:
      def test_a_failed_turn_shows_its_error_and_a_way_back_to_setup(self, client):
          conversation = make_conversation()
          make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                    state=Turn.State.FAILED, error="the worker stopped responding")
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert "the worker stopped responding" in body
          assert reverse("inference-console") in body

      def test_a_cancelled_turn_says_so(self, client):
          conversation = make_conversation()
          make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                    state=Turn.State.CANCELLED,
                    error="Cancelled from the queue before it ran.")
          body = client.get(
              reverse("chat-conversation", args=[conversation.id])
          ).content.decode()
          assert "Cancelled from the queue before it ran." in body
  ```
- [ ] Run it. Read the failures.
- [ ] Write `agents/chat/views/thread.py`:
  ```python
  """One conversation, read.

  A GET here NEVER 503s. An unbound chat role, an unreachable engine, and
  an unavailable queue are all reasons a NEW turn cannot be queued -- they
  are not reasons the operator may not read what was already said. The 503
  belongs to the POST (Task 9); this page shows a banner and renders the
  thread.
  """
  from __future__ import annotations

  from django.shortcuts import get_object_or_404
  from django.urls import reverse
  from django.views.generic import TemplateView

  from agents.chat.pickers import chat_picker_options
  from agents.chat.rendering import thread_cards
  from agents.chat.service import (
      MAX_POLL_DURATION_MS, MAX_TRANSPORT_RETRIES, POLL_INTERVAL_MS,
  )
  from agents.models import Conversation


  def thread_context(request, conversation, *, selected: str | None = None) -> dict:
      """Everything the conversation page needs, built ONCE.

      Task 9's 503 re-render calls this too, so an error response can
      never show a different thread -- or a different picker selection --
      than a success would. That is the same reason
      `tools/vision/views.py::_create_page_context` exists.

      `selected` is an ARGUMENT rather than a `request.GET` read, because
      the 503 re-render happens on a POST: the pick is in `request.POST`
      there, and a builder that only ever looked at the query string
      would silently revert the operator's chosen model on the one
      render where they are least able to notice. `None` means "read the
      query string", which is the GET path's own behaviour.
      """
      if selected is None:
          selected = request.GET.get("connection", "")
      return {
          "conversation": conversation,
          "agent": conversation.agent,
          "cards": thread_cards(conversation),
          "picker": chat_picker_options(selected),
          "selected_connection": selected,
          # Declared ONCE, in `agents/chat/service.py`, and handed to the
          # template from here (M1: `thread.py` never imports `turns.py`).
          "poll_interval_ms": POLL_INTERVAL_MS,
          "max_transport_retries": MAX_TRANSPORT_RETRIES,
          "max_poll_duration_ms": MAX_POLL_DURATION_MS,
          # `?pending=<turn_id>` is the no-JS path's answer to "where did
          # my message go": the redirect carries it, the page renders that
          # turn as pending, and the poller (Task 10) picks it up from
          # here on a JS-enabled load. One key serves both paths.
          "pending_turn_id": request.GET.get("pending", ""),
          "setup_url": reverse("inference-console"),
      }


  class ConversationView(TemplateView):
      """GET /chat/c/<uuid>/ -- the thread."""

      template_name = "chat/conversation.html"

      def get_context_data(self, **kwargs) -> dict:
          conversation = get_object_or_404(
              Conversation.objects.select_related("agent"),
              pk=kwargs["conversation_id"],
          )
          context = super().get_context_data(**kwargs)
          context.update(thread_context(self.request, conversation))
          return context
  ```
- [ ] Add `POLL_INTERVAL_MS = 2000`, `MAX_TRANSPORT_RETRIES = 3`, and `MAX_POLL_DURATION_MS = 10 * 60 * 1000` to `agents/chat/service.py` (values from `tools/rag/templates/rag/ask.html:355-359`, the poller this one mirrors), with the comment saying why they are there and not in `views/turns.py`: `thread_context` hands them to the template, and `thread.py` must not import `turns.py`. Task 10's script and its test both read them from here.
- [ ] Export `ConversationView` and add the `c/<uuid:conversation_id>/` route.
- [ ] Write `agents/chat/templates/chat/_tool_card.html`:
  ```html
  {% comment %}
  One tool call. Rendered inside `_turn_card.html`, which is itself
  rendered BOTH inline by the conversation page and standalone by
  `turn_status`'s poll body -- so this fragment takes a card dict
  (`agents/chat/rendering.py::tool_card`) and decides nothing itself.

  `state` is `agents.runtime.audit.invocation_state`, NOT
  `ToolInvocation.outcome`: an unfinished row reads as "running", because
  `invoke_tool` creates it with `outcome=ERROR` as a placeholder. A card
  that read the column directly would show red for a working call.
  {% endcomment %}
  <div class="tool-card tool-{{ tool.state|default:'unknown' }}">
    <div class="tool-head">
      <span class="tool-name">{{ tool.label }}</span>
      <span class="tool-state">{{ tool.state|default:"outcome not recorded" }}</span>
    </div>
    {% if tool.args %}
    <dl class="tool-args">
      {% for key, value in tool.args %}<dt>{{ key }}</dt><dd>{{ value }}</dd>{% endfor %}
    </dl>
    {% endif %}
    {% if tool.message %}<p class="tool-message">{{ tool.message }}</p>{% endif %}
    {% if tool.images %}
    <div class="tool-images">
      {% for image in tool.images %}
        {% if image.url %}
        <a href="{{ image.url }}"><img src="{{ image.url }}" alt="{{ image.reference }}"></a>
        {% else %}
        {# The route is not mounted on this install (vision turned off).
           Honest about what the artifact is, and honest that there is
           nothing here to open. #}
        <span class="muted">{{ image.reference }} (not available on this install)</span>
        {% endif %}
      {% endfor %}
    </div>
    {% endif %}
    {% if tool.citations %}
    <ul class="tool-citations">
      {% for citation in tool.citations %}
      <li>
        {% if citation.url %}<a href="{{ citation.url }}">{{ citation.title }}</a>{% else %}{{ citation.title }}{% endif %}{{ citation.locator_text }}
        {% if citation.score_display %}<span class="muted">{{ citation.score_display }}</span>{% endif %}
      </li>
      {% endfor %}
    </ul>
    {% endif %}
    {% if tool.discarded %}
    <p class="muted">Not run this step: {{ tool.discarded|join:", " }}</p>
    {% endif %}
  </div>
  ```
- [ ] Write `agents/chat/templates/chat/_turn_card.html`:
  ```html
  {% comment %}
  ONE turn. Rendered inline by conversation.html AND standalone by
  `turn_status`'s `{"state": "done", "html": ...}` body -- which is the
  whole reason it takes a card dict and carries no page chrome.

  A `depth > 0` turn never renders itself at top level: the grouper in
  `agents/chat/rendering.py::thread_cards` hands it to its parent, which
  renders it inside the collapsed disclosure below (spec section 8.4).
  {% endcomment %}
  <article class="turn turn-{{ card.role }}{% if card.pending %} turn-pending{% endif %}"
           data-turn-id="{{ card.turn.pk }}">
    {% if card.role == "tool" %}
      {% include "chat/_tool_card.html" with tool=card.tool %}
      {% if card.nested %}
      <details class="turn-nested">
        <summary>{{ card.nested|length }} step(s) run by the delegated agent</summary>
        {% for nested in card.nested %}{% include "chat/_turn_card.html" with card=nested %}{% endfor %}
      </details>
      {% endif %}
    {% else %}
      {% if card.pending %}
      <p class="muted no-js-note">Queued. Refresh to see the answer.</p>
      {% elif card.state == "failed" or card.state == "cancelled" %}
      {# `Turn.error` is `str(exc)`, never a traceback -- `run_turn`
         writes it that way and `models/queue/worker.py` already sets
         that rule for job failures. #}
      <p class="turn-error">{{ card.error|default:"This turn did not finish." }}</p>
      <p class="muted"><a href="{% url 'inference-console' %}">Model setup</a></p>
      {% else %}
      <div class="turn-text">{{ card.text|linebreaks }}</div>
      {% endif %}
      {% for image in card.images %}
        {% if image.url %}<a href="{{ image.url }}"><img src="{{ image.url }}" alt="{{ image.reference }}"></a>{% endif %}
      {% endfor %}
    {% endif %}
  </article>
  ```
- [ ] Write `agents/chat/templates/chat/conversation.html` extending `chat/base.html`: the agent's name and description, then the cards — Task 10 factors that loop into `chat/_turn_block.html` so the poll body reuses it; until then write it inline as `{% for card in cards %}{% include "chat/_turn_card.html" %}{% endfor %}` and expect Task 10 to replace it with `{% include "chat/_turn_block.html" %}` — then the message form —
  ```html
  <form id="turn-form" method="post" action="{% url 'chat-turn' conversation.id %}">
    {% csrf_token %}
    <textarea name="text" required placeholder="Message {{ agent.name }}…"></textarea>
    <div class="turn-controls">
      {% include "chat/_picker.html" %}
      <button type="submit">Send</button>
    </div>
  </form>
  ```
  — plus a `<div id="turn-errors"></div>` slot above it (the XHR 400/503 body lands there, exactly as `tools/vision/templates/vision/create.html`'s `#form-errors` does, and **never** into the thread, so a rejected message can never be mistaken for a sent one), and a delete disclosure posting to `chat-conversation-delete` (Task 11 gives it a view). Add `data-pending-turn="{{ pending_turn_id }}"` on the wrapper for Task 10's poller to bootstrap from.
- [ ] Add the thread's CSS to `chat/base.html`'s `{% block extra_style %}` — `.turn`, `.turn-user`/`.turn-assistant` alignment, `.turn-pending`, `.turn-error` (use `var(--danger)`), `.tool-card` and its bands, `.tool-args` as a two-column grid like `.job-facts`, `.tool-images img { max-width: 100%; max-height: 360px; }`, `.tool-citations`, `.turn-nested`. Reuse the shell's tokens (`--panel`, `--border`, `--muted`, `--accent`, `--danger`); define no new colours.
- [ ] Run `.venv/bin/pytest -q agents/chat/tests/test_thread.py` — green.
- [ ] Run the **full gate matrix**. Green. Record the count.
- [ ] Commit: `feat(chat): the thread — turns, tool cards, citations, thumbnails, nested delegates`.

---

### Task 9: posting a turn — one shared preflight, two rows, one enqueue

The POST path, following `manage.py agent_turn` step for step, because **the CLI is this page's reference client**: it preflights before it writes, writes the USER turn and the placeholder ASSISTANT turn in one transaction, enqueues, stamps `queue_job_id`, and never leaves a half-written thread behind. Its exit codes are the page's states.

The preflight rule is currently spelled twice — `agent_turn._preflight` and `loop._run_turn`'s in-loop tool-calling gate. A third copy in a view would be the drift `agents/runtime/bindings.py::resolve_chat` was extracted to prevent (P2 review finding M7: "one rule, one implementation"). So this task extracts `agents/runtime/preflight.py`, moves the CLI onto it **without changing a single one of its messages**, and the view uses the same function.

**Files:**
- `agents/runtime/preflight.py` (new)
- `agents/management/commands/agent_turn.py` (`_preflight` delegates; messages unchanged)
- `agents/chat/service.py` (new — the HTTP-free turn starter)
- `agents/chat/views/turns.py` (new), `views/conversations.py` (start posts the first turn), `views/__init__.py`, `urls.py`
- `agents/chat/tests/_helpers.py` (queue doubles), `agents/chat/tests/test_turn_create.py` (new)
- `agents/runtime/tests/test_preflight.py` (new)

**Interfaces (exact signatures):**

```python
# agents/runtime/preflight.py
UNBOUND = "unbound"
UNREGISTERED_CONNECTION = "unregistered_connection"
NO_TOOL_CALLING = "no_tool_calling"

@dataclass(frozen=True)
class Preflight:
    ok: bool
    reason: str          # "" when ok, else one of the three above
    message: str
    resolved: object | None
    answered_by: str
    dropped_tools: tuple[str, ...]

def preflight_turn(agent, connection) -> Preflight: ...
```

```python
# agents/chat/service.py
# The poller's three numbers live HERE, not in `views/turns.py`, because
# `thread.py` hands them to the template and must not import `turns.py`
# (M1: the package's import direction is one-way).
POLL_INTERVAL_MS = 2000
MAX_TRANSPORT_RETRIES = 3
MAX_POLL_DURATION_MS = 10 * 60 * 1000
TITLE_MAX = 60

@dataclass(frozen=True)
class TurnStart:
    ok: bool
    status: int              # 202 | 400 | 409 | 503
    error: str
    setup_url: str
    notes: tuple[str, ...]
    turn: object | None
    job_id: int | None
    position: int | None
    priority: int | None

def start_turn(conversation, text: str, *, connection: str = "") -> TurnStart: ...

def conversation_url(conversation, *, connection: str = "", pending=None) -> str:
    """THE ONE thread-URL builder. `conversations.py` and `turns.py`
    both import it; neither builds a query string of its own."""
```

**Steps:**

- [ ] Write the failing test `agents/runtime/tests/test_preflight.py`, covering: a bound role and a tool-capable model gives `ok=True`; an unbound role gives `reason == UNBOUND` and a message naming the role; a non-blank connection pk that resolves to nothing gives `reason == UNREGISTERED_CONNECTION` (a **different** message from an unbound role — the operator picked a model that is gone, which is not the same problem as never having picked one); a granted-tools agent on a model reporting `False` gives `reason == NO_TOOL_CALLING`; a **no-tools** agent on that same model is `ok=True` (the gate is conditional on the agent actually holding tools — `agent_turn._preflight` already reads that way and `loop._run_turn` matches it); `_supports_tool_calling` returning `None` runs the turn (`models/contracts/engines/base.py:450-458`: "we could not find out" and "the engine does not report it" both mean attempt it); `dropped_tools` names a granted key that is not registered.
  Use `bound_chat_role` for the real `ModelConnection`+`RoleBinding` row and patch **`agents.runtime.preflight.loop_module._supports_tool_calling`**'s owning module attribute — never `resolve`, which is code under test.
- [ ] Run it; read the failure.
- [ ] Write `agents/runtime/preflight.py`:
  ```python
  """Can this agent take a turn at all -- asked ONCE, answered the same
  way for every caller.

  Spec section 10.1's first three rows ("chat role unbound", "bound model
  cannot call tools", and the picked connection that no longer exists)
  are refusals that must happen BEFORE anything is written, so a turn
  that cannot possibly run never becomes a queued job somebody has to go
  and cancel. `manage.py agent_turn` has raised them since P2; `/chat/`
  raises the same three, and a third hand-rolled copy of the rule in a
  view is exactly the drift `agents.runtime.bindings.resolve_chat` was
  extracted to prevent (P2 review, finding M7).

  It does NOT replace `agents.runtime.loop._run_turn`'s own tool-calling
  check. That one is the last-resort HONEST ENDING for a turn that got
  queued anyway -- a binding can change between the enqueue and the run
  (ADR 0013:205-213), and a queued job never trusts an enqueue-time
  decision as its run-time truth. Two checks, two different jobs.

  `_supports_tool_calling` is reached through the MODULE
  (`loop_module._supports_tool_calling(...)`), never a `from ... import`
  bound at import time: the name is looked up at call time, which is what
  lets a test patch it and have this call see it. A hoisted `from`-import
  copies the function object into this namespace and the patch never
  reaches it (P2 review, finding N4).
  """
  from __future__ import annotations

  import logging
  from dataclasses import dataclass

  from agents.contracts.tools import granted_tools
  from agents.runtime import loop as loop_module
  from agents.runtime.bindings import principal_for, resolve_chat

  logger = logging.getLogger(__name__)

  UNBOUND = "unbound"
  UNREGISTERED_CONNECTION = "unregistered_connection"
  NO_TOOL_CALLING = "no_tool_calling"

  _UNREGISTERED_CONNECTION_MESSAGE = (
      "That model is no longer registered — pick another in the model console."
  )


  @dataclass(frozen=True)
  class Preflight:
      """Whether a turn may be queued, and why not.

      `reason` is a CLOSED vocabulary, not prose: the page maps it to
      copy and the command maps it to an exit, and a caller that had to
      match on `message` would break the first time the wording improved.
      `message` is the operator-readable sentence -- the platform's own,
      never a reworded copy.

      `dropped_tools` is the TOLERANT half (spec section 8.3 step 3): a
      granted tool that is not registered here, or whose role will not
      resolve, is reported as a NOTE and the turn still runs without it.
      A refusal and a note are different things and this type keeps them
      apart.
      """

      ok: bool
      reason: str
      message: str
      resolved: object | None
      answered_by: str
      dropped_tools: tuple[str, ...]


  # `principal_for` is NOT defined here -- it lives in
  # `agents/runtime/bindings.py` (below), and this module imports it.
  # Putting it here would build a cycle: `loop.py` and `jobs.py` both
  # need it, `preflight.py` imports `loop.py`, so `loop.py` importing
  # `preflight.py` back would close the loop. `bindings.py` is the
  # module all three already import and it imports none of them.


  def preflight_turn(agent, connection) -> Preflight:
      """Resolve `agent`'s chat model and check it can do what `agent`
      needs, without writing anything."""
      try:
          resolved, answered_by = resolve_chat(agent, connection)
      except (ValueError, TypeError) as exc:
          # TWO CAUSES, TWO MESSAGES. An operator who picked a model that
          # has since been deleted has a different problem from one who
          # never bound the role, and `tools/rag/views.py` keeps the same
          # two apart for the same reason.
          if connection not in (None, ""):
              logger.info("chat: picked connection %r no longer resolves (%s)",
                          connection, exc)
              return Preflight(False, UNREGISTERED_CONNECTION,
                               _UNREGISTERED_CONNECTION_MESSAGE, None, "", ())
          return Preflight(
              False, UNBOUND,
              f"No model is assigned to the {agent.llm_role!r} role yet, so "
              f"{agent.name!r} cannot answer. Assign one in the model console.",
              None, "", (),
          )

      granted = granted_tools(principal_for(agent), agent.tool_keys)
      dropped = tuple(key for key in agent.tool_keys if key not in granted)
      if granted and loop_module._supports_tool_calling(resolved) is False:
          # `False` means the engine REPORTED it. `None` -- it does not
          # report the fact at all -- runs the turn
          # (`models/contracts/engines/base.py:450-458`).
          return Preflight(
              False, NO_TOOL_CALLING,
              f"The model assigned to {agent.llm_role!r} reports that it cannot call "
              f"tools, and {agent.name!r} needs them. Assign a tool-capable model to "
              f"that role, or use an agent that needs none.",
              resolved, answered_by, dropped,
          )
      return Preflight(True, "", "", resolved, answered_by, dropped)
  ```
- [ ] **Retarget the CLI's two patches BEFORE moving the code, or the move lands silently green.** `agents/tests/test_agent_turn_command.py:58-60` and `:70` both patch `agents.management.commands.agent_turn._supports_tool_calling`. That name exists today because the command imports it (`from agents.runtime.loop import _supports_tool_calling`); the moment `_preflight` delegates to `preflight_turn`, the patched attribute is **no longer on the call path** and both tests go inert — the tool-calling refusal test would pass by reaching a real engine or by never checking at all. Change both to patch **`agents.runtime.loop._supports_tool_calling`**, which is the module attribute `preflight.py` looks up at call time (and the same target `patch_llm` already uses). Do this as its own edit, run the two tests, and **watch the tool-calling test go RED before the delegation lands** — a patch that no longer bites is invisible unless you look for it.
- [ ] Move `agent_turn._preflight` onto it **without changing its behaviour**: call `preflight_turn(agent, connection)` and raise `CommandError(result.message)` when `not result.ok`. **The patch target moved; the message assertions must pass untouched.** `test_an_unbound_chat_role_is_refused_before_anything_is_queued` asserts the role key appears in the message and `test_a_model_that_cannot_call_tools...` asserts `"cannot call tools"` does — `preflight.py`'s two sentences are written to keep both true. If either fails, the sentence drifted and the sentence is what gets fixed. Update the method's docstring to say the rule now lives in one place, and keep its "a management command may poll the queue" paragraph untouched.
- [ ] **Declare the one behaviour change, because it is not a pure move.** `resolve_chat` raises the same `ValueError` for an unbound role and for a picked connection that no longer resolves, and the command previously reported both with its one "Could not resolve a chat model" sentence. `preflight_turn` splits them: `UNBOUND` keeps the role-naming sentence, and `UNREGISTERED_CONNECTION` gets `tools/rag/views.py`'s existing copy ("That model is no longer registered — pick another in the model console"). The command therefore says something **different, and truer**, for `--connection <dead pk>`. Add a CLI test for that path naming the new sentence, and record the change in the task report rather than letting a reviewer discover it as an unexplained diff.
- [ ] **Add `principal_for` to `agents/runtime/bindings.py`, not to `preflight.py`** (RULING: one function, and it must be reachable without a cycle). `bindings.py` is already the module that answers "which model, for this agent" and is already imported by `loop.py:48`, `jobs.py:19`, and `preflight.py`; it imports none of them. Defining `principal_for` in `preflight.py` instead would need `loop.py` and `jobs.py` to import `preflight.py`, while `preflight.py` imports `loop.py` — a cycle. Body:
  ```python
  def principal_for(agent) -> Principal:
      """The principal a turn by `agent` runs as.

      Here, beside `resolve_chat`, for the same reason that function is
      here: it answers a question about an agent that several callers ask
      and none of them should answer twice. `loop._run_turn`,
      `jobs._tool_roles`, and `preflight_turn` all call it, and this
      module imports none of them, so it can never be half of a cycle.

      `/chat/` runs turns as the chosen agent's own principal and invents
      no user identity -- there are no users on this box yet (spec
      section 14 gap 4). The kind a principal is reported as is exactly
      the field a future grants table joins on, so a second spelling of
      it is a future data-quality bug, not a style question.
      """
      from agents.contracts.tools import Principal

      return Principal(
          kind="resident_agent" if agent.resident else "user_agent", key=agent.slug,
      )
  ```
- [ ] **Move the inline copies onto it: TWO call sites and ONE deletion.** `agents/runtime/loop.py::_run_turn` and `agents/runtime/jobs.py::_tool_roles` each build `Principal(kind="resident_agent" if ... else "user_agent", key=agent.slug)` by hand today and now call `principal_for` instead. `agent_turn._preflight` is the deletion, not a third call site: after delegating to `preflight_turn` it builds no `Principal` at all — `granted_tools` is called inside `preflight_turn` now — so its copy and its `Principal` import both go. Check `agents/contracts/tools.Principal` is still imported where it is still used and nowhere else.
- [ ] **Verify acyclicity in the order that can actually expose it.** `python -c "import agents.runtime.jobs"` **and** `python -c "import agents.runtime.preflight"`, before and after. Only the second order reaches `preflight` first and would surface a `preflight` ↔ `loop` cycle; importing `jobs` alone can leave one hidden behind an already-loaded module. No behaviour changes; the existing tests are the proof.
- [ ] Run `.venv/bin/pytest -q agents/tests agents/runtime/tests` — green, with the CLI's message assertions untouched.
- [ ] Write the queue doubles into `agents/chat/tests/_helpers.py`, patching **`agents.chat.service`**'s own names (the module under test), the way `agents/tests/_helpers.py::_patch_queue` patches the command's:
  ```python
  def _patch_queue(monkeypatch, *, on_enqueue=None, job_id=1, raises=None):
      """Patch `enqueue`/`get_job` on `agents.chat.service`.

      Seam-level, on the module under test, so the view's own call path
      runs for real. `on_enqueue` simulates the handler's writeback
      synchronously -- a double that only flipped a state without writing
      rows would make every output assertion pass against an empty
      thread (P2 review, finding M3)."""

  @pytest.fixture
  def fake_turn_queue(monkeypatch): ...
  @pytest.fixture
  def fake_queue_down(monkeypatch):
      """`enqueue` raises `QueueUnavailable` -- the unmigrated-window
      case `models/contracts/queue.py:83` names."""
  ```
  Import `QueueUnavailable` through `models.contracts.queue` — never `models.queue.*` (import-law rule 2), the same reason `agents/tests/_helpers.py` imports its state constants from the seam after the "done"-vs-"succeeded" incident.
- [ ] Write the failing test `agents/chat/tests/test_turn_create.py` covering:
  - blank/whitespace `text` → **400**, per-field message, and **no rows created** (assert `Turn.objects.count() == 0`);
  - happy path via XHR → **202** with a JSON body carrying `turn_id`, `state == "queued"`, `position`, `priority`, `status_url`, and `status_url == reverse("chat-turn-status", args=[turn_id])`;
  - happy path **without** XHR → **302** to `chat-conversation` with `?pending=<turn_id>` (and `&connection=` preserved when one was picked) — *the no-JS path, and the reason the whole page still works with scripts off*;
  - two rows written, in one transaction: a `USER` turn `done` and an `ASSISTANT` turn `queued`, at consecutive indices;
  - `queue_job_id` stamped on the assistant turn (correlate a running job back to the turn it is answering — `tools/vision/jobs.py:323-324`'s own reason);
  - unbound role → **503**, body naming the role and carrying `setup_url`, **nothing written**;
  - a model that cannot call tools → **503**, **nothing written**;
  - `QueueUnavailable` → **503** with the "run migrations" copy and **both turn rows rolled back** (assert `Turn.objects.count() == 0`, which is what makes the transaction real rather than decorative);
  - an unexpected `enqueue` exception → logged and **503**, rows rolled back;
  - a granted-but-unregistered tool → **202** with the key named under `notes` (the turn still runs — spec section 8.3 step 3's tolerant half);
  - `GET` on the turn URL → **405**;
  - **a second turn while one is still in flight → 409**, with its own sentence, and **nothing written** (assert the turn count is unchanged). Two concurrent `agent.turn` jobs on one conversation is not a hypothetical: the form is still on the page while the first answer is pending, and a double-submit or an impatient second message is the ordinary way it happens. `Turn.next_index` is a read-then-write whose safety `agents/models.py` justifies *precisely* by "a turn is enqueued only after the previous one finished" — this refusal is what makes that sentence true for the page, as it already is for the CLI (which is synchronous and cannot overlap with itself);
  - **a genuine index collision → 503 with an honest sentence, not a 500.** `uniq_turn_index` turns a real race into an `IntegrityError`, which is the outcome `agents/models.py::next_index` says it wants — "an honest `IntegrityError` rather than a silently reordered conversation". Caught by name, separately from the broad `except`, so its message can say what actually happened rather than "couldn't add your message to the queue";
  - a POST to a conversation whose agent was disabled since the page loaded → 503 naming it, nothing written.
- [ ] Write `agents/chat/service.py`:
  ```python
  """Starting a turn: preflight, two rows, one enqueue.

  HTTP-FREE ON PURPOSE. It returns a `TurnStart` -- a status number and a
  sentence -- and the views translate that into a 202, a redirect, or a
  503. Two callers need it (the index's "start a conversation with a
  message" and the thread's message form), and a rule that lived in one
  view and was called from the other would be the drift this whole
  design keeps naming.

  It follows `manage.py agent_turn` step for step, because that command
  is this page's REFERENCE CLIENT and its exit codes are this page's
  states: preflight BEFORE anything is written, the USER turn and the
  placeholder ASSISTANT turn in one transaction, `enqueue`, then the
  `queue_job_id` stamp.
  """
  from __future__ import annotations

  import logging
  from dataclasses import dataclass

  from django.db import IntegrityError, transaction
  from django.urls import reverse

  from agents.models import Turn
  from agents.runtime.preflight import preflight_turn
  from models.contracts.queue import QueueUnavailable, enqueue, get_job

  logger = logging.getLogger(__name__)

  TITLE_MAX = 60

  # The poller's tuning, declared ONCE and in Python. `thread.py` hands
  # these to the template and `turns.py` reads them for its own tests; a
  # copy typed into the template would be a second number nobody diffs.
  # Values from `tools/rag/templates/rag/ask.html:355-359`, the poller
  # this one mirrors.
  POLL_INTERVAL_MS = 2000
  MAX_TRANSPORT_RETRIES = 3
  MAX_POLL_DURATION_MS = 10 * 60 * 1000

  _BLANK = "Say something: the message cannot be blank."
  _QUEUE_UNAVAILABLE = (
      "The queue isn't ready yet — run database migrations, then try again."
  )
  _ENQUEUE_FAILED = "Couldn't add your message to the queue — nothing was queued."
  _AGENT_DISABLED = (
      "This conversation's agent is no longer enabled, so it cannot take another "
      "turn. Its history stays readable."
  )
  _IN_FLIGHT = (
      "This conversation is still working on the previous message. Wait for that "
      "answer before sending another."
  )
  _COLLIDED = (
      "Another message reached this conversation at the same moment, so nothing "
      "was queued. Send it again."
  )


  @dataclass(frozen=True)
  class TurnStart:
      """What happened, in transport-free terms.

      `status` is an HTTP number because both callers are views and
      inventing a parallel vocabulary for them to translate would buy
      nothing. Everything else here is data the view renders.
      """

      ok: bool
      status: int
      error: str = ""
      setup_url: str = ""
      notes: tuple[str, ...] = ()
      turn: object | None = None
      job_id: int | None = None
      position: int | None = None
      priority: int | None = None


  def start_turn(conversation, text: str, *, connection: str = "") -> TurnStart:
      """Queue one turn for `conversation`, or refuse without writing."""
      message = (text or "").strip()
      if not message:
          return TurnStart(False, 400, _BLANK)

      agent = conversation.agent
      if not agent.enabled:
          return TurnStart(False, 503, _AGENT_DISABLED,
                           setup_url=reverse("inference-console"))

      # ONE TURN AT A TIME PER CONVERSATION. The form is still on the
      # page while an answer is pending, so a double-submit or an
      # impatient second message is the ordinary way two `agent.turn`
      # jobs end up racing on one thread -- and `Turn.next_index` is a
      # read-then-write whose safety `agents/models.py` justifies
      # PRECISELY by "a turn is enqueued only after the previous one
      # finished". This refusal is what makes that sentence true for the
      # page, as it already is for the synchronous CLI. 409, not 400:
      # nothing about the message is wrong, and the same message will be
      # accepted in a moment.
      if Turn.objects.filter(
          conversation=conversation, role=Turn.Role.ASSISTANT,
          state__in=(Turn.State.QUEUED, Turn.State.RUNNING),
      ).exists():
          return TurnStart(False, 409, _IN_FLIGHT)

      check = preflight_turn(agent, connection)
      if not check.ok:
          return TurnStart(False, 503, check.message,
                           setup_url=reverse("inference-console"))

      notes = tuple(
          f"{key} is not available on this install, so this turn runs without it."
          for key in check.dropped_tools
      )

      # BOTH ROWS BEFORE THE ENQUEUE, in one transaction. The placeholder
      # is the durable side-effect that predates the job -- the row
      # `on_turn_terminal` exists to fix up -- and writing it after the
      # enqueue would open a window in which a cancel finds nothing to
      # flip. The `atomic` block is what makes the rollback below real:
      # a failed enqueue must leave no half-written thread.
      try:
          with transaction.atomic():
              _title_if_unset(conversation, message)
              Turn.objects.create(
                  conversation=conversation, index=Turn.next_index(conversation),
                  role=Turn.Role.USER, text=message, state=Turn.State.DONE,
              )
              placeholder = Turn.objects.create(
                  conversation=conversation, index=Turn.next_index(conversation),
                  role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
              )
              payload = {
                  "conversation": str(conversation.id),
                  "turn": placeholder.pk,
                  "agent": agent.slug,
                  "text": message,
                  # A pk as a STRING, matching both existing precedents
                  # (`tools/rag/jobs.py:9-10`, `tools/vision/jobs.py:11-24`)
                  # -- the planner and the handler re-resolve it fresh.
                  "connection": str(connection) if connection else None,
                  # One legal value. Flow-as-turn is deviation P3-D4.
                  "mode": "chat",
              }
              job_id = enqueue("agent.turn", payload)
      except QueueUnavailable:
          return TurnStart(False, 503, _QUEUE_UNAVAILABLE,
                           setup_url=reverse("inference-console"))
      except IntegrityError:
          # `uniq_turn_index` firing: two writers reached the same index,
          # which is exactly the honest failure `agents/models.py::
          # next_index` says it prefers to a silently reordered
          # conversation. Caught BY NAME and before the broad clause, so
          # the sentence can say what actually happened instead of
          # blaming the queue for a collision the queue never saw.
          logger.info("chat: turn index collision in conversation %s", conversation.id)
          return TurnStart(False, 503, _COLLIDED)
      except Exception:  # noqa: BLE001 -- log detail, then degrade to a clean 503
          logger.exception("chat: failed to enqueue an agent.turn job")
          return TurnStart(False, 503, _ENQUEUE_FAILED,
                           setup_url=reverse("inference-console"))

      # Stamped right after the enqueue, while the job is still running,
      # for the same reason `GenerationJob` gets its stamp right after
      # `submit_job` returns (`tools/vision/jobs.py:323-324`): without it
      # nothing links a running queue job back to the turn it is
      # answering. `agents.runtime.audit.close_open_invocations` (Task 2)
      # also correlates on it.
      Turn.objects.filter(pk=placeholder.pk).update(queue_job_id=job_id)

      status = _job_facts(job_id)
      return TurnStart(True, 202, notes=notes, turn=placeholder, job_id=job_id,
                       position=getattr(status, "position", None),
                       priority=getattr(status, "priority", None))


  def _title_if_unset(conversation, message: str) -> None:
      """The title, from the first ~60 characters of the first user turn.

      NEVER generated by a model: that would be a second, invisible model
      call per conversation (spec section 7.2).
      """
      if not conversation.title:
          conversation.title = message[:TITLE_MAX]
          conversation.save(update_fields=["title"])


  def conversation_url(conversation, *, connection: str = "", pending=None) -> str:
      """THE ONE thread-URL builder.

      `conversations.py` (after a start) and `turns.py` (after a no-JS
      POST) both need "the thread, carrying the pick, and possibly a
      turn to watch", and two builders would be two chances to drop one
      of the two keys. Lives here rather than in either view module
      because both already import this one, and `thread.py` must stay
      importable without either (M1's one-way direction).

      The pick lives in the QUERY STRING, not a session and not a column
      (deviation P3-D7) -- `tools/vision/views.py::_create_url`'s own
      rule: a link is then a complete description of what the page will
      show, so the chooser, the redirect after a submission, and a
      bookmark all agree.
      """
      from urllib.parse import urlencode

      url = reverse("chat-conversation", args=[conversation.id])
      query = {k: v for k, v in (("connection", connection),
                                 ("pending", pending)) if v}
      return f"{url}?{urlencode(query)}" if query else url


  def _job_facts(job_id):
      """The queue's own view of the job just enqueued, or `None`.

      A position we cannot read is a nicety; the turn is queued either
      way, and a 503 for a failed status read would throw away work the
      queue has already accepted -- `tools/vision/views.py::_queue_status`
      makes the same call for the same reason.
      """
      try:
          return get_job(job_id)
      except Exception:  # noqa: BLE001 -- the enqueue already succeeded
          logger.info("chat: could not read queue job %r right after enqueuing it", job_id)
          return None
  ```
- [ ] Write `agents/chat/views/turns.py`'s `turn_create` (`turn_status` lands in Task 10):
  ```python
  @require_POST
  def turn_create(request, conversation_id):
      """POST /chat/c/<uuid>/turn/ -- queue one turn.

      TWO ANSWERS, ONE PATH. With JS: 202 and a JSON body carrying the
      `status_url` the poller must use -- never a URL the script builds
      itself. Without JS: a redirect to the thread carrying
      `?pending=<turn_id>`, which the page renders as a pending turn and
      the operator refreshes. `tools/vision/views.py::generate` answers
      exactly this way and for exactly this reason.

      A 400 or a 503 for an XHR renders the message into the page's
      `#turn-errors` slot; a non-XHR one RE-RENDERS THE WHOLE PAGE with
      the banner, never a bare fragment -- the counterpart of
      `tools/vision/views.py:836`'s own named test.
      """
  ```
  with an `_is_xhr(request)` helper copied from `tools/vision/views.py:614-618` (a five-line reading of one header; a shared copy across a column boundary would be an import the law forbids). It builds no URL of its own: it calls `service.conversation_url(conversation, connection=..., pending=...)`, the same builder `conversation_start` uses, so the two redirects cannot disagree about which keys they carry.
- [ ] **`agents/chat/service.py` already exists** — Task 6 created it holding `conversation_url`, and Task 8 added the three poller constants. This task adds `TITLE_MAX`, `TurnStart`, and `start_turn` to it; it does not create the module and must not re-declare what is there.
- [ ] Give `conversation_start` its second half: when `text` is non-blank, call `start_turn` on the new conversation and redirect with `?pending=<turn_id>`; when `start_turn` refuses, **delete the just-created empty conversation** and re-render the index with the banner, so a refused start never leaves an empty thread in the list. Pin that with a test.
- [ ] Add `"agents/runtime/preflight.py"` to `RUNTIME_MODULES` in `foundation/ops/tests/test_column_boundaries.py`, in this task for the reason Task 2 gives.
- [ ] Run `.venv/bin/pytest -q agents foundation/ops` — green.
- [ ] Run the **full gate matrix**. Green. Record the count.
- [ ] Update `agents/chat/README.md` (the POST contract, the two answers, the transaction, the one-turn-at-a-time rule) and `agents/runtime/README.md` (`preflight.py`, and that it is *not* the loop's own gate).
- [ ] Commit: `feat(chat): posting a turn — one shared preflight, two rows, 202 or redirect`.

---

### Task 10: `turn_status` and the poller

The 202's other half. `turn_status` is the never-500 status view; the inline script is progressive enhancement over a page that already works without it.

**The single most important rule in this task is deviation P3-D8: `turn_status` reports `Turn.State` values, never queue states.** The queue's vocabulary is `queued`/`running`/**`succeeded`**/`failed`/`cancelled` (`models/contracts/queue.py:69-80`); a turn's is `queued`/`running`/**`done`**/`failed`/`cancelled` (`agents/models.py::Turn.State`). P2's ledger records a live 960-second hang caused by exactly this drift — the CLI polled for `"done"` against the queue and never saw it. So: the **turn row is read first** and is the source of truth for `state`; the queue is consulted only to *enrich* a non-terminal turn with a position or a progress dict; and the guard is **server-side** — a test pins that `_BODY_BUILDERS` is total over `Turn.State`, so a sixth state fails where the bodies are decided rather than in a script nobody diffs. No claim is made about the JS's own vocabulary: it reads whatever `state` the body carries.

Reading the durable row first is also the vision lesson (`tools/vision/views.py::queue_job_status`: "THE GENERATION FIRST, the queue second"), for a concrete reason — a queue row is pruned to `JobSettings.retention_limit` on every enqueue, so a finished job can vanish from under a card that is still polling. The turn row never does.

**Files:**
- `agents/chat/views/turns.py` (`turn_status`), `views/__init__.py`, `urls.py`
- `agents/chat/templates/chat/_turn_block.html` (new — the shared card loop)
- `agents/chat/templates/chat/conversation.html` (the inline `<script>`, and it includes the new fragment)
- `agents/chat/tests/test_turn_status.py` (new)

**Interfaces (exact signatures):**

```python
# agents/chat/views/turns.py
# The three poller constants are NOT here: they live in
# `agents/chat/service.py` (Task 8), because `views/thread.py` hands them
# to the template and the package's import direction is one-way
# (`views/__init__.py`). This module imports them from there like
# everyone else.
def turn_status(request, turn_id: int) -> JsonResponse: ...
```

Per-state bodies (the closed set):

| `Turn.State` | body |
|---|---|
| `queued` | `{"state", "position", "priority"}` |
| `running` | `{"state", "progress", "step", "label"}` — `progress` verbatim; `step` is `progress["done"]`; `label` is `progress["label"]` |
| `done` | `{"state", "html"}` — **every card this job wrote**, not one (see below) |
| `failed` | `{"state", "error", "setup_url"}` |
| `cancelled` | `{"state", "error"}` — **deviation P3-D9**: §8.3 says `{"state"}` alone, but `on_turn_terminal` writes a sentence onto the row and dropping it would render a blank card while the row holds the explanation |

**M6 — the `done` body is the whole job's output, not one card.** A finished turn is the TOOL cards the loop wrote *plus* the assistant answer. A poller that swapped in only the answer would leave every tool call invisible until a manual refresh — the page would silently show less than the same page shows after F5, which is the worst kind of difference between two renders of one thing. `_done_body` therefore calls `thread_cards(conversation, queue_job_id=turn.queue_job_id)` and renders the whole block; the script replaces the pending card with it. The USER turn carries no `queue_job_id` (the view wrote it before any job existed), so the block is exactly the job's own output and never a duplicate of the message that provoked it.

**Steps:**

- [ ] Write the failing test `agents/chat/tests/test_turn_status.py`:
  ```python
  """`turn_status` -- always 200 for a readable turn.

  NO FARABUNKER_FEATURES OVERRIDE (see test_mount.py).
  """
  from __future__ import annotations

  import json

  import pytest
  from django.urls import reverse

  from agents.chat.tests._helpers import make_conversation, make_turn
  from agents.models import Turn
  from models.contracts.queue import QueueUnavailable

  pytestmark = pytest.mark.django_db


  def _status(client, turn):
      response = client.get(reverse("chat-turn-status", args=[turn.pk]))
      return response.status_code, json.loads(response.content)


  class TestTheStateVocabulary:
      def test_it_reports_turn_states_and_never_queue_states(self, client, monkeypatch):
          """DEVIATION P3-D8, and the reason it is a deviation: the queue
          says "succeeded" and a turn says "done"
          (`models/contracts/queue.py:69-80` vs `Turn.State`). P2's
          ledger records a live 960-second hang from exactly this drift.
          Asserted against `Turn.State.DONE` itself rather than a literal
          typed a second time."""
          conversation = make_conversation()
          turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                           text="the answer", state=Turn.State.DONE)
          code, body = _status(client, turn)
          assert code == 200
          assert body["state"] == Turn.State.DONE.value
          assert body["state"] != "succeeded"

      def test_the_five_states_are_the_only_ones_it_can_report(self, client):
          """Anti-vacuous pin: a sixth `Turn.State` added later must fail
          HERE, where the body shapes are decided, rather than silently
          falling through to a done body with no html in it."""
          from agents.chat.views.turns import _BODY_BUILDERS

          assert set(_BODY_BUILDERS) == {s.value for s in Turn.State}


  class TestPerState:
      def test_queued_reports_its_place_in_the_line(self, client, fake_queued_job):
          turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                           queue_job_id=1)
          _code, body = _status(client, turn)
          assert body["state"] == "queued" and body["position"] == 3

      def test_running_reports_the_progress_dict_the_job_wrote(self, client,
                                                              fake_running_job):
          """`{"done","total","unit","label"}` -- `report_progress`'s own
          shape, passed through unchanged, exactly as
          `AskJobStatusView`'s running body does."""
          turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.RUNNING,
                           queue_job_id=1)
          _code, body = _status(client, turn)
          assert body["progress"]["unit"] == "items"
          # `step` and `label` are lifted OUT of the dict as well as
          # passed through in it (spec section 8.3): the script shows
          # them without having to know the progress dict's shape, and
          # `/queue/` keeps rendering the same dict unchanged.
          assert body["step"] == body["progress"]["done"]
          assert body["label"] == "thinking"

      def test_done_carries_the_rendered_card(self, client):
          """The SAME `_turn_card.html` the page rendered inline, from
          the SAME `rendering.thread_cards` -- so a polled answer and a
          refreshed one are byte-identical."""
          conversation = make_conversation()
          turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                           text="the answer", state=Turn.State.DONE)
          _code, body = _status(client, turn)
          assert "the answer" in body["html"]

      def test_done_carries_the_tool_cards_this_job_wrote_too(self, client):
          """M6. A finished turn is the TOOL cards the loop wrote PLUS
          the answer. A poller that swapped in only the answer would show
          strictly less than the same page shows after a refresh --
          silently, and only for the operator who waited rather than
          reloading."""
          conversation = make_conversation()
          make_turn(conversation=conversation, role=Turn.Role.USER,
                    text="the question", state=Turn.State.DONE)
          make_turn(conversation=conversation, role=Turn.Role.TOOL,
                    text="two results", state=Turn.State.DONE, queue_job_id=1,
                    tool_call={"tool": "rag.search", "args": {}, "agent": "general",
                               "id": "", "discarded": []})
          turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                           text="the answer", state=Turn.State.DONE, queue_job_id=1)
          _code, body = _status(client, turn)
          assert "two results" in body["html"]
          assert "the answer" in body["html"]
          # And NOT the message that provoked it: the USER turn carries no
          # queue_job_id, so it can never be duplicated into the block.
          assert "the question" not in body["html"]

      def test_a_done_turn_with_no_job_stamp_renders_itself_alone(self, client):
          """N4. `thread_cards(conversation, queue_job_id=None)` means
          "the whole thread", so a turn whose stamp never landed would
          swap the ENTIRE conversation into the page in place of one
          card. Silent duplication, not an error -- which is why the
          guard is explicit and why this test exists."""
          conversation = make_conversation()
          make_turn(conversation=conversation, role=Turn.Role.USER,
                    text="the question", state=Turn.State.DONE)
          turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                           text="the answer", state=Turn.State.DONE,
                           queue_job_id=None)
          _code, body = _status(client, turn)
          assert body["html"].count('class="turn ') == 1
          assert "the question" not in body["html"]

      def test_a_delegates_steps_are_nested_in_the_polled_block_too(self, client):
          """Anti-vacuous pin on reusing `thread_cards` rather than
          looping over `turn_card`: the grouper is what nests depth-1
          turns, and a hand-rolled loop in the poll view would render
          them flat -- so a polled thread and a refreshed one would
          disagree about structure while agreeing about text."""

      def test_failed_carries_the_error_and_a_setup_url_on_every_failure(self, client):
          """`setup_url` on EVERY failure, not only model-shaped ones --
          `AskJobStatusView`'s own rule (`tools/rag/views.py:1177-1185`):
          the simplest honest rule, and it never depends on the stored
          error's exact wording staying stable."""
          turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.FAILED,
                           error="the worker stopped responding")
          _code, body = _status(client, turn)
          assert body["error"] == "the worker stopped responding"
          assert body["setup_url"] == reverse("inference-console")

      def test_cancelled_says_so(self, client):
          turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.CANCELLED,
                           error="Cancelled from the queue before it ran.")
          _code, body = _status(client, turn)
          assert body["state"] == "cancelled"


  class TestTheTwoNon200s:
      def test_an_unknown_turn_is_404(self, client):
          response = client.get(reverse("chat-turn-status", args=[999999]))
          assert response.status_code == 404

      def test_an_unreadable_queue_is_503_for_a_turn_that_needs_it(self, client,
                                                                  monkeypatch):
          """The unmigrated-window tolerance `AskView` gives its own
          enqueue. Only for a turn still in flight: a finished turn needs
          no queue read at all."""
          monkeypatch.setattr(
              "agents.chat.views.turns.get_job",
              lambda job_id: (_ for _ in ()).throw(QueueUnavailable("no tables")),
          )
          turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                           queue_job_id=1)
          assert client.get(
              reverse("chat-turn-status", args=[turn.pk])
          ).status_code == 503

      def test_a_finished_turn_answers_without_consulting_the_queue_at_all(
          self, client, monkeypatch
      ):
          """THE VISION LESSON (`tools/vision/views.py::queue_job_status`,
          "THE GENERATION FIRST, the queue second"): a queue row is pruned
          to `JobSettings.retention_limit` on every enqueue, so a
          succeeded job can vanish from under a card still polling it.
          The turn row is the durable record and never does."""
          def _boom(job_id):
              raise AssertionError("the queue must not be consulted for a done turn")

          monkeypatch.setattr("agents.chat.views.turns.get_job", _boom)
          turn = make_turn(role=Turn.Role.ASSISTANT, text="a",
                           state=Turn.State.DONE, queue_job_id=1)
          assert _status(client, turn)[0] == 200

      def test_a_queued_turn_whose_job_vanished_still_reports_queued(self, client,
                                                                    monkeypatch):
          """Honest rather than clever: the ROW says queued, so the body
          says queued, with nothing to say about position. The poller's
          own 10-minute ceiling ends the wait."""
          monkeypatch.setattr("agents.chat.views.turns.get_job", lambda job_id: None)
          turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                           queue_job_id=1)
          _code, body = _status(client, turn)
          assert body["state"] == "queued" and body["position"] is None
  ```
  Add `fake_queued_job` / `fake_running_job` to `agents/chat/tests/_helpers.py`, patching `agents.chat.views.turns.get_job` and returning a `JobStatus`-shaped `SimpleNamespace` with the **real** field names — reuse `agents/tests/_helpers.py::_finished_job`'s shape so a change to `models/queue/backend.py::JobStatus` breaks the double rather than being quietly agreed with.
- [ ] Run it; read the failures.
- [ ] Write `turn_status` in `agents/chat/views/turns.py`:
  ```python
  def turn_status(request, turn_id: int):
      """GET /chat/turns/<id>/ -- the thread's poll target.

      ALWAYS 200 FOR A READABLE TURN. Queued, running, done, failed and
      cancelled all report their state in the response BODY, never the
      HTTP status -- this platform's never-500 convention, and
      `tools/rag/views.py::AskJobStatusView:1150-1160`'s rule verbatim.
      Exactly two non-200s exist: 404 for a turn id that does not exist,
      and 503 for a queue that cannot be read at all.

      IT REPORTS `Turn.State` VALUES, NEVER QUEUE STATES (deviation
      P3-D8). The queue says `succeeded`; a turn says `done`. P2's ledger
      records a live 960-second hang from exactly that drift, and the
      only defence against a second one is that this view reads the TURN
      and hands its own `state` through untranslated.

      THE ROW FIRST, THE QUEUE SECOND -- and the queue only for a turn
      still in flight. `tools/vision/views.py::queue_job_status` explains
      why in full: a queue row is pruned to `JobSettings.retention_limit`
      on every enqueue, so a finished job can vanish from under a card
      that is still polling. The turn row is the durable record.

      SECURITY NOTE, INHERITED NOT INTRODUCED: a `Turn` pk is a
      sequential integer, so this URL has the same enumeration exposure
      `GET /rag/ask/jobs/<job_id>/` already has on an unauthenticated box
      (spec section 14 gap 4). Recorded, not fixed here.
      """
  ```
  Structure it as a `_BODY_BUILDERS` dict keyed on `Turn.State` values, so the anti-vacuous test above can assert the mapping is total and a sixth state fails loudly:
  ```python
  _BODY_BUILDERS = {
      Turn.State.QUEUED.value: _queued_body,
      Turn.State.RUNNING.value: _running_body,
      Turn.State.DONE.value: _done_body,
      Turn.State.FAILED.value: _failed_body,
      Turn.State.CANCELLED.value: _cancelled_body,
  }
  ```
  `_done_body` renders through:
  ```python
  def _done_body(turn, request) -> dict:
      """The whole block this job wrote, not one card (M6).

      A finished turn is the TOOL cards the loop wrote PLUS the answer,
      and a poller that swapped in only the answer would show strictly
      less than the same thread shows after F5 -- silently, and only for
      the operator who waited rather than reloading.

      A turn with NO `queue_job_id` renders itself alone. That is not a
      degenerate case to be clever about: `thread_cards(conversation,
      queue_job_id=None)` means "the whole thread", so a stampless turn
      would swap the ENTIRE conversation into the page in place of one
      card. The guard is explicit rather than relying on the filter,
      because the failure it prevents is silent duplication rather than
      an error.
      """
      cards = (
          thread_cards(turn.conversation, queue_job_id=turn.queue_job_id)
          if turn.queue_job_id is not None
          else [turn_card(turn)]
      )
      return {
          "state": turn.state,
          "html": render_to_string(
              "chat/_turn_block.html", {"cards": cards}, request=request,
          ),
      }
  ```
  — the **same** `thread_cards`, the **same** `_turn_card.html`, and the **same** `_turn_block.html` the page itself loops with, which is what makes a polled thread and a refreshed one identical rather than merely similar.
- [ ] Write `agents/chat/templates/chat/_turn_block.html` — three lines, and the reason it exists rather than a loop repeated twice:
  ```html
  {% comment %}
  A run of cards. Rendered by conversation.html for the whole thread and
  by `turn_status`'s `done` body for the block one job wrote (M6), so the
  polled thread and the refreshed one come out of the SAME loop over the
  same fragment. Two copies of `{% for %}{% include %}{% endfor %}` is
  exactly how they would come to disagree about nesting or wrappers.
  {% endcomment %}
  {% for card in cards %}{% include "chat/_turn_card.html" %}{% endfor %}
  ```
  and change `conversation.html` to `{% include "chat/_turn_block.html" %}` in place of its own loop.
- [ ] Add the route and export the name.
- [ ] Write the inline `<script>` at the bottom of `chat/conversation.html`. It is **progressive enhancement only** — every rule below exists because the page must already work without it:
  ```html
  {% block scripts %}
  <script>
  // Progressive enhancement ONLY. The form is a plain POST and the answer
  // is a redirect carrying `?pending=<turn_id>`; everything below just
  // spares the operator the refresh. No external assets.
  //
  // Tuning copied from tools/rag/templates/rag/ask.html:355-359, which is
  // the poller this one mirrors: 2000 ms, three TRANSPORT retries (fetch
  // itself rejecting -- a reachable non-200 is handled inline), and a
  // ten-minute ceiling so a stuck poll can never run forever in a
  // background tab.
  (function () {
    var POLL_INTERVAL_MS = {{ poll_interval_ms }};
    var MAX_TRANSPORT_RETRIES = {{ max_transport_retries }};
    var MAX_POLL_DURATION_MS = {{ max_poll_duration_ms }};
    // ... poll(statusUrl, card): on `done` replace the card's outerHTML
    //     with data.html -- which is the whole BLOCK this job wrote (the
    //     tool cards and the answer), not one card, so the polled thread
    //     matches a refreshed one; on `failed`/`cancelled` render
    //     data.error plus
    //     data.setup_url; on `queued`/`running` update the pending note
    //     and re-arm; on 404 say the turn is gone; on 503 say the queue
    //     is not ready and stop. An UNRECOGNIZED state keeps polling
    //     rather than stranding the page -- ask.html's own rule for a
    //     forward-compatible state added later.
    // ... submit handler: preventDefault, fetch(form.action) with
    //     `new FormData(form)` (which carries csrfmiddlewaretoken, so no
    //     X-CSRFToken header is needed -- vision/create.html:257-260 does
    //     exactly this) and X-Requested-With; on 202 append a pending
    //     card and poll `data.status_url` -- NEVER a URL built here; on
    //     4xx/5xx render the body into #turn-errors and NEVER into the
    //     thread, so a rejected message can never be mistaken for a sent
    //     one.
    // ... bootstrap: if the wrapper carries data-pending-turn, poll it
    //     immediately. That is what makes the no-JS redirect and the JS
    //     path converge on one behaviour.
  })();
  </script>
  {% endblock %}
  ```
  The three constants come from the view's context (`POLL_INTERVAL_MS`, `MAX_TRANSPORT_RETRIES`, `MAX_POLL_DURATION_MS` in **`agents/chat/service.py`**, put into the context by `thread_context`) so the numbers are declared **once**, in Python, and a test can assert the page carries them rather than a second copy typed into a template.
- [ ] Add to `agents/chat/tests/test_thread.py`:
  ```python
  def test_the_page_carries_the_polling_constants_from_python(self, client):
      """Declared once, in the view, so the page and its tests can never
      disagree about the ceiling."""

  def test_the_pending_turn_id_from_the_no_js_redirect_reaches_the_script(self, client):
      """`?pending=<id>` is what makes the JS-off redirect and the JS-on
      poller converge: the page renders the turn as pending either way,
      and with a script running it starts watching it immediately."""
  ```
- [ ] Run `.venv/bin/pytest -q agents/chat` — green.
- [ ] Run the **full gate matrix**. Green. Record the count.
- [ ] Update `agents/chat/README.md` with the per-state body table and a **Turn states are not queue states** paragraph naming the 2026-08-28 incident.
- [ ] Commit: `feat(chat): the poll view and its progressive-enhancement script`.

---

### Task 11: the error surfaces, and deleting a conversation

Every row of spec §10.1 reproduced on the page, plus the one lifecycle action the surface needs. Most of the behaviour already exists after Tasks 9–10; this task makes it *visible*, gives it shared copy, and pins the paths that are easy to get subtly wrong.

**Files:**
- `agents/chat/templates/chat/_unavailable.html`, `_form_errors.html` (new)
- `agents/chat/views/conversations.py` (`conversation_delete`), `views/thread.py` (the banner), `views/__init__.py`, `urls.py`
- `agents/chat/tests/test_errors.py`, `agents/chat/tests/test_delete.py` (new)
- `agents/chat/README.md`

**Steps:**

- [ ] Write `agents/chat/tests/test_errors.py`, one class per §10.1 row that has a page surface:
  - **unbound chat role** — the thread page still renders 200 with a banner naming the role and linking to `{% url 'inference-console' %}` **and** `/setup/`; the POST is 503 with the same sentence. *Two different responses for one condition, and that is the point: reading is not queueing.*
  - **model cannot call tools** — same shape, different sentence, and the banner names the role rather than any model.
  - **queue unavailable** — the POST is 503 with the migrations copy; the thread page still renders (a queue outage does not hide history).
  - **tool dropped as unregistered** — 202 with a note; the note appears on the page after the redirect too.
  - **a failed turn** — the card shows `Turn.error` verbatim, no traceback, plus the setup link.
  - **a cancelled turn** — the card shows the sentence `on_turn_terminal` wrote (`"Cancelled from the queue before it ran."`), which is a real end-to-end pin that the hook's copy and the page's rendering agree.
  - **XHR vs non-XHR** — a 400 for an XHR returns the `_form_errors.html` fragment; a 400 without XHR returns **the whole page** with the message, not a bare fragment (the counterpart of `tools/vision/views.py:836`'s own named test).
  - **never a traceback anywhere** — assert `"Traceback"` and `"File \""` appear in no error body.
- [ ] Write `agents/chat/templates/chat/_unavailable.html` and `_form_errors.html`, both modelled on their vision counterparts (`tools/vision/templates/vision/_unavailable.html`, `_form_errors.html`) — a banner usable as an XHR fragment or inside a full page render, carrying a link to the model console.
- [ ] Add the banner to `thread_context`: run `preflight_turn` on GET and put `check.message` into the context as `unavailable`, with a comment saying explicitly that this is a **banner, not a status code** — reading a thread is never refused.
- [ ] Write `conversation_delete`:
  ```python
  @require_POST
  def conversation_delete(request, conversation_id):
      """POST /chat/c/<uuid>/delete/ -- remove one thread.

      The TURNS go with it (`Turn.conversation` is CASCADE). The AUDIT
      DOES NOT: `Turn.invocation` is `SET_NULL`, so every `ToolInvocation`
      this conversation produced survives, principal and outcome intact.
      That asymmetry is deliberate and is the 2026-08-27 addendum's
      consequence 3 doing its job -- the audit row is not owned by the
      conversation table, precisely so deleting a conversation cannot
      erase the record of what was called.

      The AGENT is untouched: `Conversation.agent` is `PROTECT` in the
      other direction only.
      """
  ```
  and a `<details>` confirm control in `conversation.html` modelled on `tools/vision/templates/vision/_delete_control.html` (a disclosure, then a real POST — no JS confirm dialog).
- [ ] Write `agents/chat/tests/test_delete.py`: the conversation and its turns are gone; **the `ToolInvocation` rows survive with their `outcome` and `principal_key` intact** (the pin that makes the SET_NULL choice a decision rather than a default); the redirect lands on the index; a `GET` is 405; an unknown id is 404.
- [ ] Run `.venv/bin/pytest -q agents/chat` — green.
- [ ] Run the **full gate matrix**. Green. Record the count.
- [ ] Update `agents/chat/README.md` with the §10.1 mapping table (failure → where it is caught → what the operator sees) and the delete asymmetry.
- [ ] Commit: `feat(chat): honest error surfaces, and deleting a conversation without erasing its audit`.

---

### Task 12: `agents/runtime/flow.py` — the resolver and the runner

The flow's execution half. Every step goes through **the same `invoke_tool`** the ReAct loop uses — same validation floor, same audit row, same budget, same depth, same five outcome classes — which is what makes "a flow is the tool contract with no LLM" true rather than aspirational.

**Ruling 1: the runner loads a ROW.** `run_flow` reads `args["flow"]` — a slug the model chose from the enumerated `choices` the prompt builder filled (Task 13) — and fetches it through `agents.visibility.visible_flows(principal)`. It does **not** consult the shipped catalogue: `agents/defaults.py` describes what the platform *offers* and validates row JSON, and a runner that could execute a catalogue entry would make an uninstalled offer runnable and give the row a rival source of truth.

The row's `inputs`/`steps` are JSON, so the runner parses them into the same `FlowInput`/`FlowStep` dataclasses `agents/defaults.py` validates — `parse_flow_json(inputs, steps)`, one parser shared by the validator and the runner. A row and a shipped default are then read by identical code, which is the only way "the same rules apply to both" stays true.

Five decisions this task settles:

1. **A failed step ends the flow, with the right class.** §6.5: "A step failure ends the flow (no per-step recovery: a flow is a declared sequence, and a model is not present to reinterpret it)." A step that was **refused** re-raises `ToolRefused` — nothing the calling model can say fixes an unbound role or a feature that is off, so it gets no retry (§10.1's own rule for refusals). Anything else raises `ValueError` naming the step, its tool, and what it said, which the outer `invoke_tool` classifies as `error` and §10.2 grants one recovery. That recovery will fail identically and end the turn honestly, which is precisely §10.2's "a second failure after being told what was wrong is not a transient".
2. **A deadline mid-flow is `degraded`, not a failure.** The steps that ran really ran, and throwing their text away because the wall clock moved would lose real work. `run_flow` returns a `ToolResult` whose text says which step it stopped at, carrying the last step's actual output, with `data["degraded"] = True` — the opt-in flag `agents/runtime/invoke.py` already classifies. **This makes the flow runner `degraded`'s first shipped producer**, which P2's Task 7 named as an open gap ("a defined-and-tested outcome class that no shipped runner produces yet").
3. **A step's tool must be registered and non-mutating, checked at run time.** Unregistered → `ToolRefused` naming it (ruling R1's tolerance: a not-here, not a fault). `mutates=True` → `ToolRefused`, and this is **load-bearing**: without it a flow would be a bypass around ADR 0010:266-276's "no settings-mutating tool is grantable", since `granted_tools` filters an *agent's* keys and never sees a flow's steps.
4. **A reference that does not resolve raises, naming itself.** §6.5, verbatim: "never `None` silently substituted, because a silently-empty prompt is exactly the failure a flow exists to prevent."
5. **An unknown, disabled, or invisible flow is `ToolRefused`, not a crash.** The model was handed an `enum` of real slugs, so a slug outside it means the row was disabled or deleted between the prompt and the call. `validate_tool_args` catches most of that by name (`operations.py:349-350`); the runner's own lookup catches the race, and refuses rather than retrying — nothing the model can say brings a deleted row back.

**Files:**
- `agents/runtime/flow.py` (new)
- `agents/defaults.py` (`parse_flow_json`, shared with the Task 5 validator)
- `agents/runtime/tests/test_flow.py` (new)

**Interfaces (exact signatures):**

```python
# agents/runtime/flow.py
class FlowReferenceError(ValueError): ...

def resolve_ref(ref: str, *, input: dict, steps: list) -> object:
    """`"$input.<key>"` -> that input value.
    `"$steps.<N>.text"` -> step N's ToolResult.text.
    `"$steps.<N>.data.<path>"` -> a dotted walk into step N's `data`,
    where an all-digit segment indexes a list.
    `"$steps.<N>.artifacts.<i>"` -> one artifact reference string.
    Raises `FlowReferenceError` naming the reference -- never `None`.

    TWO CASES THAT LOOK ALIKE AND ARE NOT (M7). An UNDECLARED input key
    is a declaration bug and RAISES. A DECLARED BUT UNSUPPLIED optional
    input resolves to its default and does not. The discriminator is
    membership in `input`, which is why `_run_steps` seeds that dict from
    the flow's `inputs` -- every declared key present, unsupplied ones
    holding `None` -- rather than passing the caller's args straight
    through. Without that seeding the two cases are indistinguishable and
    an optional input would raise the moment somebody left it out."""

def resolve_args(args: dict, *, input: dict, steps: list) -> dict:
    """`args` with every `$` reference replaced, at any nesting depth."""

def run_flow(args: dict, ctx) -> ToolResult:
    """THE REGISTERED RUNNER for the one `flow.run` tool (ruling 1).
    Loads the `Flow` ROW named by `args["flow"]`, through
    `visible_flows(ctx.principal)`."""

def _run_steps(flow, inputs: dict, ctx) -> ToolResult:
    """Spec section 6.5's `run_flow(flow_key, input, ctx)`, with the row
    already loaded (deviation P3-D5). `flow` is a `Flow` ROW; its
    `inputs`/`steps` JSON is parsed by
    `agents.defaults.parse_flow_json` -- the same parser the validator
    uses, so a row and a shipped default are read by identical code."""
```

**Steps:**

- [ ] Write the failing test `agents/runtime/tests/test_flow.py`. Cover, class by class:

  **`TestResolveRef`** — **an undeclared `$input.<key>` raises `FlowReferenceError` naming it, while a declared-but-unsupplied optional input resolves to its default (`None`) and does NOT raise** — the two cases M7 separates, asserted as a pair in one class so nobody later "simplifies" them back together; then `$input.topic` returns the input; `$steps.0.text` returns that step's text; `$steps.0.data.results.0.title` walks dicts and indexes a list on an all-digit segment; `$steps.0.artifacts.0` returns one reference string; an out-of-range step index, an out-of-range list index, a missing dict key, a missing input, and a malformed reference each raise `FlowReferenceError` **naming the reference** (assert the reference string appears in `str(exc)` — a resolver failure nobody can locate is barely better than a silent `None`); and the anti-vacuous pin that `resolve_ref` **never returns `None` for a miss** (`assert pytest.raises` rather than `assert result is None`).

  **`TestResolveArgs`** — a nested list/dict is walked; a non-`$` value passes through untouched; `"$5.00"` is a literal.

  **`TestRunSteps`** — with two registered stub tools under a module-local autouse fixture:
  ```python
  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      """A module-global registry surviving between tests is exactly the
      state that makes a suite pass in one collection order and fail in
      the other. Defined HERE (there is no conftest.py) with its body in
      `agents/tests/_helpers.py`, the same shape every other tool-
      registering test module uses."""
      saved = snapshot_tools()
      yield
      restore_tools(saved)
  ```
  and then:
  - both steps run, in order, and the recorded args prove the reference resolved (this is where `$steps.N` gets its end-to-end proof, since the resident flow declares none — see Task 13);
  - the returned `text` is the **last** step's text (§6.5);
  - `artifacts` are the union of every step's, order preserved, deduped;
  - `data` carries the last step's data merged with `{"flow": slug, "steps": [...]}` — so a `rag.ask` last step's `citations` reach `agents/chat/rendering.py::citations_of` unchanged, which is what makes a flow card render its sources;
  - **one `ToolInvocation` row per step** (the audit is per call, not per flow — 2026-08-27 addendum consequence 3);
  - `input` given as a **JSON string** is parsed and reaches the steps as values; `input` given as a **dict** is accepted unchanged (an engine that already parsed it is not wrong); `input` omitted entirely runs a flow whose inputs are all optional; **malformed JSON and a bare non-object both raise `ParamError` naming `input`** — the repairable class §10.2 grants one retry for, not a refusal, because a model that got its own JSON slightly wrong can fix it when told which argument failed;
  - a step raising ends the flow, the later steps do **not** run, and the message names the step index and the tool;
  - a step whose runner raises `ToolRefused` re-raises `ToolRefused` (no retry), and a step that fails any other way raises `ValueError`;
  - the deadline case: with `budget.deadline_monotonic` already passed after step 0, the result is a `ToolResult` with `data["degraded"] is True`, text naming the step it stopped at, and the completed step's output still present — **and `invoke_tool(flow_spec, ...)` over it classifies as `DEGRADED`, not `ERROR`** (drive that assertion through `invoke_tool` itself, so the opt-in flag and its classifier are proven to agree);
  - an unregistered step tool → `ToolRefused` naming the key;
  - a `mutates=True` step tool → `ToolRefused`, with a docstring saying this is the ADR 0010 bypass guard;
  - `run_flow` with `args["flow"]` naming no visible enabled row → `ToolRefused` naming the slug, **no retry**. The model was handed an `enum` of real slugs, so reaching here means the row was disabled or deleted between the prompt and the call — a race, and nothing the model can say brings it back;
  - `run_flow` against a row that is `enabled=False` → the same refusal, driven through `visible_flows` rather than a second `enabled` check in the runner, so the tool list and the runner can never disagree about which flows exist.

  **`TestBudget`** — the flow **spends no steps of its own**: a step is an LLM call, and a flow makes none, so `budget.steps_left` is unchanged across a flow of three steps. Pin it; it is the property that makes "one flow = one loop step" true.

- [ ] Run it; read the failure.
- [ ] Write `agents/runtime/flow.py`. Structure and the load-bearing bodies:
  ```python
  """Running a declared flow: the same tool contract, no model.

  Every step goes through the SAME `agents.runtime.invoke.invoke_tool`
  an agent's loop uses -- same validation floor, same audit row, same
  shared `StepBudget`, same depth, same five outcome classes. That is
  what makes "a flow is the tool contract with no LLM" a fact about the
  code rather than a claim about it.

  A FLOW SPENDS NO STEPS. A step of the budget is an LLM CALL, and a flow
  makes none; one whole flow is one step of whatever loop invoked it.
  What it does obey is the DEADLINE, checked before each step -- the same
  place and the same rule `agents/runtime/loop.py` checks it, and never
  DURING a step, for the post-deadline-latency reason that module's
  docstring gives in full.

  A DEADLINE MID-FLOW IS `degraded`, NOT A FAILURE. The steps that ran
  really ran; throwing their output away because the wall clock moved
  would lose work that was actually done. The result says which step it
  stopped at and carries the last completed step's own text, with
  `data["degraded"] = True` -- the opt-in flag `invoke.py` classifies.
  This module is that outcome class's first shipped producer.

  A REFERENCE THAT DOES NOT RESOLVE RAISES, NAMING ITSELF. Never `None`
  silently substituted: a silently-empty prompt is exactly the failure a
  flow exists to prevent (spec section 6.5).

  IT IMPORTS NO `tools.*` MODULE. Every step's runner is reached through
  `invoke_tool`, which resolves a dotted-path string (import-law rule 3).
  """
  ```
  ```python
  def resolve_ref(ref: str, *, input: dict, steps: list):
      """One reference, resolved against the inputs and the completed
      steps.

      An all-digit path segment INDEXES A LIST, which is what makes
      `$steps.0.data.results.0.title` -- the shape a search result
      actually has -- expressible at all.

      Every miss raises `FlowReferenceError` naming the reference AND the
      part of it that failed. A resolver failure nobody can locate is
      barely better than the silent `None` this refuses to return.

      An UNDECLARED input key raises; a DECLARED BUT UNSUPPLIED optional
      resolves to its seeded default (M7). `_run_steps` guarantees the
      distinction by seeding `input` from the flow's declaration.
      """
      body = ref[len(_REF):]
      head, _, rest = body.partition(".")
      if head == "input":
          if rest not in input:
              raise FlowReferenceError(
                  f"{ref!r} names no declared input of this flow."
              )
          return input[rest]
      if head != "steps":
          raise FlowReferenceError(f"{ref!r} is not a flow reference.")
      number, _, tail = rest.partition(".")
      if not number.isdigit() or int(number) >= len(steps):
          raise FlowReferenceError(
              f"{ref!r} names step {number!r}, but only {len(steps)} step(s) have run."
          )
      result = steps[int(number)]
      field_name, _, path = tail.partition(".")
      if field_name == "text":
          return result.text
      if field_name == "data":
          return _walk(ref, result.data, path)
      if field_name == "artifacts":
          return _walk(ref, list(result.artifacts), path)
      raise FlowReferenceError(
          f"{ref!r} reads {field_name!r}, but a step result has text, data, artifacts."
      )


  def _walk(ref: str, value, path: str):
      """A dotted walk, where an ALL-DIGIT segment indexes a list.

      That one rule is what makes `$steps.0.data.results.0.title` -- the
      shape a search result actually has -- expressible at all, and it
      costs a dict nothing: a JSON object's keys are strings, so a digit
      segment is never ambiguous against one.
      """
      if not path:
          return value
      for segment in path.split("."):
          try:
              value = value[int(segment)] if segment.isdigit() else value[segment]
          except (KeyError, IndexError, TypeError) as exc:
              raise FlowReferenceError(
                  f"{ref!r} could not be resolved: {segment!r} is not there."
              ) from exc
      return value


  def resolve_args(args: dict, *, input: dict, steps: list) -> dict:
      """`args` with every `$` reference replaced, at ANY nesting depth.

      Walks lists and dicts for the same reason
      `agents.defaults`'s own reference walk does at validation time (the
      one `validate_flow_json` runs over a row's JSON and over a
      `FlowSpec` alike): a reference one level down is still a
      reference, and the two walks must agree about what counts as one,
      or a row passes `Flow.save()` and then fails to run.
      """
      def _one(value):
          if isinstance(value, str):
              return resolve_ref(value, input=input, steps=steps) \
                  if value.startswith(_REF) and value[1:].partition(".")[0] \
                  in ("input", "steps") else value
          if isinstance(value, dict):
              return {k: _one(v) for k, v in value.items()}
          if isinstance(value, (list, tuple)):
              return [_one(v) for v in value]
          return value

      return {key: _one(value) for key, value in (args or {}).items()}
  ```
  ```python
  def _run_steps(flow, inputs: dict, ctx) -> ToolResult:
      """Spec section 6.5's `run_flow(flow_key, input, ctx)`, with the
      ROW already loaded (deviation P3-D5).

      Named `_run_steps` so the module has exactly one public runner and
      nobody has to work out which of two same-named functions a call
      reached -- the same reason `agents/runtime/jobs.py` does not
      re-export `run_turn`.

      `flow` is a `Flow` row. Its JSON is parsed into the SAME
      `FlowInput`/`FlowStep` dataclasses `agents.defaults` validates, by
      the same parser, so a row and a shipped default are read by
      identical code -- which is the only way "the same rules apply to
      both" stays true rather than being asserted twice.
      """
      from agents.contracts.tools import ToolRefused, ToolResult, get_tool
      from agents.defaults import parse_flow_json

      declared_inputs, steps = parse_flow_json(flow.inputs, flow.steps)

      # SEEDED FROM THE DECLARATION, not passed through (M7). Every
      # declared input is present, an unsupplied optional holding `None`,
      # so `resolve_ref` can tell "this flow has no such input" (a bug,
      # which raises) from "this optional was left out" (normal, which
      # resolves to the default). `invoke_tool` has already applied the
      # ToolSpec's own param defaults before this runs; this fills the
      # remaining holes rather than second-guessing that.
      inputs = {item.key: inputs.get(item.key) for item in declared_inputs} | {
          k: v for k, v in inputs.items()
          if k not in {item.key for item in declared_inputs}
      }
      results = []
      artifacts: list[str] = []
      for index, step in enumerate(steps):
          if ctx.budget.expired:
              # Checked BEFORE the step, never during one -- the same
              # rule and the same reason as `loop.run_loop`.
              return _degraded(flow, steps, index, results, artifacts)
          try:
              tool = get_tool(step.tool)
          except ValueError:
              # Ruling R1: a step naming a feature-gated or not-yet-shipped
              # tool is a NOT-HERE, not a fault -- but this particular
              # flow cannot run, and nothing the calling model says will
              # change that. ToolRefused: honest, and no retry.
              raise ToolRefused(
                  f"Flow {flow.slug!r} step {index} needs {step.tool!r}, which is not "
                  f"available on this install."
              ) from None
          if tool.mutates:
              # LOAD-BEARING. `granted_tools` filters an AGENT's keys and
              # never sees a flow's steps, so without this a flow would be
              # a bypass around ADR 0010:266-276's "registered but not
              # grantable". A declaration-time check cannot do it: whether
              # a key is mutating is a property of the REGISTERED spec.
              raise ToolRefused(
                  f"Flow {flow.slug!r} step {index} calls {step.tool!r}, which changes "
                  f"state that already exists. Such a tool is not callable until "
                  f"Identity & Auth lands."
              )
          args = resolve_args(step.args, input=inputs, steps=results)
          outcome = invoke_tool(tool, args, ctx)
          if outcome.failed:
              if outcome.bars_retry:
                  raise ToolRefused(
                      f"Flow {flow.slug!r} stopped at step {index} ({step.tool}): "
                      f"{outcome.text}"
                  )
              # No per-step recovery (section 6.5): a flow is a declared
              # sequence and no model is present to reinterpret it. The
              # CALLING loop still gets its one recovery (section 10.2),
              # which will fail identically and end the turn honestly.
              raise ValueError(
                  f"Flow {flow.slug!r} stopped at step {index} ({step.tool}): "
                  f"{outcome.text}"
              )
          results.append(outcome.result)
          artifacts.extend(outcome.result.artifacts)
      return _finished(flow, steps, results, artifacts)
  ```
  with `_finished` building the final `ToolResult`:
  ```python
  def _finished(flow, steps, results, artifacts) -> ToolResult:
      """The flow's own result.

      `text` is the LAST step's text (spec section 6.5). `data` is the
      last step's `data` MERGED UNDER the flow's own two keys -- which is
      what carries a closing `rag.ask`'s `citations` straight through to
      `agents/chat/rendering.py::citations_of`, so a flow card renders
      its sources exactly like the tool card it wraps. `artifacts` is
      every step's, deduped, first-seen order preserved, so an image
      generated mid-flow still renders even when a later step produced
      none.
      """
      last = results[-1]
      data = dict(last.data or {})
      data["flow"] = flow.slug
      data["steps"] = [
          {"tool": step.tool, "text": result.text}
          for step, result in zip(steps, results)
      ]
      return ToolResult(text=last.text, data=data,
                        artifacts=tuple(dict.fromkeys(artifacts)))


  def _degraded(flow, steps, index, results, artifacts) -> ToolResult:
      """The turn ran out of time partway through.

      `data["degraded"] = True` is the OPT-IN flag
      `agents.runtime.invoke.invoke_tool` classifies -- so this is a
      `degraded` outcome, not a failure: the steps that ran really ran,
      it costs the calling loop no recovery, and the operator sees what
      was produced rather than a card saying the whole thing failed.
      This module is that outcome class's first shipped producer.

      Carries the LAST COMPLETED step's text, and says which step it
      stopped before. With no completed step at all, it says only that --
      never a trailer claiming a step "returned" something, which is the
      same rule `loop._honest_ending` holds.
      """
      done = f" Completed {index} of {len(steps)} step(s)." if index else ""
      text = (
          f"The turn ran out of time before step {index} of flow {flow.slug!r}."
          + done
      )
      if results:
          text = f"{text}\n\n{results[-1].text}"
      data = dict(results[-1].data or {}) if results else {}
      data["degraded"] = True
      data["flow"] = flow.slug
      data["stopped_at_step"] = index
      return ToolResult(text=text, data=data,
                        artifacts=tuple(dict.fromkeys(artifacts)))
  ```
  and `run_flow` as the registered runner:
  ```python
  def run_flow(args: dict, ctx) -> ToolResult:
      """The runner behind the ONE registered `flow.run` tool (ruling 1).

      Loads the ROW named by `args["flow"]`, through
      `agents.visibility.visible_flows` -- the one place this column
      answers "which flows may this principal run" (ruling 4c). NOT the
      shipped catalogue: `agents/defaults.py` describes what the platform
      OFFERS and validates row JSON, and a runner that could execute a
      catalogue entry would make an uninstalled offer runnable and give
      the row a rival source of truth.

      `args["input"]` carries the flow's own inputs AS A JSON STRING, and
      is parsed here. That is not a compromise, it is what the schema can
      actually express: the tool contract has no object param kind and
      every kind maps to a JSON scalar, so a `"text"` param carrying JSON
      is the honest shape rather than a nested schema this contract
      cannot render. A dict is ALSO accepted unchanged -- an engine that
      hands back a parsed object should not be punished for being better
      than the wire format. Anything else, or malformed JSON, is a
      `ParamError` naming `input`, which is the repairable class section
      10.2 grants a retry for: a model that gets its own JSON slightly
      wrong can fix it when told which argument failed.

      A slug that names no visible, enabled row is `ToolRefused`, no
      retry. The model was handed an `enum` of real slugs (Task 13), so
      reaching here means the row was disabled or deleted between the
      prompt and the call -- a race, and nothing the model can say brings
      it back.
      """
      from agents.contracts.tools import ToolRefused
      from agents.visibility import visible_flows

      slug = str(args.get("flow") or "").strip()
      flow = visible_flows(ctx.principal).filter(slug__iexact=slug).first()
      if flow is None:
          raise ToolRefused(
              f"There is no enabled flow called {slug!r} on this system."
          )
      return _run_steps(flow, _parsed_input(args.get("input"), flow), ctx)


  def _parsed_input(raw, flow) -> dict:
      """`args["input"]` as a dict.

      A JSON STRING is the wire shape (see `run_flow`'s docstring: the
      tool contract has no object param kind). A dict passes through --
      an engine that already parsed it is not wrong. `None` or blank is
      an empty dict, because a flow whose inputs are all optional is
      legitimately called with none.

      `ParamError`, not `ToolRefused`: bad JSON is exactly the repairable
      failure section 10.2 grants one retry for, and `.errors` maps the
      param key to a reason the model can act on
      (`models/contracts/operations.py:228-236`).
      """
      import json

      from models.contracts.operations import ParamError

      if raw in (None, ""):
          return {}
      if isinstance(raw, dict):
          return dict(raw)
      if isinstance(raw, str):
          try:
              parsed = json.loads(raw)
          except ValueError as exc:
              raise ParamError({"input": (
                  f"Could not read the inputs for {flow.slug!r} as JSON ({exc}). "
                  f'Send an object like {{"topic": "..."}}.'
              )}) from None
          if isinstance(parsed, dict):
              return parsed
      raise ParamError({"input": (
          f"{flow.slug!r} takes its inputs as a JSON object of named values, "
          f"not a bare value."
      )})
  ```
- [ ] Add `"agents/runtime/flow.py"` to `RUNTIME_MODULES` in `foundation/ops/tests/test_column_boundaries.py`, **in this task, not Task 14.** Two guards depend on it and they fire at different moments: the `get_job` sweep the moment the file exists, and `test_every_registered_runner_lives_in_a_swept_module` the moment Task 13 REGISTERS `agents.runtime.flow.run_flow`. Adding the entry here means neither task ever leaves the suite red, and Task 13 only has to confirm the anti-vacuous pin now covers the new runner.
- [ ] Run `.venv/bin/pytest -q agents foundation/ops` — green.
- [ ] Run the **full gate matrix**. Green. Record the count.
- [ ] Update `agents/runtime/README.md` with `flow.py`, and **update P2's own note** that `degraded` has no shipped producer — it has one now, and leaving that sentence would make the docs lie about the column.
- [ ] Commit: `feat(agents): the flow runner — same tool contract, no model, honest partial results`.

---

### Task 13: `flow.run` — one tool, per-turn choices, the grant, and the planner's role walk

Wiring, and the task where ruling 1's design either holds together or does not. **One** `ToolSpec` is registered — `flow.run` — with a `flow` **choice** param whose `choices` are empty at registration and filled **per turn** from the enabled `Flow` rows this principal may run. That is what lets N rows be callable without N registrations, and it is what keeps `AppConfig.ready()` free of the database.

**The seam already exists and is already used this way.** `_input_schema` emits `enum` only for a `"choice"` param with non-empty `choices`, and deliberately emits none for one whose options the engine owns (`agents/contracts/toolschema.py:70-71`) — which is exactly the case `Param.choices` documents at `models/contracts/operations.py:45-47` ("Options an engine supplies … from `InferenceEngine.list_choices`"). P3 supplies flow slugs instead of engine options, through the same door. The registered spec stays a frozen module-level constant; the narrowing is a `dataclasses.replace` where the turn's tool list is built.

**Two consequences worth stating before the code.** First, `validate_tool_args` then rejects an unknown slug **by name** (`operations.py:349-350`) rather than the runner discovering it — the repairable failure §10.2 grants a retry for, which is the right class here because the model can simply pick a slug that exists. Second, `flow` must be `required=True`: correction §2 records that `validate_params` errors on a **blank** `"choice"` whether or not it is required (`operations.py:298-301`), so an optional flow key would be unusable rather than merely odd.

**Files:**
- `agents/runtime/flowtool.py` (new — the spec and the per-turn narrowing)
- `agents/apps.py` (one `register_tool` call)
- `agents/runtime/loop.py` (`_available_tools` narrows the flow spec)
- `agents/runtime/jobs.py` (`_tool_roles` walks flow rows)
- `agents/defaults.py` (`DEFAULT_FLOWS` gains `library-brief`), `agents/defaults.py`'s `general` gains the grant
- `agents/tests/test_apps.py`, `agents/tests/test_defaults.py`, `agents/runtime/tests/test_jobs.py`, `agents/runtime/tests/test_loop.py`
- `agents/README.md`

**Interfaces (exact signatures):**

```python
# agents/runtime/flowtool.py
FLOW_RUN_KEY = "flow.run"
FLOW_RUN = ToolSpec(key=FLOW_RUN_KEY, ..., runner="agents.runtime.flow.run_flow")

def narrowed_flow_spec(spec: ToolSpec, principal) -> ToolSpec:
    """`spec` with the `flow` param's `choices` filled from
    `visible_flows(principal)`, or `None` when there are none to offer."""
```

**Steps:**

- [ ] Write `agents/runtime/flowtool.py`:
  ```python
  """The ONE registered flow tool, and the per-turn narrowing that gives
  it real options (ruling 1, deviation P3-D11).

  ONE spec, not one per flow. A flow is a ROW, and rows cannot become
  registered specs: `AppConfig.ready()` may touch no database. What makes
  a single generic tool a good prompt anyway is that its `flow` param is
  a CHOICE whose options are filled per turn from the enabled rows -- so
  the model reads an `enum` of real slugs rather than guessing at a free
  string, which is strictly better than the `flow.<slug>` shape this plan
  originally proposed, and works for rows nobody declared in code.

  `choices=()` at registration is not a placeholder: it is the documented
  shape for a choice whose options something else supplies
  (`models/contracts/operations.py:45-47`), and `_input_schema` emits no
  `enum` for one (`agents/contracts/toolschema.py:70-71`) -- so an
  un-narrowed spec is honest about knowing nothing rather than claiming
  an empty set of flows.

  `required=True` on `flow` is forced, not stylistic: `validate_params`
  errors on a BLANK choice whether or not it is required
  (`operations.py:298-301`, the spec's own correction section 2), so an
  optional flow key would be unusable rather than merely odd.
  """
  from __future__ import annotations

  from dataclasses import replace

  from agents.contracts.tools import ToolSpec
  from models.contracts.operations import Param

  FLOW_RUN_KEY = "flow.run"

  FLOW_RUN = ToolSpec(
      key=FLOW_RUN_KEY,
      label="Run a flow",
      description=(
          "Run one of this box's saved flows: a fixed sequence of tool steps that "
          "always runs the same way, with no decisions in between. Pick the flow by "
          "name from the list you were given, and pass its inputs as an object. Use "
          "one when the shape of the work is already known; do the steps yourself "
          "when it is not."
      ),
      params=(
          Param("flow", "choice", "Flow", required=True, choices=(),
                description="Which saved flow to run, by name."),
          Param("input", "text", "Inputs", required=False,
                description=(
                    "The flow's own inputs, as a JSON object written as a string -- "
                    'for example {"topic": "attention"}. The names each flow takes '
                    "are listed with it."
                )),
      ),
      roles=(),
      runner="agents.runtime.flow.run_flow",
  )


  def narrowed_flow_spec(spec: ToolSpec, principal) -> ToolSpec | None:
      """`spec` with `flow`'s real options, or `None` to drop the tool.

      `None` when this principal has no enabled flow: offering a tool
      whose only argument has no legal value is worse than not offering
      it -- the model will call it, fail validation, and burn the turn's
      one recovery on a tool that could never have worked.

      The description gains a line PER FLOW (name, and its input keys),
      because the `enum` alone tells the model which slugs exist and
      nothing about what they take. That is the whole prompt surface of a
      flow, so it is built from the rows rather than from anything static.

      `dataclasses.replace`, never a mutation: `ToolSpec` is frozen and
      the registered constant is shared by every turn on the box.
      """
      from agents.visibility import visible_flows

      flows = list(visible_flows(principal))
      if not flows:
          return None
      slugs = tuple(f.slug for f in flows)
      lines = "\n".join(
          f"- {f.slug}: {f.description or f.name} "
          f"(inputs: {', '.join(i.get('key', '') for i in (f.inputs or [])) or 'none'})"
          for f in flows
      )
      params = tuple(
          replace(p, choices=slugs) if p.key == "flow" else p for p in spec.params
      )
      return replace(spec, params=params,
                     description=f"{spec.description}\n\nAvailable flows:\n{lines}")
  ```
- [ ] Register it in `agents/apps.py::ready()`, after the agent-as-tool loop:
  ```python
          # The ONE flow tool (ruling 1, deviation P3-D11). A flow is a
          # ROW; its slug reaches the model through this spec's `flow`
          # choices, filled per turn by `agents.runtime.flowtool.
          # narrowed_flow_spec`. So this method still touches no database
          # and still imports no implementation module -- the runner is a
          # dotted-path STRING.
          from agents.runtime.flowtool import FLOW_RUN

          register_tool(FLOW_RUN)
  ```
  **`agents/runtime/flowtool.py` must therefore stay import-light**: it imports `agents.contracts.tools` and `models.contracts.operations` (both pure leaves) at module scope and `agents.visibility` **inside** `narrowed_flow_spec`, which is the same lazy-service-import discipline the spec's correction §4 records for every `tools.py`. Task 14's `_REGISTRATION_MODULES` sweep gains this file.
- [ ] Narrow the spec per turn in `agents/runtime/loop.py::_available_tools`, where the turn's tool list is already built:
  ```python
      for key in granted_tools(principal, agent.tool_keys):
          spec = get_tool(key)
          if key == FLOW_RUN_KEY:
              # Filled from the ROWS this principal may run, once per
              # turn. A dropped `None` means there are no flows to offer,
              # and the model is simply never told the tool exists --
              # enforcement by omission, the same mechanism section 6.4
              # uses for the depth cap.
              spec = narrowed_flow_spec(spec, principal)
              if spec is None:
                  continue
          if _roles_resolve(spec):
              out[key] = spec
  ```
- [ ] Teach `agents/runtime/jobs.py::_tool_roles` to walk flow **rows**. Inside the `granted_tools` loop, after `roles.update(spec.roles)`:
  ```python
              if key == FLOW_RUN_KEY:
                  # `flow.run` declares `roles=()` -- a flow's roles are
                  # its STEPS' roles, and they live in ROWS, so they are
                  # supplied HERE, at enqueue time, exactly as a
                  # delegate's are. `plan_turn` already touches the
                  # database (it loads the turn and its agent), so
                  # reading the flows costs one more query and no new
                  # constraint. Without this the queue is never told a
                  # flow needs the embedding model, and the admission
                  # snapshot is simply wrong.
                  roles.update(flow_row_roles(principal))
                  continue
  ```
  with `flow_row_roles(principal)` in `agents/runtime/flowtool.py`: union the declared `roles` of every registered step tool across every visible enabled flow, **skipping steps whose tool is not registered here** (ruling R1's tolerance — a vision step on a box with the vision flag off is a not-here, not a fault). Extend `plan_turn`'s docstring to name flows alongside agent-as-tool in its "an agent-as-tool spec declares EMPTY roles" paragraph.
- [ ] Add `library-brief` to `DEFAULT_FLOWS` in `agents/defaults.py`:
  ```python
  DEFAULT_FLOWS: tuple[FlowSpec, ...] = (
      FlowSpec(
          slug="library-brief",
          name="Library brief",
          description=(
              "A fixed two-step brief on one topic: search the operator's own "
              "documents for it, then answer it from them with citations. Runs the "
              "same two steps every time, in the same order, with no decisions in "
              "between."
          ),
          inputs=(
              FlowInput("topic", "Topic", required=True,
                        description="What the brief should be about."),
              FlowInput("category", "Category",
                        description=(
                            "Restrict to one library category by name. Leave it out "
                            "to use every category."
                        )),
          ),
          steps=(
              FlowStep("rag.search", {"query": "$input.topic",
                                      "category": "$input.category"}),
              FlowStep("rag.ask", {"question": "$input.topic",
                                   "category": "$input.category"}),
          ),
      ),
  )
  ```
  **Write this note beside it, because a reader will otherwise think a `$steps.N` reference was forgotten:**
  ```python
  # NOTE: this flow uses `$input.*` only. Neither `rag.search` nor
  # `rag.ask` takes an argument a prior step's output can honestly fill
  # -- `rag.ask` takes a question, and feeding it a retrieved title would
  # ask a question nobody asked. `$steps.N.*` resolution is real, is the
  # whole point of `resolve_ref`, and is proven end to end in
  # `agents/runtime/tests/test_flow.py` over registered stub tools. A
  # shipped default contorted into using one would be a worked example
  # pretending to be a product.
  ```
- [ ] Add `"flow.run"` to `general`'s `tool_keys` in `agents/defaults.py`, with:
  ```python
          # P2's deviation D8 withheld a flow key because P3 had not
          # registered one yet. It is registered now, and it is ONE key
          # for every flow the box has (ruling 1) rather than one per
          # flow -- so this grant does not change when a flow is added.
          # Ruling 2: the row this grant lives on appears when somebody
          # INSTALLS the `general` default, not when a deploy runs.
  ```
  Update `general`'s system prompt with one sentence about running a saved flow — describing **what it does**, never what backs it.
- [ ] Extend the tests:
  - `agents/tests/test_apps.py`: `flow.run` is registered after `ready()`; its `runner` resolves through `resolve_dotted_path`; its `flow` param has **empty** `choices` at registration (the anti-vacuous half of P3-D11: if it were pre-filled, the per-turn narrowing would be dead code that still looked alive); `ready()` still imports no implementation module and **still touches no database** — extend the existing assertions rather than writing second copies; and **no `flow.<slug>` key is registered**, the pin on P3-D3's retirement.
  - `agents/runtime/tests/test_loop.py`: with two enabled flow rows, the tool schema the loop hands the model carries an `enum` of exactly those two slugs and a description naming their input keys; with **no** flow rows, `flow.run` is **absent from the tool list entirely** (offering a tool whose only argument has no legal value would burn the turn's one recovery on a call that could never work); a disabled row is absent from the enum; and a third flow added between two turns appears in the second turn's schema **without a restart** — the property that makes rows worth having.
  - `agents/runtime/tests/test_jobs.py`: `plan_turn` for an agent granted `flow.run` declares `rag.embed` and `rag.answer` from the installed `library-brief` **row**; the anti-vacuous half — an agent granted **only** `flow.run` still declares them, so the assertion cannot pass because some other granted tool named the same roles; and a flow row whose step names an unregistered tool is skipped rather than raising.
  - `agents/tests/test_defaults.py`: `install_default("flow", "library-brief", principal)` creates a row whose `steps` JSON round-trips through `parse_flow_json` unchanged; **`library-brief` runs with `topic` alone** (M7) — `category` is an optional declared input, so `$input.category` resolves to `None` and a blank category means "every category", the documented default (ADR 0009, and correction §2's whole reason for declaring `category` as `"text"` rather than `"choice"`). Drive it through `invoke_tool` with stub `rag.*` runners under the snapshot fixture and assert both steps ran and neither raised. **This is the case a naive `input[key]` lookup breaks, and it is the flow's most ordinary call.**
- [ ] **Add `"agents/runtime/flowtool.py"` to BOTH `RUNTIME_MODULES` and `_REGISTRATION_MODULES`** in `foundation/ops/tests/test_column_boundaries.py`, in this commit. It is runtime code (swept for `get_job` like every other module under `agents/runtime/`) **and** it is a registration module (`ready()` imports it, so its module-scope imports must stay inside the allowed set — `agents.contracts.*` and `models.contracts.*`, with `agents.visibility` lazy inside `narrowed_flow_spec`). Two lists because it is genuinely two things; Task 14 verifies both and adds neither. Confirm `test_no_tool_module_imports_its_service_layer_at_module_scope` now covers it and passes.
- [ ] Run `.venv/bin/pytest -q agents foundation/ops` — green.
- [ ] Run the **full gate matrix**. Green. Record the count.
- [ ] Update `agents/README.md`'s tool table with `flow.run` (one tool, per-turn choices) and the shipped-flow catalogue, and state that adding a flow needs **no** deploy and no restart — a row appears in the next turn's schema.
- [ ] Commit: `feat(agents): one flow.run tool whose choices come from the rows, and the library-brief default`.

---

### Task 14: guards, docs, the gate matrix, and the ladder

The structural pins that keep P3's rules true after P3, the doc sweep, and the verification ladder ending in fresh pixels on the owner's live system.

**Files:**
- `foundation/ops/tests/test_column_boundaries.py` (`RUNTIME_MODULES`, the view/command exclusions)
- `foundation/ops/tests/test_import_law.py` (an anti-vacuous pin that the `agents/` sweeps really cover `agents/chat`)
- `agents/chat/tests/test_never_500.py` (new)
- `docs/DEV.md`, `docs/ROADMAP.md`, `agents/README.md`, `agents/chat/README.md`, `agents/runtime/README.md`
- `.superpowers/sdd/2026-08-28-agents-p3-chat-and-flows/progress.md` (the ledger)

**Steps:**

- [ ] **Add `"agents/runtime/bindings.py"` to `RUNTIME_MODULES`, then verify the sweep is complete rather than extending it further.** `bindings.py` predates P3 and was never swept; the directory-derived check below is red without it, and it should be — it is runtime code that could grow a `get_job` call like any other. Tasks 2, 9, 12, and 13 added `audit.py`, `preflight.py`, `flow.py`, and `flowtool.py` in the commits that created them (M5, A3), so after this one entry the list is complete. **`_REGISTRATION_MODULES` needs nothing here either** — Task 13 added `flowtool.py` to it in the same commit; verify, do not extend. Then write the check as a **directory listing minus a named, commented exclusion set** rather than an equality against a typed list:
  ```python
  # Everything under `agents/runtime/` is swept, with exactly one
  # exclusion, named here so adding a second is a decision rather than an
  # omission: `__init__.py`, which holds no code. `tests/` is not a
  # module of the package for this purpose.
  _RUNTIME_SWEEP_EXCLUSIONS = frozenset({"agents/runtime/__init__.py"})

  def test_every_runtime_module_is_swept():
      """A hand-maintained list checked by hand is checked once. This
      derives the expected set from the directory, so a module added
      later is swept or this fails naming it."""
  ```
  Confirm `test_every_registered_runner_lives_in_a_swept_module` covers `agents.runtime.flow.run_flow`.
- [ ] **Make the two view/command exclusions explicit, not implicit — by EXTENDING the test that already does half of it.** `foundation/ops/tests/test_column_boundaries.py::test_the_management_command_really_does_poll_and_is_really_excluded` (P2 Task 12) already asserts both directions for `agents/management/commands/agent_turn.py`: that it really calls `get_job`, and that it is really absent from the swept list. P3 adds a second excluded caller with the identical justification, so it belongs **in that test**, not in a near-duplicate beside it — two tests spelling one rule is the drift this plan keeps naming. Rename it to `test_the_queue_polling_callers_really_poll_and_are_really_excluded`, parametrize it over the two paths, and extend its docstring:
  ```
      The never-block rule governs a TOOL RUNNER -- code executing INSIDE
      a job, holding the machine's one execution slot in sequential mode
      (`models/queue/scheduler.py:375`). A management command holds no
      slot; it stands OUTSIDE the queue waiting for it. A VIEW holds no
      slot either; it answers one request and returns, exactly as
      `tools/vision/views.py::queue_job_status` already does.

      BOTH DIRECTIONS, for both files: each really does call `get_job`,
      and neither is in `RUNTIME_MODULES`. A refactor that moved either
      into the swept list would then be a decision somebody had to make,
      rather than a rule quietly changing under a test that no longer
      described it.
  ```
  Also extend that module's own comment block (the one naming the `agents/management/` carve-out) to name `agents/chat/views/turns.py` beside it, since the comment is what a reader finds first.
- [ ] **Pin that the `agents/` import-law sweeps really see `agents/chat`.** Both rule-3 gates walk `git ls-files -- agents`, so `agents/chat` is covered *by construction* — but "by construction" is exactly the kind of coverage that silently disappears. Add:
  ```python
  def test_the_agents_import_sweeps_actually_reach_the_chat_app():
      """Anti-vacuous pin. `test_no_agents_module_imports_a_tools_package`
      and `test_agents_reaches_models_registry_through_bindings_and_
      nothing_else` both walk `git ls-files -- agents`, so `agents/chat`
      is covered by construction -- and a sweep that quietly stopped
      seeing a whole app would still pass every assertion in it."""
      out = subprocess.run(["git", "ls-files", "--", "agents"], cwd=REPO_ROOT,
                           capture_output=True, text=True, check=True)
      swept = [p for p in out.stdout.splitlines()
               if p.endswith(".py") and not _is_test_file(p)]
      assert any(p.startswith("agents/chat/") for p in swept)
      assert "agents/chat/rendering.py" in swept
  ```
- [ ] **A never-500 sweep over the whole surface.** Write `agents/chat/tests/test_never_500.py`: for every URL name in `agents/chat/urls.py`, drive the view with the database in a normal state, with **no agents at all**, with the queue raising `QueueUnavailable`, and with a malformed/absent target, and assert the status is always in `{200, 202, 302, 400, 404, 405, 409, 503}` — `409` is the one-turn-at-a-time refusal (M9) — and the body never contains `"Traceback"`. Build the URL list by **importing `agents.chat.urls.urlpatterns`** rather than typing six names, so a seventh route added later is swept automatically.
- [ ] **`docs/DEV.md`.** Replace the "Driving an agent turn from the CLI" section's opening line — "There is no `/chat/` page until P3" is now false, and a doc that lies about what exists is worse than one that says nothing. Rewrite it as: `/chat/` is the surface; `manage.py agent_turn` remains the CLI equivalent and the scriptable one. Add a **Using `/chat/`** subsection: assign a tool-capable chat model to `chat.converse` at `/inference/`, open `/chat/`, **press "Add the default General assistant"** (or run `manage.py install_defaults`), then send a message. **Rewrite the "A resident-agent change also needs `sync_agents`" subsection wholesale** — its command is gone and its story has changed: a code change to the catalogue changes what is **offered**, never what is installed; an operator who already installed `general` keeps their `general` including every edit they made to it; a newly shipped default appears on the page as an offer with an Add button; and `manage.py install_defaults --reset <slug>` is the only thing that ever restores shipped text over an existing row. Add an **Accounts are off** note: `ACCOUNTS_REQUIRED` defaults to `0`, `1` raises today, and Identity & Auth implements it. Extend the restart-rules section **without changing the command**: it is `docker compose restart watcher worker`, and `web` is deliberately absent — that section already explains why. What P3 adds to it is scope: `agents/runtime/**` now includes `flow.py`, `flowtool.py`, and `preflight.py`, and `agents/defaults.py` is read by `ready()`. **A new flow needs no restart and no deploy at all** — it is a row, and it reaches the next turn's tool schema through `flow.run`'s per-turn choices.
- [ ] **`docs/ROADMAP.md` Phase 1.6.** Tick **v2 — tool use** with what actually shipped, and add the P3 line: the `/chat/` surface (conversation list, thread, tool cards with thumbnails and citations, 202 + poll, a no-JS path) and flows (**rows**, run through the one `invoke_tool` by the one `flow.run` tool whose choices come from those rows per turn). Name what P3 deliberately did not build: **no flow-as-turn** (deviation P3-D4), **no flow-builder UI** (§14 gap 2 — a flow is still JSON an operator edits out of band, but it is a ROW now, which is what a builder would edit). Record the two operator-facing changes P3 makes to P2's story, because they change what a deploy does: **nothing is seeded automatically** (ruling 2 — `install_defaults` replaces `sync_agents`) and **a row that came from a shipped default is the operator's to edit** (ruling 3). Under **Identity & Auth**, name the seams P3 left ready: `principal_for_request`, `agents/visibility.py`'s three functions, `ACCOUNTS_REQUIRED`, and the `owner_kind`/`owner_key` columns on all three tables. Leave the three post-P4 phases (Identity & Auth → MCP edge → tenancy) in their existing order.
- [ ] **The READMEs.** `agents/README.md`: `chat/` moves from "P3" to "shipped" in the sub-package table, with the flow tool in the tool list and a **Flows** section. `agents/chat/README.md`: complete — the URL table, the 202-and-poll contract, the no-JS path, the §10.1 mapping, the pre-auth behaviour, the `Turn.State`-not-queue-state rule. `agents/runtime/README.md`: `audit.py`, `preflight.py`, `flow.py`, and the corrected `degraded` sentence. **`agents/README.md` also records the helper-import ruling** from this plan's Global Constraints — that `agents/chat/tests/_helpers.py` may import row builders from `agents/tests/_helpers.py` because the duplication rule's boundary is the COLUMN, and that the cross-column rule is unchanged. A ruling that lives only in a plan is a ruling the next author re-litigates.
- [ ] **Run the full gate matrix one final time** on the finished branch, both flag states, both collection orders, on `farabunker_impl` at 5433. Record all four results and the final collected count in the ledger. **A count that differs between the two collection orders is a real defect and must be chased, not noted.**
- [ ] **The ladder.** No "done"/"works"/"complete" language about `/chat/` or flows until this finishes.

  **Rung 1 — the suite.** The four gate commands green. This is not a claim of completion.

  **Rung 2 — the branch preview stack.** Per ADR 0011 and `docs/DEV.md`: `scripts/preview` up on this worktree, `manage.py migrate` (applies `agents/0002`), **`docker compose restart watcher worker`** (job-kind code changed; `web` auto-reloads and is deliberately not in that command), bind `chat.converse` to a tool-capable chat connection at the preview's `/inference/`. Then the checklist below — every item observed rather than inferred.

  The numbered list is `## Smoke Checklist` below — `docs/DEV.md:275-278` requires every implementation plan to carry one under that exact heading, driven by a human against the preview after review and before the merge decision.

  **Rung 3 — the live stack.** Merge `main` into the branch first, resolve nothing by guessing, then follow the bind-mount deploy discipline: the live checkout serves `main`, so the live proof runs after the branch's SHA is on `main`. `manage.py migrate`, `docker compose restart watcher worker`, then re-run checklist items 1, 2, 4, 5, 8, and **14** on the live box — item 14 is the button path, and ruling 2 makes it the only way rows appear at all. Serialize container git operations against any other session.

  **Rung 4 — fresh pixels.** A screenshot of `/chat/` on the owner's live `:8000` showing a real conversation with a rendered citation, plus one of a tool card with a thumbnail. Only after this may the work be described as complete.
- [ ] Keep the ledger `.superpowers/sdd/2026-08-28-agents-p3-chat-and-flows/progress.md` current through every task: the preflight scan, every ruling with its "why" and its "cost if wrong", the per-task commit range and collected count, and every deferred item with the phase that owns it.
- [ ] Commit: `docs(agents): P3 guards, the /chat/ documentation sweep, and the gate matrix`.

---

## Smoke Checklist

Numbered, browser-level, driven by a human against the branch preview stack at Rung 2 (and items 1, 2, 4, 5, 8 again on the live box at Rung 3). Each row is **observed**, never inferred from a passing test.

| # | Step | What must be true |
|---|---|---|
| 1 | `/chat/` loads | Conversation list renders; the Chat nav entry is marked current; every other page still renders (the nav edit touched all nine entries) |
| 2 | Start a conversation with a message | Redirect or 202; the thread shows the user turn and a pending assistant turn |
| 3 | Wait for the answer **with JS on** | The pending card is replaced in place, without a manual refresh — and **the tool cards appear with it** (M6: the poll body is the whole block this job wrote, so the polled thread and a refreshed one show the same thing; if the answer appears alone and F5 then adds tool cards, that is the bug this item exists to catch) |
| 4 | A turn that calls `rag.search` or `rag.ask` | A tool card appears with the tool's label, its arguments, and **a citation whose title links to `rag-document-file`** — click it and get the document |
| 5 | A turn that calls `vision.generate` | A tool card with **a thumbnail served by `vision-output-file`** — click it and get the image |
| 7 | A turn that delegates (`agent.library`) | The delegate's steps are inside a **collapsed** disclosure under the delegating card |
| 8 | **JS off** (disable scripting in the browser), send a message | Plain POST → redirect to `?pending=<id>`; the page shows the pending turn; **refresh shows the answer**. This is the item most likely to have quietly broken, so do it explicitly rather than assuming |
| 9 | Unbind `chat.converse`, reload a thread | The page still renders 200 with a banner and a link to the model console; sending is refused with a 503 and the same sentence; **nothing is queued** (`/queue/` unchanged) |
| 10 | Send a message, then cancel the job from `/queue/` | The turn becomes `cancelled` with `on_turn_terminal`'s own sentence; any open `ToolInvocation` for it is closed (`finished_at` set) — Task 2, observed on a real cancel |
| 11 | Delete a conversation | It leaves the list; its `ToolInvocation` rows survive (`manage.py shell`, count before and after) |
| 12 | Turn the vision feature off and reload a thread carrying an `output:` artifact | The page renders; the artifact shows as plain text; **no 500 and no broken image** |
| 13 | Send a second message while the first is still pending | Refused with the one-turn-at-a-time sentence; **no second job appears at `/queue/`** and the thread is unchanged (M9) |
| 14 | **On a database with no agent rows at all**, open `/chat/` | An honest empty state naming the shipped defaults, each with an **"Add the default X"** button; `Agent.objects.count()` is still **0** afterwards — a page load installed nothing (ruling 2). Do this FIRST, before item 1, because every later item needs a row and this is the only chance to see the state a fresh box is really in |
| 15 | Press "Add the default General assistant" | One row appears, owned by `open/box`; the offer disappears from the list and the agent appears in the picker; press it again and there is still exactly one row |
| 16 | Edit that agent's prompt (`manage.py shell`), then run `manage.py install_defaults` | The edit **survives** — ruling 2's whole point, and the behaviour P2's `sync_agents` did not have. Then `manage.py install_defaults --reset general` and confirm the shipped text is back |
| 17 | **With JavaScript off**, press an Add button | It works: a plain POST form with a CSRF token, and a redirect back to `/chat/`. A GET of `/chat/defaults/install/` returns **405** |
| 18 | Add the `library-brief` flow, then ask `general` to run a saved flow | The model's tool schema offers `flow.run` with `library-brief` in its enum (visible in the turn's tool card arguments); **one** tool card for the flow, carrying the closing `rag.ask`'s citations; `/queue/` shows **one** `agent.turn` job, not two — a flow is one tool call inside one turn, and two jobs would mean it had enqueued something (which §6.3 forbids) |
| 19 | Disable that flow row, start a new turn | `flow.run` is either absent from the tool list (no flows left) or its enum no longer names it — **without a restart**, which is the property rows were chosen for |

---

## Coverage against the spec

| Spec item | Task |
|---|---|
| §6.5 a flow is data plus a small resolver, not a workflow engine | 9, 10 |
| §6.5 `resolve_ref` — `$input.*`, `$steps.N.text`, `$steps.N.data.<path>` with list indexing, `$steps.N.artifacts.<i>`; raises naming the reference, never a silent `None` | 10 |
| §6.5 `run_flow` — every step through the SAME `invoke_tool`; a step failure ends the flow, no per-step recovery | 10 |
| §6.5 a flow **as a tool**, so an LLM agent can invoke a fixed sequence as one step | 9, 10, 11 |
| §6.5 a flow **as a turn** (`"mode": "flow"`) | **Deviation P3-D4 — not built.** No producer exists; named gap |
| §7.4 `Flow` row + `steps` validated on save | **Deviation P3-D2 — code-declared `FlowSpec`, validated in `__post_init__`.** The validation rules themselves are kept verbatim (9) |
| §8.1 `/chat/` mounted ungated; nav link in the shell | 2 |
| §8.2 six URLs, their names and methods | 2 (table), 3, 5, 6, 7, 8 |
| §8.2 templates under `chat/` — base, index, conversation, `_turn_card`, `_tool_card`, `_unavailable`, `_form_errors` | 2, 3, 5, 8 |
| §8.3 step 1 — blank text → 400, per-field message, nothing written | 6 |
| §8.3 step 2 — the picked connection validated against the chat capability → 503 | 6 (via `resolve_chat`, with its own distinct message) |
| §8.3 step 3 — preflight before enqueue: unbound role, tool-calling unsupported, and tolerant notes for droppable tools | 6 |
| §8.3 step 4 — USER + placeholder ASSISTANT turn in one transaction | 6 |
| §8.3 step 5 — `enqueue`, `QueueUnavailable` → 503, any other exception logged → 503, rows rolled back | 6 |
| §8.3 step 6 — `queue_job_id` stamped, then 202 for XHR or a `?pending=` redirect for no-JS | 6 |
| §8.3 `turn_status` — always 200 for a readable turn; 404 and 503 the only non-200s; per-state bodies | 7 — **deviation P3-D9** for the cancelled body; the `done` body is the whole job's block (M6) |
| §8.3 — one turn at a time per conversation, so `Turn.next_index`'s stated safety condition holds for the page as it does for the CLI | 6 (409 refusal + `IntegrityError` caught by name) |
| §8.3 client polling — 2000 ms, 3 transport retries, 10-minute ceiling, polls the handed-back `status_url` | 7 |
| §8.4 turns in index order; `depth > 0` in a collapsed disclosure | 4, 5 |
| §8.4 tool cards — label, args, image thumbnails via `vision-output-file`, citations via `rag-document-file`, else escaped text | 4, 5 |
| §8.4 / `agents/contracts/artifacts.py` — no layer below the view exposes a filesystem path (`source_path` never reaches a card or the page) | 4, 5 |
| §8.4 citations from `Turn.data` | 4 — **deviation P3-D6**: `citations` OR `results` |
| §8.4 failure renders `Turn.error` plus a setup link, never a traceback | 4, 5, 8 |
| §8.5 `chat.converse` registered | **P2 — already shipped**, unchanged here |
| §8.5 per-conversation model picker from `picker_options("chat", CHAT_CONVERSE_ROLE, …)` | 3 — **deviation P3-D7**: per-turn, carried in the query string |
| §10.1 every failure row that has a page surface | 6, 7, 8 |
| §10.1 cancel / double-orphan → `on_turn_terminal` | 1 (extended to close open invocations), 12 (browser) |
| §10.3 `on_turn_terminal` as a conditional UPDATE | 1 |
| §10.4 progress read by the poller from the same `report_progress` dict `/queue/` renders | 7 |
| §11.1 no `conftest.py`; per-package `_helpers.py`; the vision-flag rule | Global Constraints; 2, 4 |
| §11.3 chat-surface tests — 400, three 503s, the 202 body, `turn_status`'s states, the non-XHR whole-page render, an `output:` `<img>`, a citation link | 6, 7, 8 |
| §11.3 flow tests — `resolve_ref` on all four forms and its named failure; a flow builds no LLM; the declaration validator refuses an unknown tool and a forward reference | 9, 10 |
| §11.4 both flag states, both collection orders, private DB on 5433 | Global Constraints; every task; 12 |
| §12.4 content — the `agents/chat` app, the nav link, the mount, the flow runner, the flow tool | 1–13 |
| §12.4 gates — the full ladder to Rung 4; the no-JS path; every §10.1 error path reproduced in a browser | 12 |
| §14 gap 4 — `/chat/` unauthenticated; `Turn` pk enumeration inherited not widened | 7 (named in `turn_status`'s docstring), 12 |
| **P2 ledger** — renderers treat `finished_at IS NULL` as in progress | 1, 4 |
| **P2 ledger** — `ToolInvocation.text` blank on error paths; render `outcome`/`error` | 1, 4 |
| **P2 ledger** — `on_turn_terminal` closes open invocation rows | 1 |
| **P2 ledger** — the CLI's exit codes are the page's reference states | 6 |
| **P2 ledger** — the no-queue-wait guard should name its exclusions explicitly | 12 |
| **P2 D8** — `general` gains a flow key once P3 registers one | 11 |
| **P2 D7** — the payload's `"mode"` gains a second value | **Superseded by P3-D4**: it does not, and the reason is D7's own |
| **Addendum 3** — `ToolInvocation` is its own row; a conversation delete never erases it | 8 |
| **Addendum 6** — Identity & Auth → MCP edge → tenancy stay the next phases | 12 (ROADMAP untouched); 3 (the one queryset that changes) |
| §6.5's single `agents/runtime/flows.py` module | **Deviation P3-D10** — split into `agents/defaults.py`, `agents/runtime/flow.py`, and `agents/runtime/flowtool.py` |
| §7.4 `Flow` is a row with `steps` validated on save | 1 (model + migration), 5 (the one validator, shared with the catalogue) — **ruling 1** |
| §7.5's resident sync | **Retired by ruling 2.** 5 (`install_default`, `manage.py install_defaults`), 6 (the Add button) |
| Ruling 3 — a row from a shipped default is the operator's | 1 (the lock removed, `slug` still immutable), 5 (`--reset`) |
| Ruling 4 — auth-ready seams, accounts off | 4 (`principal_for_request`, `ACCOUNTS_REQUIRED`, `agents/visibility.py`, the direct-queryset guard), 1 (`"open"`, the owner columns), 6 (owner stamped on create) |
| §11.1 — every new runtime module joins `RUNTIME_MODULES` in the commit that creates it | 1, 6, 10; verified in 12 |
| `docs/DEV.md:275-278` — every plan carries a `## Smoke Checklist` | the section of that name |

## Self-review

**Placeholder scan.** No step says "similar to Task N", "as above", "and so on", "TODO", or "etc." in place of code. Every module that ships has its bodies written out — round 1's m6 closed the last of the docstring-only stubs, and the 2026-08-28 amendment writes out `install_default`, `narrowed_flow_spec`, and the row-loading `run_flow` in the same style — every test class names the behaviour it pins and why that behaviour is right, and the three places that deliberately contain prose rather than code are marked as such and are all mechanical fill-in: the CSS block in Task 8 (token names given, values left to the implementer's eye), the poller's `tick()` body in Task 10 (every branch and its rule enumerated, the JS spelled out only where a rule lives), and the `_helpers.py` additions, which are named with their exact signatures and purposes at the point they are introduced.

**Name and signature consistency, checked across tasks.**

| Name | Declared | Consumed by |
|---|---|---|
| `invocation_state(invocation)` / `invocation_message(invocation, turn_text="")` | 1 | 4 (`tool_card`) |
| `close_open_invocations(queue_job_id, *, reason)` | 1 | 1 (`on_turn_terminal`, `run_turn`) |
| `ToolInvocation.queue_job_id` | 1 | 1 (`invoke_tool`, `invoke_unknown_tool`, both closers) |
| `ChatConfig` (`label="chat"`) | 2 | `INSTALLED_APPS`, `EXPECTED_LABELS` |
| `chat_picker_options(selected="")` | 3 | 3 (index), 5 (`thread_context`) |
| `thread_cards(conversation, *, queue_job_id=None)` / `turn_card(turn, nested=None)` / `tool_card(turn)` | 4 | 5 (page), 7 (`_done_body`, narrowed to one job) |
| `artifact_links(references)` / `citations_of(turn)` | 4 | 4, 5 |
| `thread_context(request, conversation, *, selected=None)` | 5 | 5 (`ConversationView`), 6 (the 503 re-render, passing `request.POST`'s pick) |
| `Preflight(ok, reason, message, resolved, answered_by, dropped_tools)` / `preflight_turn(agent, connection)` | 6 | 6 (`start_turn`, `agent_turn._preflight`), 8 (the thread banner) |
| `TurnStart(...)` / `start_turn(conversation, text, *, connection="")` | 6 | 6 (`turn_create`), 6 (`conversation_start`) |
| `_is_xhr(request)` | 6 | 6, 8 |
| `_BODY_BUILDERS` / `turn_status(request, turn_id)` / `_done_body(turn, request)` | 7 | 7 (its own totality pin), `urls.py` |
| `chat/_turn_block.html` | 7 (factored out of `conversation.html`) | 5/7 (the page), 7 (`_done_body`) — **one loop, two renderers** |
| `POLL_INTERVAL_MS` / `MAX_TRANSPORT_RETRIES` / `MAX_POLL_DURATION_MS` | 5 (`agents/chat/service.py`) | 5 (`thread_context` → template), 7 (its test) — **in `service.py`, not `turns.py`**, so `thread.py` never imports `turns.py` |
| `conversation_url(conversation, *, connection="", pending=None)` | 3 (`agents/chat/service.py`) | 3 (`conversation_start`), 6 (`turn_create`'s no-JS redirect) |
| `FlowSpec` / `FlowStep` / `FlowInput` / `FlowDeclarationError` / `FLOW_TOOL_PREFIX` | 9 | 9, 10, 11 |
| `FLOW_RUN` / `FLOW_RUN_KEY` / `narrowed_flow_spec(spec, principal)` / `flow_row_roles(principal)` | 13 (`agents/runtime/flowtool.py`) | 13 (`apps.py` registers, `loop._available_tools` narrows, `jobs._tool_roles` walks) |
| `validate_flow_json(inputs, steps, *, label="")` / `parse_flow_json(inputs, steps)` | 5 (`agents/defaults.py`) | 1 (`Flow.save()`), 5 (`FlowSpec.__post_init__`), 12 (`_run_steps`) — **one validator and one parser for rows and catalogue alike** |
| `install_default(kind, slug, principal, *, reset=False)` / `missing_defaults(kind, installed)` / `catalogue(kind)` / `default_for(kind, slug)` | 5 | 6 (the Add button, the offers list), `manage.py install_defaults` — **and nowhere else; Task 5's AST guard counts the call sites** |
| `principal_for_request(request)` / `OPEN_PRINCIPAL` | 4 (`agents/chat/principal.py`) | 6, 9, 10, 11 — the ONLY request→principal conversion |
| `visible_conversations` / `visible_agents` / `visible_flows` / `owner_fields` | 4 (`agents/visibility.py`) | 6, 8, 11, 12, 13 — and the guard says nothing else reaches those managers |
| `resolve_ref(ref, *, input, steps)` / `_walk(ref, value, path)` / `resolve_args(args, *, input, steps)` | 12 | 12 (`_run_steps`) |
| `_finished(flow, steps, results, artifacts)` / `_degraded(flow, steps, index, results, artifacts)` / `_parsed_input(raw, flow)` | 12 | 12 (`_run_steps`, `run_flow`) — all four take the ROW, not a catalogue spec |
| `principal_for(agent)` | 6 (**`agents/runtime/bindings.py`**, beside `resolve_chat` — `preflight.py` cannot hold it without a cycle) | 6 (`preflight_turn`), plus two moved call sites (`loop._run_turn`, `jobs._tool_roles`) and one deletion (`agent_turn._preflight` builds none after delegating) |
| `run_flow(args, ctx)` / `_run_steps(flow, inputs, ctx)` | 12 | `FLOW_RUN.runner`'s dotted-path string only — never imported |
| `DEFAULT_AGENTS` / `DEFAULT_FLOWS` | 5 (`agents/defaults.py`; `DEFAULT_FLOWS` gains `library-brief` in 13) | 5 (`catalogue`, `install_default`), 6 (the offers list) — **a catalogue, never a registry: nothing resolves a flow through it at run time** |

**Three things a reviewer should push on, named rather than hidden.**

1. **The per-turn narrowing of `flow.run` is the one genuinely new mechanism in this plan (P3-D11).** Nothing in the tree fills a `Param.choices` at prompt time today — `Param.choices`'s docstring anticipates it for engine-supplied options, and `_input_schema` already handles both the filled and the empty case, but P3 is the first caller. If it turns out to be a mistake, the fallback is a free-text `flow` param plus a `ToolRefused` naming the valid slugs: worse prompt, same runner, one file.
2. **`ToolInvocation.queue_job_id` is a migration this phase was not obliged to have.** Without it, `close_open_invocations` cannot reach the exact row the live incident produced — the crashed call has no `Turn` pointing at it. The alternative (close only rows referenced by the turn's TOOL turns) fixes the easy half and leaves the hard half open forever, which is the half the ledger actually named.
3. **`start_turn` refuses a second concurrent turn with a 409 (M9), which is a product decision as much as a correctness one.** The alternative — queue both and let `uniq_turn_index` sort it out — makes `Turn.next_index`'s own stated safety condition false for the page, and produces two turns racing to re-index the same placeholder. The cost is that an operator cannot queue a follow-up while thinking; if that turns out to matter, the fix is a real per-conversation job chain, not relaxing this.
4. **`agents/chat` is a second Django app while `agents/runtime` is not** (P3-D1 vs P2's D1), and that asymmetry needs its reason said out loud: `runtime` holds models, `chat` holds templates. It also forces the one helper-import ruling in Global Constraints, which is the only place this plan softens a P2 rule — and it softens it along the column boundary that rule was written to defend, not across it.

---

---

## Plan review

### Round 1 — AMEND (9 MAJOR / 11 minor). Author applied all findings.

**Superseded in part by the 2026-08-28 owner rulings** — see `## Amendment (2026-08-28)`. This block is kept verbatim as the record of what was reviewed and why; where it and the amendment disagree, the amendment wins. Specifically: findings about `agents/flows.py`, `flow.<slug>`, `flow_tool_specs`, and `RESIDENT_FLOWS` refer to modules and names the rulings retired, and Task numbers in it are the post-amendment ones.

**Orchestrator ruling.** Deviations **D1–D8 accepted as written**. Two further deviations were added while applying this round (**P3-D9**, the cancelled body's `error`; **P3-D10**, the `agents/flows.py` + `agents/runtime/flow.py` split), both recorded in the deviation table rather than left implicit. The D8 row's JS half is corrected rather than defended: see m7.

**MAJOR**

| # | Finding | Applied in |
|---|---|---|
| M1 | Import cycle: `views/thread.py` put the poller constants into the context while they were declared in `views/turns.py`, and `turns.py` imports `thread.py` for its 503 re-render. | The three constants moved to `agents/chat/service.py` (Task 8 adds them; both modules already import it). `views/__init__.py`'s docstring now states the one-way direction outright — `turns.py` → `thread.py` → nothing, `conversations.py` → `service.py` — with the reason: a constant reached by an import back up the chain is a cycle waiting for its second caller. |
| M2 | Task 9 would have silently broken `agents/tests/test_agent_turn_command.py:58-60,70`: both patch `agents.management.commands.agent_turn._supports_tool_calling`, a name that leaves the call path the moment `_preflight` delegates, so the tool-calling tests go **inert** rather than red. | A dedicated step retargets both patches to `agents.runtime.loop._supports_tool_calling` **before** the delegation lands, with an instruction to watch the test go red first. "Fix the message, not the test" is replaced by the accurate rule: the patch target moved; the message assertions must pass untouched. |
| M3 | Task 5's purity test was a substring scan (`"tools." not in text`) that its own module comment trips. | Rewritten as an `ast.walk` over `Import`/`ImportFrom`, the shape of `foundation/ops/tests/test_import_law.py:323-353`, plus its anti-vacuous lazy-import pin. |
| M4 | `TestToolSpecs` looped over an empty `RESIDENT_FLOWS` and asserted `all(...)` — vacuously green at Task 5 and green for the wrong reason afterwards. | `flow_tool_specs(specs=RESIDENT_FLOWS)` takes the tuple as an argument; every case drives an explicit `_spec()`; a new test pins that the **default** is `RESIDENT_FLOWS`; and Task 13 adds `assert flow_tool_specs()` once the resident exists. |
| M5 | Task 13 registered `agents.runtime.flow.run_flow` while `RUNTIME_MODULES` gained it only in Task 14 — leaving the suite red between them via `test_every_registered_runner_lives_in_a_swept_module`. | Each module joins the sweep in the task that **creates** it: `audit.py` in Task 2, `preflight.py` in Task 9, `flow.py` in Task 12. Task 14 verifies the list against a directory listing instead of extending it. |
| M6 | The `done` poll body carried one card, so a polled thread showed strictly less than the same thread after F5 — every tool call invisible until a refresh. | `thread_cards` grows `queue_job_id=`; `_done_body` renders the whole block that job wrote; the script replaces the pending card with it. Two tests (tool cards present, USER turn absent, delegate steps still nested) plus smoke item 3. |
| M7 | `resolve_ref` conflated an **undeclared** input (a bug) with a **declared-but-unsupplied optional** (normal) — so `library-brief` would have raised on its most ordinary call, `topic` alone. | `_run_steps` seeds `input` from the flow's declaration, so membership discriminates the two; both cases are asserted as a pair in `TestResolveRef`; Task 13 adds the topic-only end-to-end test. |
| M8 | `source_path` — a real server filesystem path — is in every `run_ask` citation dict and reaches `Turn.data` unfiltered. | Pinned twice: `citations_of` returns a fixed four-key dict (Task 7 asserts the key set and that no path is in its `repr`), and Task 8 asserts the path never appears in the rendered page. |
| M9 | Nothing stopped a second turn while one was in flight, which makes `Turn.next_index`'s own stated safety condition false for the page. | `start_turn` refuses with **409** and its own sentence; `IntegrityError` is caught by name with honest copy; both pinned, plus smoke item 13 and `409` added to the never-500 status set. |

**minor (all 11 applied)** — m1 the `UNREGISTERED_CONNECTION` split is declared as a real CLI behaviour change with its own test, not smuggled in as a refactor. m2 `thread_context` takes `selected` so a POST-side 503 re-render keeps the operator's pick. m3 RULING applied: the three inline `Principal(...)` copies move onto `principal_for`, with an acyclicity check. m4 one `conversation_url(conversation, *, connection, pending)` in `service.py`; Task 6 creates the module, Task 9's local `_thread_url` is gone. m5 five unconditional mounts (named), Chat is the ninth nav entry. m6 `flow_tool_specs`, `flow_step_roles`, `get_flow`, `flow_from_tool_key`, `resolve_ref`, `_walk`, `resolve_args`, `_finished`, and `_degraded` are written out in full. m7 RULING applied: **D8's JS claim is dropped** — the guard is the server-side `_BODY_BUILDERS` totality pin, and the plan now says which end is guarded rather than promising a template-side pin it does not write. m8 the running body is `{state, progress, step, label}` with `step`/`label` lifted out of the dict. m9 the cancelled body's `error` is recorded as **P3-D9**. m10 `test_the_management_command_really_does_poll_and_is_really_excluded` is renamed and parametrized over both callers rather than duplicated. m11 (a) `docker compose restart watcher worker` only, with `web`'s auto-reload named; (b) the checklist is now a top-level `## Smoke Checklist` per `docs/DEV.md:275-278`; (c) the helper-import ruling lands in `agents/README.md` in Task 14; (d) the two-module flow split is recorded as **P3-D10**.

### Round 2 — AMEND (4 MAJOR / 5 stale-text). Author applied all findings.

| # | Finding | Applied |
|---|---|---|
| N1 | Round 1's m3 ruling built a cycle: `principal_for` sat in `preflight.py`, which imports `loop.py`, while `loop.py` and `jobs.py` were told to import it back. | `principal_for` moves to **`agents/runtime/bindings.py`** — already imported by `loop.py:48`, `jobs.py:19`, and `preflight.py`, and importing none of them. `preflight.py` imports it from there. The verify step now runs `python -c "import agents.runtime.preflight"` **as well as** the `jobs` import: only that order reaches `preflight` first and can expose the cycle. |
| N2 | `agents/runtime/bindings.py` was never in `RUNTIME_MODULES` (it predates P3), so Task 14's directory-derived completeness check was red on arrival. | Task 14 adds that one entry, then writes the check as **directory listing minus a named, commented exclusion set** (`__init__.py` only) rather than an equality against a typed list. |
| N3 | Residual M6: the `_done_body` prose still rendered the single-card template, contradicting the block decision three paragraphs above it. | `_done_body` is written out in full against `render_to_string("chat/_turn_block.html", {"cards": thread_cards(turn.conversation, queue_job_id=turn.queue_job_id)}, …)`. New `chat/_turn_block.html` (`{% for card in cards %}{% include "chat/_turn_card.html" %}{% endfor %}`) added to Task 10's Files; `conversation.html` includes the same fragment, so the page and the poll body come out of one loop. |
| N4 | Residual M6: a done turn with no `queue_job_id` would call `thread_cards(conversation, queue_job_id=None)` — which means "the whole thread" — and swap the entire conversation in place of one card. Silent duplication, not an error. | Explicit guard: `[turn_card(turn)]` when the stamp is absent, with the reasoning at the call site, plus a test pinning that a stampless done turn renders exactly one card and does not carry the USER turn. |

**stale text (all 5 corrected)** — s1 `thread_cards`' implementation signature now matches its interfaces block. s2 Global Constraints says `flow.py` joins `RUNTIME_MODULES` in **Task 12**, with Task 14 verifying. s3 the Task 10 preamble's JS-vocabulary sentence is replaced by the `_BODY_BUILDERS` totality sentence (round 1, m7). s4 the interfaces block reads `flow_tool_specs(specs: tuple = RESIDENT_FLOWS)`. s5 the principal step is stated as **two call sites and one deletion** — `agent_turn._preflight` builds no `Principal` at all once it delegates, so its copy and its import both go.

### Round A — owner rulings (2026-08-28), then AMEND (9 MAJOR / 11 minor). Author applied all.

Four binding rulings arrived after round 2 and after the plan was merged to `main` at `120c083`. They are design changes, not review findings, so they are recorded as an amendment rather than as findings, and every deviation they contradict is struck through in the table above rather than quietly rewritten. **Two of this plan's own deviations are retired by them (P3-D2 and P3-D3) and P2's D6 and D8 go with them.** Four new deviations replace them (P3-D11 … P3-D14). The task list grew from 12 to 14: one new task for the data model, one for the auth-ready seams, one for the defaults catalogue, and the old flow-declaration task absorbed into the last of those.

Everything not named by the rulings is unchanged and still stands as reviewed: the audit-row semantics, the rendering layer, the thread, the 202-and-poll contract with its whole-block `done` body, the 409 in-flight refusal, the shared preflight, the never-500 rule, the structural guards, and the ladder.

---

## Amendment (2026-08-28)

Four owner rulings, binding, applied in place. Each is stated as the ruling, then what changed, then the deviation that carries it.

### Ruling 1 — flows are rows, like agents

A `Flow` model joins the phase's one migration (Task 1): `slug` (CI-unique, immutable), `name`, `description`, `inputs` JSON, `steps` JSON with `$input.x` / `$steps.N.field` references, `resident`, `enabled`, `owner_kind`/`owner_key`, timestamps. The shipped `library-brief` becomes a code-declared `FlowSpec` used for exactly two things — **a catalogue entry to offer, and a validator for row JSON** — and for nothing at run time.

**One registered tool, `flow.run`**, with a `flow` **choice** param whose `choices` are empty at registration and filled **per turn** from `visible_flows(principal)`, plus an `input` object. The runner loads the row by slug at call time. `flow_step_roles` becomes `flow_row_roles`, reading rows at plan time — legal because `plan_turn` already touches the database.

This **retires P3-D2 and P3-D3**, and it retires them because the owner was right and the plan was wrong: D2's argument ("`ready()` cannot touch the database, so a row cannot become a tool") was an argument about the *tool*, not about the *flow*, and one generic tool with per-turn choices dissolves it. The engine is unchanged. Carried by **P3-D11** (the per-turn narrowing) and **P3-D10** (the three-module split).

### Ruling 2 — consent-gated seeding

**No automatic database writes anywhere.** Code-declared residents — agents and flows alike — are a **catalogue** of what the platform offers. A page declares its resident dependencies; when they are missing it renders an honest empty state with an **"Add the default X"** CSRF-protected POST button that works with JavaScript off, calling `install_default(kind, slug, principal)` — create-if-absent, idempotent, never overwriting. `manage.py sync_agents` is **deleted** and replaced by `manage.py install_defaults [--reset <slug>]`, the explicit CLI equivalent. The chat picker lists installed agents and, separately, shipped defaults not yet installed, each with an Add button. Task 14's live ladder proves the button path (checklist items 14–17). Carried by **P3-D12**; P2's **D5** is superseded and P2's **D8** is resolved by `general` granting `flow.run`.

### Ruling 3 — user-owned residents

The resident edit-lock leaves `Agent.save()`: `_from_resident_sync` is deleted, `_RESIDENT_MUTABLE_FIELDS` and `_refuse_resident_edit` with it, and the `delete()` override goes (`Conversation.agent`'s `PROTECT` was doing the real work). `resident=True` becomes an **origin marker**; **`slug` stays immutable** because it is a key three things resolve. `install_defaults --reset <slug>` restores the shipped default and is the only path that ever rewrites an existing row. P2's tests (`test_models.py:81-110`, `test_resident.py`) are rewritten in Task 1, the task that makes the change. Carried by **P3-D13**; P2's **D6** is retired.

### Ruling 4 — auth-ready seams, accounts off

1. `agents/chat/principal.py::principal_for_request(request) -> Principal` is the **only** request→principal point; open mode returns `Principal("open", "box")`, and `"open"` joins `PRINCIPAL_KINDS` (a pure change in `agents/contracts`).
2. `Agent` and `Flow` gain `owner_kind`/`owner_key` beside `Conversation`'s; **every create stamps the acting principal**, including a default adopted from the button.
3. **One visibility function per kind** in `agents/visibility.py` — `visible_conversations`, `visible_agents`, `visible_flows`, plus `owner_fields` for creates. Every list, detail, POST, and delete goes through them, and a guard fails the build if any module under `agents/chat` touches `Conversation`/`Agent`/`Flow` `.objects` directly.
4. `settings.ACCOUNTS_REQUIRED = False` (env-driven) is the branch point `principal_for_request` reads. **The `True` path raises `NotImplementedError` naming Identity & Auth** rather than degrading to the open principal — a setting whose `True` branch quietly returned an unauthenticated principal would be a security hole wearing a setting's name.

In open mode every one of these returns what P3 would have returned anyway, so **ruling 4 changes no behaviour today**. That is precisely why it is worth doing now and why it is guarded rather than documented: Identity & Auth edits four functions instead of auditing a package. Carried by **P3-D14**.

### What this cost, stated plainly

Two tasks of new work (the data model was already a task; the seams and the catalogue are new), one migration instead of one migration, and the deletion of `manage.py sync_agents` along with a sweep of every doc that names it. The plan's own two flow deviations are gone, which makes it **closer to the spec than the reviewed version was** — §7.4's `Flow` row and §6.5's single `flow.run` are both restored. The rulings that remain deviations from the spec are §7.5's automatic sync (ruling 2 forbids it) and §7.5's non-editable resident (ruling 3 forbids it), and both are the owner overruling a spec written before an operator had ever adopted a default.

**Round A review findings, applied.**

| # | Finding | Applied |
|---|---|---|
| A1 | Task 1 adds a model but never touched `EXPECTED_TABLES`, so `foundation/ops/tests/test_app_labels.py` would go red on a pin whose whole point is that it is a hand-typed literal. | Files list gains that module; a step adds `"agents.flow": "agents_flow"` **after watching it go red**, per that file's own red-run discipline. Task 3's "EXPECTED_TABLES below is unchanged" comment is corrected — it is true of `agents.chat`, not of P3. |
| A2 | Deleting `_from_resident_sync` in Task 1 leaves `agents/resident.py:171,176` calling `save()` with a keyword that no longer exists — `TypeError` on the first sync, P2's suite red inside a task about the data model. | Task 1 strips both kwargs (behaviour-neutral: the bypass existed only to pass the guard being removed). The FUNCTION still dies in Task 5, with its command and its docs. |
| A3 | `agents/runtime/flowtool.py` is both runtime code and a registration module, and joined neither list. | Task 13 adds it to **both** `RUNTIME_MODULES` and `_REGISTRATION_MODULES` in the commit that creates it; Task 14 verifies and adds neither. |
| A4 | The plan carved a hole in ruling 4c's guard for `Conversation.objects.create`. | **RULING applied:** `visibility.create_conversation(principal, agent)` owns the create and stamps `owner_fields`. The guard is a flat per-module exclusion naming `agents/visibility.py` alone — a guard with an exception is a guard somebody widens, and the ownership stamp belongs where ownership is decided. |
| A5 | D5 still described `run_flow` reading `ctx.tool_key`, which ruling 1 retired. | Rewritten for `args["flow"]` + the row load through `visible_flows`; the Task 12 test that named `ctx.tool_key` now names an unknown/disabled row, driven through the visibility layer. |
| A6 | `input` was specified as a dict, which the tool contract cannot express — there is no object param kind. | **RULING applied:** `input` is a **JSON string**, `run_flow` parses it, a dict is accepted as-is, and malformed JSON is a `ParamError` naming `input` (repairable, one retry) rather than a refusal. `_parsed_input` is written out; both paths pinned; the description says "a JSON object written as a string" and shows one. |
| A7 | The pre-auth section still said owner columns stay blank and named `ChatIndexView`'s queryset as the line that changes — both contradicted by ruling 4. | Replaced with the ruling-4 story: one principal point, `OPEN_PRINCIPAL` stamped on every create, `agents/visibility.py` as the filter point for the page **and** the runtime. |
| A8 | The registration constraint still named `agents/flows.py` and "the flow tool specs" (plural). | Rewritten: `ready()` imports `agents.runtime.flowtool` and registers **one** `FLOW_RUN`; its module-scope imports are the two pure leaves; `agents.visibility` is lazy inside `narrowed_flow_spec`; the blacklist keeps `runtime.flow`, `runtime.loop`, `agents.models`, `agents.chat.*`, and `tools/*`. |
| A9 | Two modules independently constructed `Principal("open", "box")`. | **RULING applied:** `OPEN_PRINCIPAL` lives in `agents/contracts/tools.py` beside the kind it is built from; `principal.py` and `install_defaults` import it; Task 4 adds an AST guard that `Principal(` is constructed only in `agents/contracts/tools.py`, `agents/chat/principal.py`, `agents/runtime/bindings.py`, and tests. |

**minor (all 11 applied)** — counts corrected (fourteen tasks; twelve live and two retired deviations; the Goal line now says flows are rows and nothing is seeded automatically); the `resolve_args` docstring cites `agents/defaults.py`'s validator rather than the retired `agents/flows.py`; the rounds 1–2 review block is marked **superseded in part** with a pointer to the amendment; the consistency table's flow rows say Task 12 and take the ROW (`_finished(flow, steps, …)`, `_degraded(flow, steps, …)`, `_parsed_input`); Task 6's `conversation_start` docstring names `create_conversation`, and its test module gains `Agent`, `OPEN_PRINCIPAL`, and a written-out `_offers_section` helper, with `test_defaults_view.py` folded into `test_index.py` (the offers list and the Add button are the index's own contract); Task 1 names the `ProtectedError` import path and the explicitly-named `agents_agent_owner` index; Task 5 gains a `TestPurity` class for `agents/defaults.py`; **RULING** recorded that `visible_flows` is asked with the ACTING principal — the delegate's own inside a delegation — and that flipping `ACCOUNTS_REQUIRED` therefore changes what an agent can run, not only what a page lists; **RULING** recorded as named gap **P3-G1**: `agent.<slug>` stays catalogue-registered, row-driven `agent.run` with per-turn choices is the P4/auth-phase symmetry item because delegation is a grant decision, and it is not built now; smoke item 6 folded into item 18.

### Round B — CLEAN. Three residuals corrected.

R1 the principal-construction guard says **three files** (its exclusion set has three entries; tests are exempt via `_is_test_file`), and the test is renamed `test_a_principal_is_only_constructed_in_the_three_files_that_may`. R2 `manage.py install_defaults` **imports** `OPEN_PRINCIPAL` from `agents/contracts/tools.py` rather than constructing one — an explicit import step, and a docstring saying "imports, never constructs" with the reason (it is deliberately absent from `_PRINCIPAL_CONSTRUCTORS`). R3 P3-D1's "`EXPECTED_TABLES` is untouched" is scoped to the `chat` label, with a pointer to Task 1's `agents.flow` entry (A1).
