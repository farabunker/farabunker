# Agents P2 — Agent Data, the Turn Runtime, and Resident Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `agents/` a real Django app and give it a turn runtime. An `Agent` row binds a system prompt, an LLM role, and a set of granted tool keys; a `Conversation` holds `Turn` rows; every tool call writes its own `ToolInvocation` audit row. One queue job kind — `agent.turn` — runs a bounded ReAct loop inline: build the prompt from history, offer the granted tools as JSON schemas, take one tool call per step, run it through the one validation floor, feed the result back, and stop honestly when the budget or the deadline runs out. Three resident agents are declared in code and synced to rows. `manage.py agent_turn` is the proof surface. No `/chat/` page, no flows, no `Flow` model — those are P3. P2 ends with a turn that provably runs a RAG tool call and a vision tool call end to end from a shell, and with the `ChatSession`/`ChatMessage` tables gone.

**Architecture:** Fourteen tasks, bottom-up. Tasks 1–3 build the app shell, the four models with their one migration, and the `chat.converse` role. Tasks 4–5 widen the P1 contract in the two places the addendum requires: `Principal` replaces `ToolContext.agent_key`, `granted_tools` becomes the single availability decision, and `mcp_tool_dict` joins `openai_tool_dict` behind a drift pin. Tasks 6–9 build `agents/runtime/` as a private package inside the app — prompt building, `invoke_tool` (outcome classification plus the audit row), the loop, and the job wiring including `on_turn_terminal`. Tasks 10–11 add the code-declared resident agents and agent-as-tool. Task 12 is the CLI proof surface with its preflight. Task 13 retires `ChatSession`/`ChatMessage` and the `answer_question` parameter that fed them. Task 14 installs the structural guards, sweeps the docs, and runs the gates and the ladder.

**Tech Stack:** Python 3.12/3.13, Django 5, PostgreSQL on the branch preview port 5433, pytest + pytest-django, the installed `llama-index-core` / `llama-index-llms-ollama` / `ollama` packages (versions re-recorded in Task 6 against the tree, never predicted). No new Python dependencies: `requirements.txt` pins only floors, and every wire shape this phase needs is already in the installed integration.

**Spec:** docs/superpowers/specs/2026-08-25-agents-and-tools-design.md — §6, §7, §10, §11, §12.3, its "Corrections from plan authoring (2026-08-25)" section, and its "Long-term requirement: enterprise control and MCP interop (2026-08-27)" addendum.

## Global Constraints

- **Depends on P0 and P1 having landed.** Both are merged at `33c41c5`. Every path in this plan is written against the **post-P1** tree: `models/contracts/`, `models/registry/`, `models/queue/`, `tools/rag/`, `tools/vision/`, `foundation/`, `agents/contracts/`. Re-verify every `file:line` citation against the tree before executing a task — a citation that has moved is a signal to re-read, not to guess.
- **Orchestrator gates — the full matrix, every task, no exceptions.** Two `FARABUNKER_FEATURES` states × two collection orders:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q                                            # configured collection order
  .venv/bin/pytest -q scripts agents foundation models tools     # reversed
  ```

  `pytest.ini` already reads `testpaths = tools models foundation agents scripts` (P1 Task 2 put `agents` there). **P2 changes it not at all** — every new test in this phase lands under `agents/`, `tools/`, `models/`, or `foundation/`, all already collected. A step that appears to need a `testpaths` edit is a step that put a test in the wrong place.
- **Private test DB per role. Never `test_farabunker`.** Implementers use `farabunker_impl` on the branch preview Postgres at **5433** (`docs/DEV.md:213-232`). Two sessions on the same test database race each other's create/drop lifecycle. Never a bare `test_farabunker`, never `5432`.
- **`agents/contracts/` still imports NO Django.** Task 4 adds `Principal` and rewrites `ToolContext` **inside that pure leaf**. `Principal` is a frozen dataclass of two strings — no `models.Model`, no `TextChoices`, no `django.db`. `agents/contracts/tests/test_purity.py` (P1 Task 10) imports the whole package in a subprocess with **no `DJANGO_SETTINGS_MODULE` set at all**; it will catch a slip on the first run. The new `agents/` app code (`apps.py`, `models.py`, `runtime/`, `resident.py`) is Django code and is a different thing entirely — it lives outside `contracts/` and is never imported by it.
- **No `conftest.py`, anywhere.** `find . -name conftest.py` returns nothing and must keep returning nothing. New per-package helper modules this phase: **`agents/tests/_helpers.py`** (Task 3) and **`agents/runtime/tests/_helpers.py`** (Task 6). Autouse fixtures stay *defined* per test module and delegate their bodies to `_helpers`. **Helpers are duplicated per app, never imported across apps** — `make_job_ctx` already exists four times (`tools/rag/tests/_helpers.py:76`, `tools/vision/tests/_helpers.py:56`, `models/registry/tests/_helpers.py:75`, `agents/contracts/tests/_helpers.py:26`) and `snapshot_tools`/`restore_tools` likewise (`tools/rag/tests/_helpers.py:116,128`, `tools/vision/tests/_helpers.py:96,108`, `models/registry/tests/_helpers.py:127,139`, `agents/contracts/tests/_helpers.py:90,106`). **P1's review left "four copies of the snapshot/restore helpers" as an open item; this plan's ruling is that they stay per-app copies.** A shared fixture package would make one app's test scaffolding load-bearing for another's, and each copy is eight lines. `agents/tests/_helpers.py` gets its own copy of what it needs and imports nothing from **another app's** tests. Within the `agents` app, `agents/runtime/tests/_helpers.py` **does** import `make_job_ctx` / `make_budget` / `make_principal` / `snapshot_tools` / `restore_tools` / `bind_chat_role` from `agents/tests/_helpers.py` — that is one app, not two, and a sixth copy of `make_job_ctx` inside the same app would be duplication with no boundary to justify it. Each `_helpers.py` ships **only what a test in its own package actually calls**; a helper written before its caller is dead code with a docstring.
- **A fixture in `_helpers.py` is not visible until a test module imports it by name.** There is no `conftest.py`, so pytest never discovers a fixture defined in a helper module. Every test module that uses one writes an explicit re-export at the top — `from agents.tests._helpers import bound_chat_role  # noqa: F401` — and that import IS the registration. A fixture that is used but not imported fails with `fixture 'x' not found`, which is the honest failure; a fixture that is imported but unused is caught by the `# noqa: F401` marker being unnecessary. This applies to every fixture named in this plan: `bound_chat_role`, `bound_embed_role`, `fake_queue`, `fake_queue_with_tool`, `fake_failed_queue`, `fake_running_queue`.
- **THE VISION-FLAG RULE** (`tools/rag/tests/_helpers.py:9-38`, enforced by `tools/rag/tests/test_flag_hygiene.py`, which sweeps `tools`, `models`, `foundation`, and `agents`): any test that overrides `settings.FARABUNKER_FEATURES` (directly, via the `settings` fixture, or via `override_settings`) **AND** performs an actual HTTP request or URL resolution in that same test (Django's test `Client`, `reverse()`) **MUST** keep `"vision"` in the overridden set. `config/urls.py` builds its `vision/` mount conditionally, once, at import time; Django resolves the URLconf lazily on the first request/`reverse()` in the process and never re-evaluates it, so one unlucky test poisons every later one. No test in this phase needs to override the flag at all — Task 10's tolerant-key test deletes an entry from the tool registry under a snapshot/restore fixture instead of turning a feature off, which is both narrower and does not touch URL resolution. **That is the preferred shape whenever a test's real subject is the registry rather than the install.** If a later task does override the flag and also touches `Client`/`reverse()`, it must keep `"vision"` in the set.
- **Never-500, and never a fabricated answer.** Three separate obligations in this phase, and all three are honesty obligations, not defensiveness:
  - `run_turn` writes its own `Turn(state=FAILED, error=str(exc))` before re-raising (§6.2 step 8) — a traceback never reaches a row.
  - The loop's terminal states are honest sentences, never invented content: budget exhausted, deadline expired, and "the bound model cannot call tools" are each a fixed sentence plus whatever the last tool actually returned.
  - `on_turn_terminal` and `summarize_turn` must never raise. `invoke_on_terminal` catches and logs (`models/contracts/jobkinds.py:291-320`) and `models/queue/views.py::_summarize` does the same for summarizers — but a hook that relies on its caller's tolerance is a hook that will one day be called by something less tolerant.
- **No model names or versions.** Not in code, not in a `ToolSpec.description`, not in an `AgentSpec.system_prompt`, not in a comment, not in a test name, not in this plan. The repository is going public and ADR 0010's third amendment (`docs/adr/0010-model-management-framework.md:290-380`) forbids the platform from naming a model for the operator. A resident agent's system prompt describes *what the agent does*, never *what model backs it*.
- **ADR import law, post-regroup (spec §3.3), with P2's own reading of rule 2:**
  - **Rule 1 — pure leaves are universally importable.** `foundation/format.py`, `foundation/files.py`, everything under `models/contracts/`, and everything under `agents/contracts/`. Any column, any direction.
  - **Rule 2 — Django apps are column-private.** `tools/rag`, `tools/vision`, `models/registry`, `models/queue`, `foundation/ops`, `foundation/setup`, and **now `agents`** are not importable across a column boundary, with exactly one exception: a `tools/*` **or** `agents/*` app MAY import `models.registry.bindings`, and only that module. `models.registry.models` and `models.registry.views` stay off-limits, with no exception. `agents/runtime/` therefore reaches ordinary role resolution through `models.contracts.bindings.resolve` (rule 1) and the picker-override path through `models.registry.bindings.resolve_connection_named` (`models/registry/bindings.py:317`) — and nothing else in `models/registry`.
  - **Rule 3 — cross-column *work* goes through a seam, never an import.** The queue (`models.contracts.queue`), the gateway (`models.contracts.gateway`), and the tool registry (`agents.contracts.tools`), whose `ToolSpec.runner` is a dotted-path string resolved at call time by `models.contracts.jobkinds.resolve_dotted_path` (`jobkinds.py:273`). **`agents/runtime/` never imports `tools.*` — not at module scope and not in a function body** — it reaches every tool through `resolve_dotted_path`. Task 14 pins both halves.
  - **Never `import models`.** Always `from models.<sub> import ...`.
- **Registration imports no implementation module.** `AgentsConfig.ready()` registers a role, a job kind, and (Task 11) the agent-as-tool specs. It may import `agents.resident` (pure data, no Django) and `agents.contracts.tools` (pure). It must **not** import `agents.runtime.loop`, `agents.runtime.jobs`, `agents.models`, or anything under `tools/` — the `planner`/`handler`/`summarizer`/`on_terminal`/`runner` fields are dotted-path **strings** for exactly that reason (`models/contracts/jobkinds.py:16-26`, and the code comment at `tools/vision/apps.py:51-55` which says so outright). **Django forbids DB access in `ready()`**, so `sync_resident_agents()` is called from a management command, never from `ready()` — deviation D5 below.
- **A tool runner must never block on a queue job.** It may enqueue one and return its id; it must never call `get_job` in a loop, and it must never call `enqueue` for work whose result it needs. On a default install `JobSettings.memory_budget_bytes` is `null` (`models/queue/models.py:184-192`), which `models/queue/scheduler.py:375` reads as sequential mode — at most one job on the whole machine — so a job that waits on a job it enqueued deadlocks with certainty, not probability. **P2 adds `agents/runtime/` to that rule's swept list** (Task 14) and one carefully-argued exception outside it: `manage.py agent_turn` (Task 12) polls `get_job`, and **a management command is not a tool runner** — it holds no execution slot; it is the thing waiting *outside* the queue for the queue to finish.
- **Post-deadline latency is real and is documented, not fixed.** `budget.expired` is checked at the top of every loop iteration and before every tool call — never *during* one. A `vision.generate` call that enters `services.wait_for` just under the deadline can return well past it (vision's own `GENERATE_WAIT_TIMEOUT_SECONDS` is 600s). `agents/runtime/loop.py`'s module docstring says so in as many words: **a tool call may return after the deadline, and the turn then ends immediately with the honest deadline sentence plus that tool's actual result.** The alternative — killing a tool mid-flight — would strand a submitted generation and lose work that was actually done.
- **Tests and docs ship with every task** (ADR 0008, `docs/adr/0008-engineering-standards.md:15-30`). TDD: failing test first, run it and read the failure, minimal implementation, run it green, docs, commit.
- **Doubles are seam-level, never method-level.** The LLM double (`FakeToolLLM`, Task 6) is patched in at `models.contracts.gateway.get_llm_for` — where `docs/DEV.md:181-186` says every test already mocks the gateway — and it implements the same two methods the real integration exposes (`chat(messages, tools=None)` and `get_tool_calls_from_response(response, error_on_no_tool_call=False)`), returning the same types. A tool double is a real registered `ToolSpec` whose runner is a module-level function, registered inside a fixture that snapshots and restores `_TOOLS`.
- **Baseline: measure it, do not predict it.** Task 1 starts by recording `pytest -q --collect-only | tail -1` on the merged tree; that number is this phase's floor. Every later task's expectation is a **delta**: the whole suite green, and the collected count up by exactly what that task's own per-file run reported. Never a hand-computed total — `@pytest.mark.parametrize` expands, so an arithmetic prediction is wrong more often than the code is. A count that goes *down*, or that differs between the configured and reversed collection orders, is a real defect and must be chased.
- **Migrations: expect exactly TWO.** `agents/0001_initial` (Task 2 — all four models in one migration) and `tools/rag/0013_retire_chat_tables` (Task 13 — two `DeleteModel`s). Task 10 adds no third: the resident-agent sync is a management command, not a data migration, for the reason Task 10 states. If a step appears to need any other migration, STOP and report.
- **Branch-only.** No commits to `main`, no merges to `main`, no deploy from Tasks 1–13. Task 14 owns the ladder.
- **Commit trailers.** Every commit ends with:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```
- **Verification doctrine.** No "done"/"works"/"fixed"/"complete" language about the turn runtime until Task 14's ladder finishes with fresh pixels on the owner's live system. P2 ships no page, so its Rung 3 is the CLI proof rung — `manage.py agent_turn` against the live stack, plus every existing page still rendering — but that is still a rung and it is still driven on the live box.

### VISION-OWNED work, and what P2 must NOT touch

P1's review left two vision items open. **Both are VISION-OWNED and out of this plan's scope. Note them; do not plan them; do not fix them in passing.**

| Item | Status |
|---|---|
| `tools/vision/tools.py`'s double preflight — `run_operations` (`:363-365`) and `run_generate` (`:397,427`) each reach `preflight`/`VisionUnavailable` independently | VISION-OWNED. P2 calls `vision.generate` through the registry like any other tool and does not reshape it. |
| `build_generate_spec()`'s rebuild-on-registration (`tools/vision/tools.py:215`) — the spec is constructed from `all_operations()` at `ready()` time | VISION-OWNED. P2 reads whatever is registered and never rebuilds it. |

P2 touches exactly three files under `tools/`: `tools/vision/tests/_helpers.py` and `tools/rag/tests/_helpers.py` (Task 4, the `ToolContext` field rename), plus `tools/rag/`'s retirement surface in Task 13. It changes no vision runner.

### The five deviations from the spec, and why

The spec was written before P0/P1 landed and before the 2026-08-27 addendum. Where the owner's P2 direction and spec §7 disagree, **the direction wins** and the deviation is recorded here rather than re-argued in each task.

| # | Spec says | P2 does | Why |
|---|---|---|---|
| D1 | New Django app `agents/runtime`, `name = "agents.runtime"`, `label = "agents"` (§7 preamble) | The app is **`agents` itself** — `agents/apps.py`, `name = "agents"`, `label = "agents"`, models in `agents/models.py`. `agents/runtime/` is a **private package inside it**, not an app. | One column, one app, one `agents/migrations/` directory — and the migration is `agents/0001_initial` either way. `agents/contracts/` is already a subpackage of `agents/` and stays a pure leaf the app never imports. P3's `agents/chat` can still be its own app with its own label if it wants one. |
| D2 | `Agent.key` / `.label` / `.role_key` (§7.1) | `Agent.slug` / `.name` / `.llm_role` | The owner's P2 field names. `description` and `max_steps` are **kept** from §7.1 — `max_steps` is load-bearing (`StepBudget(steps=agent.max_steps)`, §6.2 step 1) and dropping it would hard-code the budget. |
| D3 | Four residents: `general`, `librarian`, `illustrator`, `critic` (§7.5) | **Three**: `general`, `library`, `illustrator` | The owner's P2 direction. `critic` was the self-delegation worked example; agent-as-tool is still built and still proven (Task 11), with `library` and `illustrator` as the delegates. |
| D4 | `agent.delegate` is **one** code-registered tool with an `agent` param, "because tools are code-registered; agents are rows" (§6.4) | **`agent.<slug>`, one spec per code-declared resident**, registered from `agents.resident.RESIDENT_AGENTS` | The owner's P2 direction, reconciled with the spec's own constraint: the specs are built from **code**, never from rows, so `ready()` still touches no database. A user-built agent is **not** exposed as a tool in P2 — that needs either DB access at `ready()` (forbidden) or a dynamic registry (not designed). A named deferral, not an oversight. This is also what forces `ToolContext.tool_key` (Task 4): a runner's signature is `(args, ctx)`, and N specs sharing one runner cannot otherwise learn which spec invoked them. |
| D5 | `sync_resident_agents()` has two callers: a data migration and `manage.py sync_agents` (§7.5). The P2 brief says "called from `ready()`". | **`manage.py sync_agents` only.** `ready()` does not call it, and no data migration does either. | Django forbids DB access in `AppConfig.ready()`, and §7.5 says so itself. A tolerant `ready()` that swallowed the resulting error would be a hook that silently does nothing on a fresh box — worse than not having one. A data migration would freeze today's resident list into history and re-run nothing when the list changes. The deploy step is `manage.py migrate && manage.py sync_agents`, documented in Task 14. |
| D6 | §7.5: `sync_resident_agents` "needs **no bypass** of §7.1's `save()` validation, and must not have one" | `Agent.save()` grows exactly one bypass: the **keyword-only** `_from_resident_sync: bool = False` | §7.5 wrote that sentence about the `tool_keys` rule, which genuinely needs no bypass — and P2 gives it none. But §7.5 also makes a resident row non-editable, and those two rules together make its own suggested `update_or_create` impossible: `update_or_create` calls `save()` itself and cannot pass a keyword through. The bypass is therefore for the *resident-edit guard*, not for validation; it is keyword-only, greppable, has exactly one caller (`sync_resident_agents`), and a test pins that an ordinary `save()` on a resident row still refuses. |
| D7 | §6.1's payload carries `"mode": "chat" \| "flow"` **and** `"flow": "<flow key>" \| None` | The payload carries `"mode"` (one legal value, `"chat"`) and **no `"flow"` key at all** | A key with exactly one possible value — `None` — is not forward compatibility, it is a field nobody can read honestly. `"mode"` is kept because P3 adds a second legal value to an existing key; `"flow"` is added by P3 in the same commit that gives it a meaning. Neither needs a payload migration: a queue payload is a JSON dict, and a handler that does not read a key is unaffected by its arrival. |
| D8 | §7.5's `general` grants `flow.run` | `general` grants no `flow.run` | `flow.run` is registered in P3 (§12.4). Ruling R1 would tolerate the key — it is written verbatim and dropped at prompt time — but writing it now would put a key in a shipped row that this phase cannot test end to end, and the row is re-synced by `manage.py sync_agents` on the deploy that ships P3 anyway. The tolerant path is proven in Task 10 against a real registered-then-removed tool instead (M5). |

### Flows are P3, and this plan builds none of them

Spec §6.5, §7.4, and §12.4 put `Flow`, `agents/runtime/flows.py`, `resolve_ref`, `run_flow`, and the `flow.run` tool in **P3**. P2's `agent.turn` payload declares a `"mode"` key with exactly one legal value, `"chat"`, so P3 adds `"flow"` without a payload migration. Nothing else about flows appears in this plan, and a task that starts writing one has gone out of scope.

---

### Task 1: `agents` becomes a Django app

The column has been a plain package since P0 and a pure leaf's home since P1. It becomes an app now because Task 2 needs a migrations directory and Task 3 needs a `ready()` to register a role from. Nothing else changes in this task: no models, no registrations, no runtime.

The one thing to get right is that **`agents/contracts/` must stay importable with no Django configured**. Making the *parent package* an app does not change that — Django imports `agents.models` and `agents.apps`, never `agents.contracts` — but only as long as `agents/__init__.py` stays a docstring and nothing else. A `default_app_config`, an import, or any executable statement there would be evaluated on `import agents.contracts.tools` and could drag Django into the pure leaf.

**Files**
- Create: `agents/apps.py`
- Create: `agents/tests/__init__.py`, `agents/tests/test_apps.py`
- Modify: `config/settings.py` — `INSTALLED_APPS`
- Modify: `agents/__init__.py` — the P0/P1 docstring now describes an app
- Modify: `agents/README.md` — the `runtime/` row

**Interfaces**
- Consumes: `django.apps.AppConfig`.
- Produces: `agents.apps.AgentsConfig` — `name = "agents"`, `label = "agents"`, `default_auto_field = "django.db.models.BigAutoField"`, `ready()` present and empty (Task 3 fills it).

**Steps**

- [ ] **Measure the baseline. Do not predict it.** This number is the phase floor; every later task's expectation is a delta from it.

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q --collect-only | tail -1
  ```

  Record the number in the task notes. At authoring time (2026-08-27, HEAD `33c41c5`) it read **2960 tests collected**; confirm it rather than trusting it.

- [ ] Failing test first. Create `agents/tests/__init__.py` (empty) and `agents/tests/test_apps.py`:

  ```python
  """`agents` is a Django app, and making it one did not cost the pure leaf.

  Two properties, deliberately in one module: the app is installed under
  the label every migration and every `apps.get_model` call will use, and
  `agents/contracts/` is still reachable without Django being configured
  at all. The second is not paranoia -- `agents/contracts/tests/
  test_purity.py` proves it in a subprocess, and this asserts the ONE
  thing that would break it here: `agents/__init__.py` must stay inert.
  """
  from __future__ import annotations

  import ast
  from pathlib import Path

  from django.apps import apps
  from django.conf import settings


  def test_the_agents_app_is_installed_under_the_agents_label():
      config = apps.get_app_config("agents")
      assert config.name == "agents"
      assert config.label == "agents"


  def test_agents_package_init_is_inert():
      """`import agents.contracts.tools` evaluates `agents/__init__.py`
      first. If that file ever grows an import or a statement, the rule-1
      pure leaf below it inherits whatever that import drags in. A
      docstring is the only body this file may have."""
      source = (Path(settings.BASE_DIR) / "agents" / "__init__.py").read_text(encoding="utf-8")
      body = ast.parse(source).body
      assert len(body) == 1, [type(node).__name__ for node in body]
      node = body[0]
      assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
      assert isinstance(node.value.value, str)
  ```

- [ ] Run it and read the failure — `LookupError: No installed app with label 'agents'`:

  ```bash
  .venv/bin/pytest -q agents/tests/test_apps.py
  ```

- [ ] Create `agents/apps.py`:

  ```python
  """The `agents/` column's Django app (spec section 7, deviation D1).

  ONE app for the whole column, `label = "agents"`, so there is one
  `agents/migrations/` directory and `apps.get_model("agents", "Agent")`
  resolves from a data migration, a management command, and a test
  alike. The spec's section 7 preamble named `agents.runtime` as the app;
  the owner's P2 direction makes the column root the app and
  `agents/runtime/` a private package inside it. Both produce the same
  label and the same `agents/0001_initial`.

  `ready()` registers the `chat.converse` role (Task 3), the `agent.turn`
  job kind (Task 9), and the agent-as-tool specs (Task 11). It imports NO
  implementation module: every planner/handler/summarizer/on_terminal/
  runner is a dotted-path STRING, resolved lazily by
  `models.contracts.jobkinds.resolve_dotted_path` -- the same reason
  `tools/vision/apps.py:51-55` gives for its own job kind. And it touches
  NO database: Django forbids that here, which is why resident-agent sync
  is `manage.py sync_agents` and not a call from this method (deviation
  D5).
  """
  from __future__ import annotations

  from django.apps import AppConfig


  class AgentsConfig(AppConfig):
      default_auto_field = "django.db.models.BigAutoField"
      name = "agents"
      label = "agents"

      def ready(self) -> None:
          """Empty in Task 1 -- the app shell only. Filled by Tasks 3, 9, 11."""
  ```

- [ ] Add the app to `config/settings.py`'s `INSTALLED_APPS`, **after** `models.queue` and before `foundation.setup` — the column order the four-column tree reads in (`tools`, `models`, `agents`, `foundation`):

  ```python
      "tools.rag",
      "tools.vision",
      "models.registry",
      "models.queue",
      "agents",
      "foundation.setup",
      "foundation.ops",
  ```

- [ ] Replace `agents/__init__.py`'s body with a docstring that is true post-P2-Task-1. **A docstring and nothing else** — the test above enforces it:

  ```python
  """The `agents/` column: the central brain.

  A Django app since P2 (`agents/apps.py`, label `agents`), holding:

  - `contracts/` -- the P1 TOOL CONTRACT. A rule-1 PURE LEAF: no Django,
    universally importable, in any direction. The app never imports it
    from `models.py` or `apps.py` in a way that could reverse that.
  - `models.py`  -- Agent, Conversation, Turn, ToolInvocation (P2).
  - `runtime/`   -- the turn runtime: prompt building, invoke, the
    bounded loop, the job wiring (P2). A PRIVATE package: nothing outside
    `agents/` imports it.
  - `resident.py`-- the code-declared resident agents (P2). Pure data, no
    Django, so a management command and a test can both read it.

  `agents/runtime` reaches a tool's implementation through a dotted-path
  STRING resolved at call time (import-law rule 3), never a module-scope
  import of `tools/*` -- exactly as an `AppConfig.ready()` today
  registers a job kind without ever importing its handler.

  THIS FILE MUST STAY A DOCSTRING AND NOTHING ELSE. Importing
  `agents.contracts.tools` evaluates it first, and the pure leaf below
  inherits anything imported here. Pinned by
  `agents/tests/test_apps.py::test_agents_package_init_is_inert`.
  """
  ```

- [ ] Run green, then the full matrix:

  ```bash
  .venv/bin/pytest -q agents/tests/test_apps.py
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  .venv/bin/python manage.py check
  ```

  Expect: green, collected count up by exactly what the per-file run reported, and `manage.py check` silent. **`manage.py makemigrations --check --dry-run` must also be clean** — Task 1 adds no model, so an app with no `models.py` and no `migrations/` produces no migration state.

- [ ] Update `agents/README.md`'s table: the `runtime/` row's phase column becomes "P2 — in progress", and its description becomes "A private package inside the `agents` app (not its own Django app — deviation D1)". Leave the `chat/` row alone.

- [ ] Commit:

  ```bash
  git add agents/apps.py agents/__init__.py agents/README.md agents/tests config/settings.py
  git commit -m "$(cat <<'EOF'
  feat(agents): the column becomes a Django app

  One app for the whole column, label `agents`, so there is one
  migrations directory and `apps.get_model("agents", ...)` resolves the
  same way from a migration, a command, and a test. Spec section 7's
  preamble named `agents.runtime` as the app; the owner's P2 direction
  makes the column root the app and `agents/runtime/` a private package
  inside it -- same label, same `agents/0001_initial`.

  `ready()` is present and empty. It will register a role, a job kind,
  and the agent-as-tool specs, and it will import no implementation
  module and touch no database while doing it.

  `agents/__init__.py` is pinned to a docstring and nothing else: it is
  evaluated on `import agents.contracts.tools`, so anything imported
  there would be inherited by a rule-1 pure leaf.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 2: the `chat.converse` role, and the four shared limits

The role belongs to P2, not P3, because **an `Agent` row binds to it** — `Agent.llm_role` defaults to it, and Task 3's model cannot be written without the constant. The `/chat/` page that lets an operator pick a per-conversation connection is P3; the role, its registration, and the console rows it produces are here.

Registering it costs zero framework change and that is the point ROADMAP:236-241 predicted: `all_roles()` (`models/contracts/roles.py:91`) is what the `/inference/` console, the Getting-models checklist, and the drift guard all derive from, so a new `RoleSpec` appears in every one of them without any of them being edited. `rematerialize=None` — a chat role does not embed, so there is nothing to re-encode.

This task also creates `agents/limits.py`, the one place the four turn limits live. Spec §6.2 declares them at the top of `loop.py`; that does not work here, because `Agent.max_steps`'s field default (Task 3) and `AgentSpec.max_steps`'s dataclass default (Task 10) both need `MAX_STEPS_DEFAULT`, and `agents/models.py` importing `agents/runtime/loop.py` — which imports `agents/models.py` — is a cycle. A four-constant pure module has no such problem and no dependencies at all.

**Files**
- Modify: `models/contracts/roles.py` — add `CHAT_CONVERSE_ROLE`
- Create: `agents/limits.py`
- Modify: `agents/apps.py` — `ready()` registers the role
- Create: `agents/tests/test_roles.py`
- Modify: `models/registry/tests/test_roles.py` **only if** it pins an exhaustive role list (check first; if it does not, leave it alone)

**Interfaces**
- Consumes: `models.contracts.roles.RoleSpec` (`roles.py:58-77`), `register_role` (`roles.py:82`), `all_roles` (`roles.py:91`), `CAPABILITIES` (`roles.py:19`).
- Produces:
  - `models.contracts.roles.CHAT_CONVERSE_ROLE: str = "chat.converse"`
  - `agents.limits.MAX_STEPS_DEFAULT: int = 8`, `TURN_DEADLINE_SECONDS: float = 900.0`, `HISTORY_TURNS: int = 20`, `MAX_AGENT_DEPTH: int = 2`
  - `AgentsConfig.ready()` registers `RoleSpec(CHAT_CONVERSE_ROLE, "Agent conversation", "chat")`.

**Steps**

- [ ] Failing test first. Create `agents/tests/test_roles.py`:

  ```python
  """`chat.converse` is a registered role, and registering it cost nothing.

  ROADMAP:236-241 predicted this exact outcome -- "the console, the
  Getting-models checklist, and per-role binding then serve it with zero
  framework changes". These tests are what turns that prediction into a
  pinned fact: the role is in `all_roles()`, it declares the `chat`
  capability every chat-capable connection is filtered by, and it has no
  `rematerialize` (a conversation role does not embed, so there is
  nothing to re-encode and inventing a callback would be a lie).
  """
  from __future__ import annotations

  from models.contracts.roles import CHAT_CONVERSE_ROLE, all_roles, get_role


  def test_chat_converse_is_registered_with_the_chat_capability():
      spec = get_role(CHAT_CONVERSE_ROLE)
      assert spec is not None, [r.key for r in all_roles()]
      assert spec.capability == "chat"


  def test_chat_converse_declares_no_rematerialize():
      """A chat role does not embed. `models.registry.drift` resolves
      `rematerialize` for every role that declares one; a role that
      declared a callback it does not need would make the drift guard
      offer an action that does nothing."""
      assert get_role(CHAT_CONVERSE_ROLE).rematerialize is None


  def test_the_role_key_constant_is_the_shared_one():
      """Not a bare literal in `agents/`. `models/contracts/roles.py` is
      the one place both the `models/` column and every feature column can
      reach a single definition -- that file's own comment at :22-29 says
      so for the two RAG roles, and this is the same argument."""
      assert CHAT_CONVERSE_ROLE == "chat.converse"
  ```

- [ ] Run it and read the failure — `ImportError: cannot import name 'CHAT_CONVERSE_ROLE'`:

  ```bash
  .venv/bin/pytest -q agents/tests/test_roles.py
  ```

- [ ] Add the constant to `models/contracts/roles.py`, immediately after `RAG_TRANSCRIBE_ROLE`/`RAG_EXTRACT_ROLE` (`roles.py:46-47`) and before `IMAGE_GENERATION_CAPABILITY`:

  ```python
  # The agent-conversation role, registered by the `agents` app (P2). Lives
  # here beside the role keys above for the same reason they do:
  # `models/contracts/` is the one place both `models/` and every feature
  # column can reach a single definition, and `agents/models.py` needs it
  # as a field default while `agents/apps.py` needs it to register with.
  # ROADMAP:237 named this key years before it existed; it is spelled the
  # same here so the roadmap line and the code agree.
  CHAT_CONVERSE_ROLE = "chat.converse"
  ```

- [ ] Create `agents/limits.py`:

  ```python
  """The four numbers that bound a turn.

  Here, not at the top of `agents/runtime/loop.py` where spec section 6.2
  declares them, for one mechanical reason: `agents/models.py`'s
  `Agent.max_steps` field default and `agents/resident.py`'s
  `AgentSpec.max_steps` dataclass default both need `MAX_STEPS_DEFAULT`,
  and `agents/models.py` importing `agents/runtime/loop.py` -- which
  imports `agents/models.py` -- is a cycle. A module with four ints and
  no imports at all cannot participate in one.

  Pure: no Django, no imports. Read by `agents/models.py`,
  `agents/resident.py`, `agents/runtime/loop.py`, and
  `agents/runtime/delegate.py`.
  """
  from __future__ import annotations

  # LLM calls per turn. A per-agent override lives on `Agent.max_steps`;
  # this is the default that row starts at. Every LLM call spends one,
  # including a call that fails, so a failing loop cannot outrun it.
  MAX_STEPS_DEFAULT = 8

  # Wall clock for one whole turn, monotonic. Deliberately LARGER than
  # vision's own `GENERATE_WAIT_TIMEOUT_SECONDS` (600.0,
  # `tools/vision/jobs.py`) so exactly one image generation fits inside a
  # turn without the turn's own deadline being the thing that ends it.
  # Checked at the top of each loop iteration and before each tool call,
  # never DURING one -- see `agents/runtime/loop.py`'s docstring on
  # post-deadline latency.
  TURN_DEADLINE_SECONDS = 900.0

  # How many prior turns of a conversation are replayed into the prompt.
  # A fixed cap, NOT a token budget: the bound model's `context_window` is
  # an operational bound the platform already owns
  # (`ModelConnection.context_window`, ADR 0010:412-435), and a token
  # counter here would be a second, drifting one.
  HISTORY_TURNS = 20

  # The root turn is depth 0; an agent-as-tool call is depth 1; its own
  # delegate is depth 2; no deeper. The depth cap is the belt; the SHARED
  # StepBudget is the braces and is the real guard -- total LLM calls per
  # turn are bounded by `Agent.max_steps` regardless of how the
  # delegation tree is shaped.
  MAX_AGENT_DEPTH = 2
  ```

- [ ] Fill `AgentsConfig.ready()`:

  ```python
      def ready(self) -> None:
          """Register the `chat.converse` role.

          Local import so app import stays light (no DB, no HTTP at
          startup), matching `tools/rag/apps.py` and `tools/vision/
          apps.py`. Not gated on any feature flag: the agent column is not
          optional, and a role that appeared and disappeared with a flag
          would make the console's role list depend on install-time
          configuration in a way `/inference/` has no way to explain.
          """
          from models.contracts.roles import CHAT_CONVERSE_ROLE, RoleSpec, register_role

          register_role(RoleSpec(CHAT_CONVERSE_ROLE, "Agent conversation", "chat"))
  ```

- [ ] Run green, then check the two surfaces that derive from `all_roles()` really did pick it up with no edit — this is the ROADMAP claim, and it is worth one command:

  ```bash
  .venv/bin/pytest -q agents/tests/test_roles.py
  .venv/bin/python manage.py shell -c "from models.contracts.roles import all_roles; print([r.key for r in all_roles()])"
  ```

  Expect `chat.converse` in the printed list beside the RAG and vision roles.

- [ ] Full matrix. **Watch for a role-count assertion turning red anywhere** — `models/registry/tests/` and `foundation/setup/tests/` both render surfaces built from `all_roles()`. If one of them pins an exhaustive list, extend that list; if one pins a *count*, change the count and add a comment naming the role that moved it. Do not weaken an assertion to `>=`:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  ```

- [ ] Commit:

  ```bash
  git add models/contracts/roles.py agents/limits.py agents/apps.py agents/tests/test_roles.py
  git commit -m "$(cat <<'EOF'
  feat(agents): register the chat.converse role, and the four turn limits

  The role belongs to P2, not P3: an Agent row BINDS to it, so the
  constant has to exist before the model can declare its default. The
  page that picks a per-conversation connection is still P3.

  Zero framework change, exactly as ROADMAP:236-241 predicted: the
  console's role rows, the Getting-models checklist, and the drift guard
  all derive from `all_roles()` and picked it up with no edit.
  `rematerialize=None` -- a conversation role does not embed, and
  declaring a callback it does not need would make the drift guard offer
  an action that does nothing.

  `agents/limits.py` holds MAX_STEPS_DEFAULT, TURN_DEADLINE_SECONDS,
  HISTORY_TURNS, MAX_AGENT_DEPTH. Spec section 6.2 puts them atop
  loop.py; models.py and resident.py both need MAX_STEPS_DEFAULT as a
  default, and models.py -> loop.py -> models.py is a cycle. Four ints
  and no imports cannot be in one.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 3: `agents/models.py` — Agent, Conversation, Turn, ToolInvocation, and one migration

Four models, **one** migration (`agents/0001_initial`). The reconciliation against spec §7.1–§7.3 is stated below field by field, because a plan that silently renamed half a data model would leave the next reader unable to tell a decision from a typo.

#### Reconciliation against spec §7.1–§7.3

| Spec field | P2 | Where it lives | Note |
|---|---|---|---|
| `Agent.key` | `Agent.slug` | Agent | D2. Still CI-unique via `UniqueConstraint(Lower(...))`, the house pattern (`tools/rag/models.py` `uniq_category_name_ci`, `models/registry/models.py:169-173`, `:194-198`). |
| `Agent.label` | `Agent.name` | Agent | D2. |
| `Agent.description` | kept | Agent | Operator-facing; never sent to a model. |
| `Agent.system_prompt` | kept | Agent | The whole of what the model is told about who it is. |
| `Agent.role_key` | `Agent.llm_role` | Agent | D2. Default `CHAT_CONVERSE_ROLE` (Task 2). |
| `Agent.tool_keys` | kept | Agent | JSON list. Ruling R1 validation in `save()`. |
| `Agent.max_steps` | kept | Agent | Load-bearing: `StepBudget(steps=agent.max_steps)`. Default `MAX_STEPS_DEFAULT`. |
| `Agent.resident` / `.enabled` | kept | Agent | |
| `Conversation.*` (§7.2) | kept verbatim | Conversation | UUID pk, `agent` FK `PROTECT`, `title`, timestamps, `ordering = ["-updated_at"]`. |
| `Turn.tool_call` five keys | kept verbatim | Turn | `tool` / `args` / `agent` / `id` / `discarded`, §7.3. This is the **prompt-replay** record: what history replay reads to rebuild the message list. |
| `Turn.data`, `.artifacts`, `.depth`, `.state`, `.error`, `.queue_job_id` | kept | Turn | `queue_job_id` is a `BigIntegerField`, **not** an FK — `agents/` may not import `models.queue` (rule 2), the same reasoning `tools/vision/models.py:135-143` records for `GenerationJob.queue_job_id`. |
| — (new, addendum consequence 3) | `ToolInvocation` | its own table | The **audit** record: who called, what ran, how it ended. `Turn.invocation` is a nullable FK to it. Two records, not one, because an external MCP `tools/call` has no `Turn` at all and must reuse this row unchanged. |
| — (new) | `Turn.invocation` | Turn | `ForeignKey(ToolInvocation, null=True, blank=True, on_delete=SET_NULL)`. `SET_NULL`, not `CASCADE`: losing the audit row must never delete the conversation turn that referenced it. |

**Why the duplication between `Turn.tool_call` and `ToolInvocation` is deliberate, not sloppy.** They answer different questions and have different lifetimes. `tool_call` is replayed into a prompt on every subsequent turn of the conversation and must stay exactly what the model was told. `ToolInvocation` is never replayed into anything: it records the principal, the outcome class, the timings, and the error text, and it outlives the conversation. Folding them would either put audit fields into a prompt or make the audit trail conversation-shaped, and the addendum's consequence 3 forbids the second outright.

**Files**
- Create: `agents/models.py`
- Create: `agents/migrations/__init__.py`, `agents/migrations/0001_initial.py` (generated, then read)
- Create: `agents/tests/test_models.py`
- Create: `agents/tests/_helpers.py` — the row builders, `stub_runner`, and the tool-registry snapshot pair

**Interfaces**
- Consumes: `models.contracts.roles.CHAT_CONVERSE_ROLE`; `agents.limits.MAX_STEPS_DEFAULT`; `agents.contracts.tools.all_tools` (lazy, inside `save()`); `django.db.models.functions.Lower`.
- Produces:
  - `agents.models.Agent` — `.slug`, `.name`, `.description`, `.system_prompt`, `.llm_role`, `.tool_keys`, `.max_steps`, `.resident`, `.enabled`, `.created_at`, `.updated_at`; `save(*args, _from_resident_sync: bool = False, **kwargs)`; `delete(*args, **kwargs)`.
  - `agents.models.Conversation` — UUID pk, `.agent`, `.title`, `.created_at`, `.updated_at`.
  - `agents.models.ToolInvocation` — `.Outcome`, `.principal_kind`, `.principal_key`, `.tool_key`, `.args`, `.outcome`, `.text`, `.error`, `.started_at`, `.finished_at`; `.duration_ms` (property, not a column).
  - `agents.models.Turn` — `.Role`, `.State`, `.conversation`, `.index`, `.role`, `.text`, `.tool_call`, `.data`, `.artifacts`, `.depth`, `.state`, `.error`, `.invocation`, `.queue_job_id`, `.created_at`; `Turn.next_index(conversation) -> int`.

**Steps**

- [ ] Failing test first. Create `agents/tests/test_models.py`:

  ```python
  """Field wiring, and the three rules that are not just field wiring.

  Rule 1 (ruling R1, spec section 7.1): `Agent.save()` REJECTS a tool key
  whose registered spec is `mutates=True`, and ACCEPTS-and-logs a key that
  is not registered at all. The asymmetry is the whole point. A mutating
  tool is registered but not grantable before Identity & Auth
  (ADR 0010:266-276) and a row that granted one must never save. An
  UNREGISTERED key is a not-yet or a not-here -- `general` grants tools
  that exist only when the vision feature flag is on -- and rejecting it
  would make `sync_agents` fail on a legal install.

  Rule 2 (spec section 7.5): a resident row is not editable except for
  `enabled`, and not deletable at all. The one sanctioned bypass is the
  keyword-only `_from_resident_sync=True`, which only
  `agents.resident.sync_resident_agents` passes.

  Rule 3 (spec section 7.3): `Turn.tool_call` carries all five keys or is
  null. `"discarded"` is never OMITTED -- a missing key and an empty list
  must not both mean "nothing was discarded".
  """
  from __future__ import annotations

  import logging
  import uuid

  import pytest

  from agents.contracts.tools import ToolSpec, register_tool
  from agents.models import Agent, ToolInvocation, Turn
  from agents.tests._helpers import (
      make_agent, make_conversation, make_turn, restore_tools, snapshot_tools,
  )

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  class TestAgentToolKeyValidation:
      def test_a_registered_mutating_tool_key_is_rejected_by_name(self):
          register_tool(ToolSpec(
              key="stub.mutating", label="Stub", description="d",
              runner="agents.tests._helpers.stub_runner", mutates=True,
          ))
          with pytest.raises(ValueError) as exc:
              make_agent(tool_keys=["stub.mutating"])
          assert "stub.mutating" in str(exc.value)

      def test_an_unregistered_tool_key_is_accepted_and_written_verbatim(self, caplog):
          # `caplog` captures at WARNING by default, so an assertion on an
          # INFO line passes vacuously without this -- it would pass just
          # as happily if the log call were deleted. The logger name is
          # the module's own, so a stray INFO from anywhere else cannot
          # satisfy the assertion either.
          with caplog.at_level(logging.INFO, logger="agents.models"):
              agent = make_agent(tool_keys=["not.registered.anywhere"])
          agent.refresh_from_db()
          assert agent.tool_keys == ["not.registered.anywhere"]
          assert "not.registered.anywhere" in caplog.text

      def test_a_registered_non_mutating_key_is_accepted_silently(self, caplog):
          register_tool(ToolSpec(
              key="stub.safe", label="Stub", description="d",
              runner="agents.tests._helpers.stub_runner",
          ))
          with caplog.at_level(logging.INFO, logger="agents.models"):
              make_agent(tool_keys=["stub.safe"])
          assert "stub.safe" not in caplog.text

      def test_tool_keys_must_be_a_list_of_strings(self):
          with pytest.raises(ValueError):
              make_agent(tool_keys={"not": "a list"})


  class TestResidentRowsAreProtected:
      def test_a_resident_row_refuses_a_field_change(self):
          agent = make_agent(resident=True)
          agent.system_prompt = "something else"
          with pytest.raises(ValueError) as exc:
              agent.save()
          assert "system_prompt" in str(exc.value)

      def test_a_resident_row_still_accepts_an_enabled_flip(self):
          agent = make_agent(resident=True)
          agent.enabled = False
          agent.save()
          agent.refresh_from_db()
          assert agent.enabled is False

      def test_a_resident_row_refuses_deletion(self):
          agent = make_agent(resident=True)
          with pytest.raises(ValueError):
              agent.delete()
          assert Agent.objects.filter(pk=agent.pk).exists()

      def test_the_sync_bypass_is_keyword_only_and_works(self):
          agent = make_agent(resident=True)
          agent.system_prompt = "re-declared in code"
          agent.save(_from_resident_sync=True)
          agent.refresh_from_db()
          assert agent.system_prompt == "re-declared in code"

      def test_a_non_resident_row_is_freely_editable_and_deletable(self):
          agent = make_agent(resident=False, slug="scratch")
          agent.system_prompt = "changed"
          agent.save()
          agent.delete()
          assert not Agent.objects.filter(slug="scratch").exists()


  class TestConversationAndTurn:
      def test_conversation_gets_a_uuid_primary_key(self):
          assert isinstance(make_conversation().id, uuid.UUID)

      def test_an_agent_with_a_conversation_cannot_be_deleted(self):
          from django.db.models import ProtectedError

          agent = make_agent(slug="protected", resident=False)
          make_conversation(agent=agent)
          with pytest.raises(ProtectedError):
              agent.delete()

      def test_next_index_starts_at_zero_and_then_increments(self):
          conv = make_conversation()
          assert Turn.next_index(conv) == 0
          make_turn(conversation=conv, index=0)
          assert Turn.next_index(conv) == 1

      def test_two_turns_cannot_share_an_index_in_one_conversation(self):
          from django.db import IntegrityError

          conv = make_conversation()
          make_turn(conversation=conv, index=0)
          with pytest.raises(IntegrityError):
              make_turn(conversation=conv, index=0)

      def test_turns_order_by_index(self):
          conv = make_conversation()
          make_turn(conversation=conv, index=1, text="second")
          make_turn(conversation=conv, index=0, text="first")
          assert [t.text for t in conv.turns.all()] == ["first", "second"]

      def test_a_tool_turn_carries_all_five_tool_call_keys(self):
          conv = make_conversation()
          turn = make_turn(
              conversation=conv, index=0, role=Turn.Role.TOOL,
              tool_call={"tool": "rag.search", "args": {"query": "q"},
                         "agent": "general", "id": "", "discarded": []},
          )
          turn.refresh_from_db()
          assert set(turn.tool_call) == {"tool", "args", "agent", "id", "discarded"}

      def test_queue_job_id_is_a_plain_integer_not_a_foreign_key(self):
          """`agents/` may not import `models.queue` (import-law rule 2) --
          the same call `tools/vision/models.py:135-143` records for
          `GenerationJob.queue_job_id`."""
          field = Turn._meta.get_field("queue_job_id")
          assert field.get_internal_type() == "BigIntegerField"
          assert not field.is_relation


  class TestToolInvocation:
      def test_a_turn_references_an_invocation_and_survives_losing_it(self):
          conv = make_conversation()
          inv = ToolInvocation.objects.create(
              principal_kind="resident_agent", principal_key="general",
              tool_key="rag.search", args={"query": "q"},
              outcome=ToolInvocation.Outcome.OK, text="ok",
          )
          turn = make_turn(conversation=conv, index=0, role=Turn.Role.TOOL, invocation=inv)
          inv.delete()
          turn.refresh_from_db()
          assert turn.invocation_id is None
          assert Turn.objects.filter(pk=turn.pk).exists()

      def test_every_outcome_class_the_addendum_names_is_a_choice(self):
          assert {value for value, _ in ToolInvocation.Outcome.choices} == {
              "ok", "refused", "param_error", "error", "degraded",
          }

      def test_duration_ms_is_none_until_the_call_finishes(self):
          inv = ToolInvocation.objects.create(
              principal_kind="api_client", principal_key="k",
              tool_key="rag.search", args={}, outcome=ToolInvocation.Outcome.OK,
          )
          assert inv.duration_ms is None
  ```

- [ ] Run it and read the failure — `ModuleNotFoundError: No module named 'agents.models'`:

  ```bash
  .venv/bin/pytest -q agents/tests/test_models.py
  ```

- [ ] Create `agents/tests/_helpers.py` with **exactly what this task's tests call** and nothing more — a helper written before its caller is dead code with a docstring. Tasks 4, 6, and 12 add to it when they need to:

  ```python
  """Shared test helpers for the `agents` app's own tests.

  Plain importable module -- **not** a `conftest.py` (the repo forbids
  them anywhere). Each test module imports what it needs explicitly;
  autouse fixtures stay *defined* in each test module but delegate their
  bodies to the functions below.

  Duplicated per APP, never imported across apps. `snapshot_tools`/
  `restore_tools` below are a fifth copy of an eight-line pair that
  already exists in `tools/rag/tests/_helpers.py:116,128`, `tools/vision/
  tests/_helpers.py:96,108`, `models/registry/tests/_helpers.py:127,139`,
  and `agents/contracts/tests/_helpers.py:90,106`. That is deliberate and
  was ruled on in this phase's Global Constraints. `agents/runtime/tests/
  _helpers.py` is the same APP and imports from here rather than making a
  sixth copy.
  """
  from __future__ import annotations

  import pytest        # Tasks 6 and 12 add `@pytest.fixture`s to this module.

  CALLS: list = []


  def make_agent(**overrides):
      """An `Agent` row. `slug` defaults to a fixed test value; pass one
      when a test needs two agents."""
      from agents.models import Agent

      fields = dict(
          slug="test-agent",
          name="Test agent",
          description="",
          system_prompt="You are a test agent.",
          tool_keys=[],
          resident=False,
          enabled=True,
      )
      fields.update(overrides)
      return Agent.objects.create(**fields)


  def make_conversation(**overrides):
      from agents.models import Conversation

      fields = dict(agent=overrides.pop("agent", None) or make_agent())
      fields.update(overrides)
      return Conversation.objects.create(**fields)


  def make_turn(**overrides):
      from agents.models import Turn

      conversation = overrides.pop("conversation", None) or make_conversation()
      fields = dict(
          conversation=conversation,
          index=overrides.pop("index", Turn.next_index(conversation)),
          role=Turn.Role.USER,
          text="",
      )
      fields.update(overrides)
      return Turn.objects.create(**fields)


  def stub_runner(args: dict, ctx):
      """A module-level runner a `ToolSpec` can name by dotted path.
      Records its calls in `CALLS` so a test can assert what reached it."""
      from agents.contracts.tools import ToolResult

      CALLS.append((args, ctx))
      return ToolResult(text="stub ran", data={"args": args})


  def snapshot_tools() -> dict:
      """Body of the `_snapshot_tools` autouse fixture every test module
      that registers a tool uses: copy `_TOOLS`, hand it back, let the
      fixture restore it afterwards. A module-global registry surviving
      between tests is exactly the state that makes a suite pass in one
      collection order and fail in the other."""
      from agents.contracts import tools as tools_module

      return dict(tools_module._TOOLS)


  def restore_tools(saved: dict) -> None:
      from agents.contracts import tools as tools_module

      tools_module._TOOLS.clear()
      tools_module._TOOLS.update(saved)
      CALLS.clear()
  ```

- [ ] Create `agents/models.py`:

  ```python
  """The agent column's data model (spec section 7, deviations D1/D2).

  Four tables, two lifetimes:

  - `Agent` / `Conversation` / `Turn` are the CONVERSATION. A `Turn` is
    replayed into a prompt; `Turn.tool_call` is therefore exactly what the
    model was told, and nothing else may be smuggled into it.
  - `ToolInvocation` is the AUDIT TRAIL. It is never replayed into
    anything. It records WHO called (a principal, not an agent -- an
    external MCP `tools/call` has no conversation at all), what ran, how it
    ended, and how long it took. The 2026-08-27 addendum's consequence 3
    is why it is its own row rather than columns on `Turn`.

  No import of `models.queue` anywhere in this file: `Turn.queue_job_id`
  is a plain `BigIntegerField`, exactly as `tools/vision/models.py:135-143`
  records for `GenerationJob.queue_job_id`, because import-law rule 2
  forbids an `agents/*` app reaching into the queue's storage layer.
  """
  from __future__ import annotations

  import logging
  import uuid

  from django.db import models
  from django.db.models.functions import Lower

  from agents.limits import MAX_STEPS_DEFAULT
  from models.contracts.roles import CHAT_CONVERSE_ROLE

  logger = logging.getLogger(__name__)

  # Which fields of a `resident=True` row an operator may change. Exactly
  # one: turning a resident agent off. Everything else about a resident is
  # a code declaration (`agents/resident.py`), and a row that drifted from
  # it would make the code lie about what is running.
  _RESIDENT_MUTABLE_FIELDS = frozenset({"enabled", "updated_at"})


  class Agent(models.Model):
      """One agent: a system prompt, an LLM role, and granted tool keys.

      `tool_keys` validation is ruling R1, and the asymmetry IS the
      ruling: a key whose REGISTERED spec is `mutates=True` is REJECTED by
      name (ADR 0010:266-276 -- a settings-mutating tool is registered but
      not grantable before Identity & Auth, so a row granting one must
      never save); a key that is not registered AT ALL is ACCEPTED and
      logged. The second case is the one a strict check gets wrong: the
      `general` resident grants the vision tools, which are not registered
      when the `vision` feature flag is off, and rejecting them would make
      `sync_agents` fail on a perfectly legal install. An unregistered key
      is a not-yet or a not-here, never a privilege escalation -- there is
      nothing to escalate TO, because `granted_tools` drops it again
      before a prompt is ever built.

      Three conditions gate a tool call, checked in three different
      places, and all three must hold: the row names it (here), it is
      registered and non-mutating (`granted_tools`,
      `agents/contracts/tools.py`), and its declared roles resolve
      (`plan_turn`, `agents/runtime/jobs.py`).
      """

      slug = models.CharField(max_length=64)
      name = models.CharField(max_length=255)
      description = models.TextField(blank=True, default="")
      system_prompt = models.TextField(blank=True, default="")
      # Which RoleSpec backs this agent's LLM. A per-agent override is
      # ROADMAP:242-245's "per-role model split as a first-class outcome",
      # realized: a lean model can back one agent and a conversational one
      # another, with no framework change.
      llm_role = models.CharField(max_length=255, default=CHAT_CONVERSE_ROLE)
      tool_keys = models.JSONField(default=list, blank=True)
      max_steps = models.PositiveIntegerField(default=MAX_STEPS_DEFAULT)
      # True for a code-declared agent synced from `agents/resident.py`.
      resident = models.BooleanField(default=False)
      enabled = models.BooleanField(default=True)
      created_at = models.DateTimeField(auto_now_add=True)
      updated_at = models.DateTimeField(auto_now=True)

      class Meta:
          ordering = ["slug"]
          constraints = [models.UniqueConstraint(Lower("slug"), name="uniq_agent_slug_ci")]

      def __str__(self) -> str:  # pragma: no cover - trivial
          return self.slug

      def save(self, *args, _from_resident_sync: bool = False, **kwargs) -> None:
          """Validate `tool_keys` (ruling R1), then refuse to change a
          resident row's declaration.

          `_from_resident_sync` is keyword-only and is the ONE sanctioned
          bypass: `agents.resident.sync_resident_agents` passes it when it
          re-applies a code declaration to its row. Nothing else in the
          codebase passes it, and a test pins that. It is a keyword rather
          than a separate manager method because `Model.save` is what
          every other caller reaches, and a guard that can be walked
          around by using the ordinary path is not a guard.

          NOTE: this forces `sync_resident_agents` to use an explicit
          get-then-set-then-save rather than `update_or_create` (which
          spec section 7.5 suggests), because `update_or_create` calls
          `save()` itself and cannot pass the keyword through.
          """
          self._validate_tool_keys()
          if self.pk and self.resident and not _from_resident_sync:
              self._refuse_resident_edit()
          super().save(*args, **kwargs)

      def delete(self, *args, **kwargs):
          """A resident row is never deletable, with no bypass at all.

          `sync_resident_agents` DISABLES a retired resident rather than
          deleting it: `Conversation.agent` is `PROTECT`, so a delete
          would either fail or orphan history, and a disabled row keeps
          every past conversation readable.
          """
          if self.resident:
              raise ValueError(
                  f"Agent {self.slug!r} is a resident (code-declared) agent and cannot "
                  f"be deleted. Set `enabled=False` instead."
              )
          return super().delete(*args, **kwargs)

      def _validate_tool_keys(self) -> None:
          from agents.contracts.tools import all_tools

          if not isinstance(self.tool_keys, list) or not all(
              isinstance(key, str) for key in self.tool_keys
          ):
              raise ValueError(
                  f"Agent {self.slug!r}: tool_keys must be a list of strings, "
                  f"got {self.tool_keys!r}"
              )
          registered = {spec.key: spec for spec in all_tools()}
          mutating = sorted(
              key for key in self.tool_keys if key in registered and registered[key].mutates
          )
          if mutating:
              raise ValueError(
                  f"Agent {self.slug!r} may not be granted {', '.join(mutating)}: a tool "
                  f"that changes state which already exists is registered but not "
                  f"grantable until Identity & Auth lands (ADR 0010)."
              )
          for key in self.tool_keys:
              if key not in registered:
                  logger.info(
                      "agents: %r grants %r, which is not registered on this install "
                      "(a feature-gated or not-yet-shipped tool). Written verbatim; "
                      "dropped again when the prompt is built.",
                      self.slug, key,
                  )

      def _refuse_resident_edit(self) -> None:
          stored = type(self).objects.filter(pk=self.pk).first()
          if stored is None:
              return
          changed = sorted(
              field.name
              for field in self._meta.concrete_fields
              if field.name not in _RESIDENT_MUTABLE_FIELDS
              and getattr(stored, field.attname) != getattr(self, field.attname)
          )
          if changed:
              raise ValueError(
                  f"Agent {self.slug!r} is a resident (code-declared) agent; "
                  f"{', '.join(changed)} cannot be edited. Change agents/resident.py "
                  f"and run `manage.py sync_agents`."
              )


  class Conversation(models.Model):
      """A thread of turns with one agent.

      UUID pk, matching `GenerationJob` (`tools/vision/models.py:101`) and
      the `ChatSession` this replaces. `PROTECT` on `agent` so an agent
      can never be deleted out from under a conversation. `title` is set
      from the first ~60 characters of the first user turn and is NEVER
      generated by a model -- that would be a second, invisible model call
      per conversation.
      """

      id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
      agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="conversations")
      title = models.CharField(max_length=255, blank=True, default="")
      created_at = models.DateTimeField(auto_now_add=True)
      updated_at = models.DateTimeField(auto_now=True, db_index=True)

      class Meta:
          ordering = ["-updated_at"]

      def __str__(self) -> str:  # pragma: no cover - trivial
          return self.title or str(self.id)


  class ToolInvocation(models.Model):
      """One tool call, as an audit record (2026-08-27 addendum,
      consequence 3).

      Its own table, referenced BY `Turn`, never a set of columns ON it:
      an external MCP `tools/call` has no conversation and no turn, and
      must land in this same table unchanged. `principal_kind` /
      `principal_key` are `agents.contracts.tools.Principal`'s two fields,
      stored flat -- a principal is two strings, and joining a table to
      read them would buy nothing.

      `outcome` is a CLASS, not a message: five values, closed, and the
      one thing every later report (a per-principal rate limit, a failure
      dashboard, an MCP error mapping) can be built on without parsing
      prose.
      """

      class Outcome(models.TextChoices):
          OK = "ok", "OK"
          REFUSED = "refused", "Refused"
          PARAM_ERROR = "param_error", "Bad arguments"
          ERROR = "error", "Error"
          DEGRADED = "degraded", "Degraded"

      principal_kind = models.CharField(max_length=32)
      principal_key = models.CharField(max_length=255)
      tool_key = models.CharField(max_length=255, db_index=True)
      args = models.JSONField(default=dict, blank=True)
      outcome = models.CharField(max_length=16, choices=Outcome.choices)
      # What the runner handed back as the tool message -- the only part a
      # model ever saw. Blank on a refusal or an error.
      text = models.TextField(blank=True, default="")
      # `str(exc)`, never a traceback.
      error = models.TextField(blank=True, default="")
      started_at = models.DateTimeField(auto_now_add=True)
      finished_at = models.DateTimeField(null=True, blank=True)

      class Meta:
          ordering = ["-started_at"]
          indexes = [
              models.Index(fields=["principal_kind", "principal_key"],
                           name="agents_inv_principal"),
          ]

      def __str__(self) -> str:  # pragma: no cover - trivial
          return f"{self.tool_key} -> {self.outcome}"

      @property
      def duration_ms(self) -> int | None:
          """Milliseconds from start to finish, or `None` while the call is
          still running. A property, not a column: it is derivable, and a
          stored copy is one more thing that can disagree with its own
          inputs."""
          if self.finished_at is None or self.started_at is None:
              return None
          return int((self.finished_at - self.started_at).total_seconds() * 1000)


  class Turn(models.Model):
      """One entry in a conversation.

      Only an ASSISTANT turn ever holds a non-`done` `state`; USER and
      TOOL turns are written after the fact and are always `done`.
      """

      class Role(models.TextChoices):
          USER = "user", "User"
          ASSISTANT = "assistant", "Assistant"
          TOOL = "tool", "Tool"
          SYSTEM = "system", "System"

      class State(models.TextChoices):
          QUEUED = "queued", "Queued"
          RUNNING = "running", "Running"
          DONE = "done", "Done"
          FAILED = "failed", "Failed"
          CANCELLED = "cancelled", "Cancelled"

      conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                       related_name="turns")
      index = models.PositiveIntegerField()
      role = models.CharField(max_length=16, choices=Role.choices)
      text = models.TextField(blank=True, default="")
      # On a TOOL turn; null otherwise. FIVE keys, all JSON-safe, all
      # always present (spec section 7.3):
      #   "tool"      -- the ToolSpec KEY that ran ("rag.search"), dotted,
      #                  never the wire name. The wire name is derivable;
      #                  the key is what every other layer indexes by.
      #   "args"      -- the VALIDATED args dict (`validate_tool_args`
      #                  output) on an ok/refused/error/degraded outcome;
      #                  the RAW dict the model emitted on a param_error,
      #                  because validation is precisely what did not
      #                  happen there and there is no validated form to
      #                  record. Replayed into a later prompt either way,
      #                  which is why it must be what actually ran.
      #   "agent"     -- the Agent.slug whose loop issued the call. Differs
      #                  from the conversation's agent on a delegated turn.
      #   "id"        -- the model-supplied tool_call_id, or "" when the
      #                  engine supplied none. RESERVED, not load-bearing
      #                  today: the installed Ollama integration supplies
      #                  none at all (see `agents/runtime/prompt.py`).
      #   "discarded" -- [{"tool": ..., "args": ...}, ...] for every call in
      #                  the same response that was NOT run. An empty list
      #                  in the ordinary single-call case, NEVER omitted: a
      #                  missing key and an empty list must not both mean
      #                  "nothing was discarded".
      tool_call = models.JSONField(null=True, blank=True)
      # `ToolResult.data` on a TOOL turn (RAG citations live in
      # data["citations"]); null otherwise.
      data = models.JSONField(null=True, blank=True)
      artifacts = models.JSONField(default=list, blank=True)   # ["output:12", "document:7"]
      depth = models.PositiveIntegerField(default=0)
      state = models.CharField(max_length=16, choices=State.choices, default=State.DONE)
      error = models.TextField(blank=True, default="")
      # The audit row for this turn's tool call (2026-08-27 addendum,
      # consequence 3). SET_NULL, not CASCADE: losing an audit row must
      # never delete the conversation turn that referenced it.
      invocation = models.ForeignKey(ToolInvocation, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="turns")
      # The `agent.turn` job that produced (or is producing) this turn. NOT
      # a ForeignKey: `agents/` may not import `models.queue` (import-law
      # rule 2), exactly the reasoning `tools/vision/models.py:135-143`
      # records for `GenerationJob.queue_job_id`.
      queue_job_id = models.BigIntegerField(null=True, blank=True, db_index=True)
      created_at = models.DateTimeField(auto_now_add=True)

      class Meta:
          ordering = ["index"]
          constraints = [
              models.UniqueConstraint(fields=["conversation", "index"], name="uniq_turn_index"),
          ]
          indexes = [
              models.Index(fields=["conversation", "index"], name="agents_turn_thread"),
          ]

      def __str__(self) -> str:  # pragma: no cover - trivial
          return f"{self.conversation_id}#{self.index} {self.role}"

      @classmethod
      def next_index(cls, conversation) -> int:
          """The next free index in `conversation`.

          Read-then-write, and that is safe here rather than lucky: a
          conversation's turns are written by exactly one `agent.turn` job
          at a time (a turn is enqueued only after the previous one
          finished), and the `uniq_turn_index` constraint above turns a
          real race into an honest `IntegrityError` rather than a silently
          reordered conversation.
          """
          last = cls.objects.filter(conversation=conversation).order_by("-index").first()
          return 0 if last is None else last.index + 1
  ```

- [ ] Create `agents/migrations/__init__.py` (empty), generate the migration, then **read it before trusting it**:

  ```bash
  .venv/bin/python manage.py makemigrations agents
  cat agents/migrations/0001_initial.py
  ```

  Confirm: one file; four `CreateModel`s; the two `UniqueConstraint`s (`uniq_agent_slug_ci` built on `Lower("slug")`, and `uniq_turn_index`); the two `Index`es; and `Turn.invocation` added **after** `ToolInvocation` exists. If `makemigrations` emitted more than one file, STOP and report.

- [ ] Run green:

  ```bash
  .venv/bin/pytest -q agents/tests/test_models.py
  .venv/bin/python manage.py makemigrations --check --dry-run   # must be clean now
  ```

- [ ] Prove the migration applies **and reverses** on a real database. A migration nobody has ever reversed is a migration nobody can back out of:

  ```bash
  .venv/bin/python manage.py migrate agents
  .venv/bin/python manage.py migrate agents zero
  .venv/bin/python manage.py migrate agents
  ```

- [ ] Full matrix, then commit:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  ```

  ```bash
  git add agents/models.py agents/migrations agents/tests
  git commit -m "$(cat <<'EOF'
  feat(agents): Agent, Conversation, Turn, ToolInvocation -- one migration

  Two lifetimes in four tables. Agent/Conversation/Turn are the
  CONVERSATION: a Turn is replayed into a prompt, so Turn.tool_call is
  exactly what the model was told and carries all five keys always --
  "discarded" is never omitted, because a missing key and an empty list
  must not both mean "nothing was discarded".

  ToolInvocation is the AUDIT TRAIL and is its own table, referenced by
  Turn through a nullable SET_NULL FK. The 2026-08-27 addendum requires
  it: an external MCP tools/call has no conversation and no turn, and has
  to land in the same table unchanged. Five closed outcome classes, so a
  later report never has to parse prose.

  Agent.save() implements ruling R1's asymmetry: a REGISTERED mutating
  key is rejected by name (ADR 0010 -- not grantable before Identity &
  Auth); an UNREGISTERED key is accepted and logged, because a
  feature-gated tool is a not-here, not a privilege escalation, and
  rejecting it would make sync_agents fail on a legal install.

  A resident row refuses every field change but `enabled`, and refuses
  deletion outright with no bypass -- a retired resident is disabled, not
  deleted, because Conversation.agent is PROTECT. The single sanctioned
  edit path is the keyword-only _from_resident_sync=True, which forces
  sync to use get-then-save rather than update_or_create.

  Turn.queue_job_id is a plain BigIntegerField: agents/ may not import
  models.queue (import-law rule 2), the same call tools/vision made for
  GenerationJob.queue_job_id.

  Migration applies and reverses cleanly on a real database.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 4: `Principal`, `ToolContext.principal`, `ToolContext.tool_key`, and `granted_tools`

The 2026-08-27 addendum's consequence 1, built now rather than retrofitted: **a grant attaches to a principal, and one function decides availability.** P1 shipped `ToolContext.agent_key` (`agents/contracts/tools.py:197`) — a bare string that could only ever mean "an agent". A principal is a *kind* plus a *key*, so the same `ToolContext` an internal turn builds is the one an external MCP `tools/call` will build later, with no field to add and no runner to change.

This task also adds `ToolContext.tool_key`, which deviation D4 forces: `agent.<slug>` registers N specs that share one runner, a runner's signature is `(args, ctx)`, and there is no other way for it to learn which spec invoked it. It is generally useful beyond that — a runner logging its own identity has nowhere else to get it.

**Everything here lives in the rule-1 pure leaf.** `Principal` is two strings and a validating `__post_init__`. No Django, no `TextChoices`, no import of `agents.models`. `agents/contracts/tests/test_purity.py` proves it in a subprocess.

**`granted_tools` implements ruling R1's *runtime* half, and only that half.** Three conditions gate a tool call and they are checked in three places: the row names it (`Agent.save`, Task 3), it is registered and non-mutating (**here**), and its declared roles resolve (`plan_turn`, Task 9). The third is deliberately **not** here — resolving a role needs Django and a database, and this module has neither.

**Files**
- Modify: `agents/contracts/tools.py` — `PRINCIPAL_KINDS`, `Principal`, `ToolContext`, `granted_tools`
- Modify: `agents/contracts/__init__.py` — the module summary
- Modify: `agents/contracts/tests/_helpers.py`, `tools/rag/tests/_helpers.py`, `tools/vision/tests/_helpers.py`, `models/registry/tests/_helpers.py` — each app's own `make_tool_ctx`
- Modify: `agents/tests/_helpers.py` — add this app's own `make_principal`/`make_tool_ctx`
- Modify: `agents/contracts/tests/test_tools.py` — the new behaviour
- Modify: `agents/contracts/README.md` — a "Principals and grants" section

**Interfaces**
- Consumes: nothing new. Standard library plus what `tools.py` already imports.
- Produces:
  - `agents.contracts.tools.PRINCIPAL_KINDS: tuple[str, ...] = ("resident_agent", "user_agent", "api_client")`
  - `agents.contracts.tools.Principal` — frozen dataclass, `.kind: str`, `.key: str`; raises `ValueError` on an unknown kind or a blank key.
  - `agents.contracts.tools.ToolContext` — `.conversation_id: str`, `.principal: Principal`, `.depth: int`, `.budget: StepBudget`, `.job: JobContext`, `.tool_key: str = ""`. **`agent_key` is gone.**
  - `agents.contracts.tools.granted_tools(principal: Principal, tool_keys: Sequence[str]) -> list[str]`

**Steps**

- [ ] Failing test first. Add to `agents/contracts/tests/test_tools.py`:

  ```python
  class TestPrincipal:
      def test_a_principal_is_a_kind_and_a_key(self):
          p = Principal(kind="resident_agent", key="general")
          assert (p.kind, p.key) == ("resident_agent", "general")

      def test_an_unknown_kind_is_rejected_at_construction(self):
          with pytest.raises(ValueError) as exc:
              Principal(kind="wizard", key="k")
          assert "wizard" in str(exc.value)

      def test_a_blank_key_is_rejected(self):
          with pytest.raises(ValueError):
              Principal(kind="api_client", key="")

      def test_the_three_kinds_are_the_ones_the_addendum_names(self):
          assert PRINCIPAL_KINDS == ("resident_agent", "user_agent", "api_client")

      def test_a_principal_is_frozen_and_hashable(self):
          """Frozen so it can be a dict key in a later grant cache, and so
          nothing downstream can quietly re-point a context at a different
          caller mid-turn."""
          p = Principal(kind="user_agent", key="k")
          with pytest.raises(Exception):
              p.key = "other"
          assert {p: 1}[Principal(kind="user_agent", key="k")] == 1


  class TestGrantedTools:
      def test_an_unregistered_key_is_dropped_and_logged(self, caplog):
          # `caplog` captures at WARNING by default; without the level and
          # the logger name this assertion passes even if the log call is
          # deleted.
          with caplog.at_level(logging.INFO, logger="agents.contracts.tools"):
              assert granted_tools(make_principal(), ["nope.missing"]) == []
          assert "nope.missing" in caplog.text

      def test_a_mutating_key_is_dropped(self):
          register_tool(make_spec(key="stub.mutating", mutates=True))
          assert granted_tools(make_principal(), ["stub.mutating"]) == []

      def test_a_registered_non_mutating_key_survives(self):
          register_tool(make_spec(key="stub.safe"))
          assert granted_tools(make_principal(), ["stub.safe"]) == ["stub.safe"]

      def test_order_is_the_caller_s_order_and_duplicates_collapse(self):
          """The agent row's order is the order the model sees its tools
          in. Preserved, because a stable prompt is a debuggable one."""
          register_tool(make_spec(key="stub.a"))
          register_tool(make_spec(key="stub.b"))
          assert granted_tools(make_principal(), ["stub.b", "stub.a", "stub.b"]) == [
              "stub.b", "stub.a",
          ]

      def test_it_never_calls_get_tool(self, monkeypatch):
          """An absent key is NORMAL here, and `get_tool` RAISES on one
          (`agents/contracts/tools.py:238`). This function must read the
          registry directly -- `agents/contracts/README.md`'s "get_tool
          raises; the prompt builder does not" section is the rule."""
          import agents.contracts.tools as module

          monkeypatch.setattr(module, "get_tool", _explode)
          assert granted_tools(make_principal(), ["nope.missing"]) == []


  class TestToolContext:
      def test_it_carries_a_principal_not_an_agent_key(self):
          ctx = make_tool_ctx()
          assert isinstance(ctx.principal, Principal)
          assert not hasattr(ctx, "agent_key")

      def test_tool_key_defaults_blank_and_is_set_per_call(self):
          """N `agent.<slug>` specs share one runner (deviation D4); a
          runner's signature is `(args, ctx)`, so `ctx.tool_key` is the
          only way it can learn which spec invoked it. `invoke_tool` sets
          it; a hand-built context leaves it blank."""
          import dataclasses

          ctx = make_tool_ctx()
          assert ctx.tool_key == ""
          assert dataclasses.replace(ctx, tool_key="agent.library").tool_key == "agent.library"
  ```

  with `_explode` a module-level helper that raises if called, and `make_principal` added to `agents/contracts/tests/_helpers.py`.

- [ ] Run and read the failures — `ImportError` on `Principal`/`PRINCIPAL_KINDS`/`granted_tools`, plus `TypeError: ToolContext.__init__() got an unexpected keyword argument 'principal'`:

  ```bash
  .venv/bin/pytest -q agents/contracts/tests/test_tools.py
  ```

- [ ] Add to `agents/contracts/tools.py`, immediately after `ToolRefused` and before `ToolSpec`:

  ```python
  # The kinds of caller a grant can attach to (2026-08-27 addendum,
  # consequence 1), in the order they arrive:
  #   "resident_agent" -- a code-declared agent (`agents/resident.py`).
  #   "user_agent"     -- an Agent row somebody wrote.
  #   "api_client"     -- an external caller through the future MCP edge.
  # Users and groups become further KINDS later, never a second mechanism.
  # A closed tuple rather than an open string so a typo is a construction
  # error here and not a silently-ungranted principal three layers down.
  PRINCIPAL_KINDS = ("resident_agent", "user_agent", "api_client")


  @dataclass(frozen=True)
  class Principal:
      """WHO is calling a tool.

      Two strings, deliberately: the same shape serves an in-process agent
      turn and an external MCP `tools/call`, so `ToolContext` needs no
      second field and no runner needs a second code path when the edge
      lands. `key` means whatever `kind` says it means -- an `Agent.slug`
      for the two agent kinds, an API client's identifier for the third.

      Frozen: hashable (so a later grant cache can key on it) and
      un-repointable (so nothing downstream can quietly change who a
      half-finished turn is acting as).

      NO DJANGO. This is a rule-1 pure leaf; `agents.models` is a
      different layer and never appears here.
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
  ```

- [ ] Replace `ToolContext` in the same file:

  ```python
  @dataclass(frozen=True)
  class ToolContext:
      """The second argument every tool runner is called with.

      `principal` is WHO is calling (2026-08-27 addendum, consequence 1).
      It replaced P1's bare `agent_key` string because a grant attaches to
      a principal, not to an agent: an external MCP `tools/call` has no
      agent at all and must arrive through this same field.

      `tool_key` is WHICH registered spec is being run, set by
      `invoke_tool` immediately before it calls the runner and blank
      otherwise. Needed because several specs may share one runner --
      `agent.<slug>` registers one spec per code-declared resident and
      they all run `agents.runtime.delegate.run_agent_tool` -- and a
      runner's signature is `(args, ctx)`, so there is nowhere else for it
      to learn its own identity from.

      `job` is this turn's `models.contracts.jobkinds.JobContext` -- the
      runner's only progress-reporting path, and the same object the
      vision job handler already threads into `services.wait_for`'s
      `on_poll` (`tools/vision/jobs.py:308-336`). Annotated, never
      imported at runtime (see the `TYPE_CHECKING` block above):
      `jobkinds` imports Django, and this module must not.
      """

      conversation_id: str          # UUID as a string; "" for a call with no conversation
      principal: Principal
      depth: int                    # 0 at the top level; +1 per agent-as-tool hop
      budget: StepBudget
      job: JobContext
      tool_key: str = ""
  ```

- [ ] Add `granted_tools` beside `grantable_tools`:

  ```python
  def granted_tools(principal: Principal, tool_keys: Sequence[str]) -> list[str]:
      """Which of `tool_keys` this `principal` may actually call, in the
      caller's own order, de-duplicated.

      The ONE function that decides availability (2026-08-27 addendum,
      consequence 1). Ruling R1's runtime half:

      - a key absent from the registry is DROPPED and logged. Normal, not
        exceptional: a feature-gated tool with its flag off, or a tool
        that ships in a later phase. `Agent.save()` already accepted it
        into the row for exactly that reason.
      - a key whose registered spec is `mutates=True` is DROPPED, silently
        -- `Agent.save()` refuses to store one at all, so reaching this
        branch means the registry changed under a row that was legal when
        it was written, and the honest response is to not offer the tool.

      Reads the registry DIRECTLY rather than through `get_tool`, which
      RAISES on an absent key (`get_tool`'s own docstring names this
      caller). Order is preserved because the agent row's order is the
      order the model sees its tools in, and a stable prompt is a
      debuggable one.

      `tool_keys` is an argument TODAY because grants live on the `Agent`
      row. When grants move to their own table behind Identity & Auth,
      this argument disappears and the principal alone decides; the call
      site and the return type do not change. That is the whole reason the
      principal is the first parameter of a function that does not yet
      read it.
      """
      out: list[str] = []
      seen: set[str] = set()
      for key in tool_keys:
          if key in seen:
              continue
          seen.add(key)
          spec = _TOOLS.get(key)
          if spec is None:
              logger.info(
                  "agents: %s %r was granted %r, which is not registered on this "
                  "install; dropped from its tool list.",
                  principal.kind, principal.key, key,
              )
              continue
          if spec.mutates:
              continue
          out.append(key)
      return out
  ```

  This needs `import logging` / `logger = logging.getLogger(__name__)` and `from collections.abc import Sequence` at the top of `tools.py` — **standard library only**, so the purity test stays green. (`collections.abc`, not `typing`: `typing.Sequence` has been deprecated since 3.9 and the rest of this codebase annotates with the real ABCs.) The test module gains `import logging` for the `caplog.at_level` call above.

- [ ] Update the four existing `make_tool_ctx` copies. Each keeps living in its own app's `_helpers.py`; none imports another's. The body changes from `agent_key="test-agent"` to `principal=Principal(kind="resident_agent", key="test-agent")`:

  - `agents/contracts/tests/_helpers.py:49-60`
  - `tools/rag/tests/_helpers.py:93-114`
  - `tools/vision/tests/_helpers.py:73-93`
  - `models/registry/tests/_helpers.py:104-125`

  and add a `make_principal(**overrides)` beside `make_tool_ctx` in `agents/contracts/tests/_helpers.py` and in `agents/tests/_helpers.py` (this app's own fifth copy — the per-app rule, again deliberately):

  ```python
  def make_principal(**overrides):
      from agents.contracts.tools import Principal

      fields = dict(kind="resident_agent", key="test-agent")
      fields.update(overrides)
      return Principal(**fields)
  ```

- [ ] **Sweep for stragglers before running anything.** No production module read `agent_key` — P1's runners take `ctx` and never touch it — but prove that rather than trusting it:

  ```bash
  grep -rn "agent_key" --include='*.py' agents models tools foundation scripts
  ```

  Expect: no hits at all after the edits. A hit in a *runner* would mean a rewrite; a hit in a test means a missed helper.

- [ ] Run green, then the full matrix — **this task touches three other apps' test helpers, so the reversed order matters more than usual**:

  ```bash
  .venv/bin/pytest -q agents/contracts models/registry/tests/test_tools.py tools/rag/tests/test_tools.py tools/vision/tests/test_tools.py
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  ```

- [ ] Add a "Principals and grants" section to `agents/contracts/README.md`, and update its opening list of what `tools.py` exports. State three things: a grant attaches to a principal; `granted_tools` is the one function that decides; the `tool_keys` argument is temporary and the principal is permanent.

- [ ] Commit:

  ```bash
  git add agents/contracts agents/tests/_helpers.py models/registry/tests/_helpers.py \
          tools/rag/tests/_helpers.py tools/vision/tests/_helpers.py
  git commit -m "$(cat <<'EOF'
  feat(agents): a tool call has a PRINCIPAL, and one function grants it

  ToolContext.agent_key becomes ToolContext.principal, a frozen
  Principal(kind, key) in the pure leaf. P1's bare string could only ever
  mean "an agent"; the 2026-08-27 addendum needs the same context to
  carry a resident agent, a user-built agent, and -- once the MCP edge
  lands -- an external API client, with no field to add and no runner to
  change.

  granted_tools(principal, tool_keys) is now the ONE place availability is
  decided: an unregistered key is dropped and logged (ruling R1 -- a
  feature-gated tool is a not-here, not an escalation), a mutating key is
  dropped (ADR 0010). It reads the registry directly, because get_tool
  RAISES on an absent key and an absent key is normal here. The tool_keys
  argument is temporary: when grants move to their own table the principal
  alone decides, and this signature loses an argument rather than gaining
  a caller.

  ToolContext.tool_key is new and is forced by agent-as-tool: N
  `agent.<slug>` specs share one runner, a runner's signature is
  (args, ctx), and there is nowhere else for it to learn which spec
  invoked it.

  Four per-app make_tool_ctx copies updated in place. They stay four
  copies -- the per-app helper rule -- and each is eight lines.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 5: `mcp_tool_dict(spec)` and the drift pin

The addendum's P2 deliverable. One more adapter over the schema builder `openai_tool_dict` already is, written **now**, while there is one builder to share, rather than after an edge exists to drift from.

The MCP `tools/list` entry shape is `{"name", "description", "inputSchema"}`, and `inputSchema` is a JSON Schema object — byte-identical to what `openai_tool_dict` puts under `function.parameters` (`agents/contracts/toolschema.py:99`). So the honest implementation is to **factor the schema out** and have both adapters call it, and the honest test is to assert the two agree for every registered spec.

**Files**
- Modify: `agents/contracts/toolschema.py` — `_input_schema`, `mcp_tool_dict`
- Modify: `agents/contracts/tests/test_toolschema.py`
- Modify: `agents/contracts/README.md`, `agents/contracts/__init__.py`

**Interfaces**
- Consumes: `agents.contracts.tools.ToolSpec`; this module's own `wire_name` (`toolschema.py:34`).
- Produces:
  - `agents.contracts.toolschema._input_schema(spec) -> dict` — `{"type": "object", "properties": {...}, "required": [...]}`. Private; the two adapters are the public surface.
  - `agents.contracts.toolschema.mcp_tool_dict(spec) -> dict` — `{"name", "description", "inputSchema"}`.
  - `openai_tool_dict` keeps its exact current output. **Refactor only; a byte for byte change in that dict is a defect.**

**Steps**

- [ ] Failing test first. Add to `agents/contracts/tests/test_toolschema.py`:

  ```python
  class TestMcpToolDict:
      def test_it_emits_exactly_name_description_and_input_schema(self):
          spec = make_spec(key="rag.search", description="Search the library.")
          assert set(mcp_tool_dict(spec)) == {"name", "description", "inputSchema"}

      def test_the_name_is_the_wire_name(self):
          """MCP tool names travel the same function-name grammar an
          OpenAI-style call does: no dots. `wire_name` is the one place
          that mapping lives, and `key_from_wire_name` is its inverse."""
          assert mcp_tool_dict(make_spec(key="rag.search"))["name"] == "rag__search"

      def test_it_never_serializes_runner_or_describer(self):
          """Implementation, never contract -- `describe_tool`'s own rule
          (`agents/contracts/tools.py:278-283`), applied to the second
          adapter so the two cannot disagree about it."""
          rendered = repr(mcp_tool_dict(make_spec(
              key="a.b", runner="agents.tests._helpers.stub_runner", describer="x.y",
          )))
          assert "stub_runner" not in rendered and "x.y" not in rendered


  class TestTheTwoAdaptersCannotDrift:
      """The drift pin the addendum asks for. Parametrized over EVERY
      registered spec rather than one hand-written example, so a tool
      added in a later phase is covered the day it is registered."""

      @pytest.mark.parametrize("spec", all_tools(), ids=lambda s: s.key)
      def test_input_schema_equals_the_openai_parameters_block(self, spec):
          assert mcp_tool_dict(spec)["inputSchema"] == (
              openai_tool_dict(spec)["function"]["parameters"]
          )

      @pytest.mark.parametrize("spec", all_tools(), ids=lambda s: s.key)
      def test_name_and_description_agree(self, spec):
          mcp = mcp_tool_dict(spec)
          fn = openai_tool_dict(spec)["function"]
          assert (mcp["name"], mcp["description"]) == (fn["name"], fn["description"])

      def test_the_parametrization_is_not_vacuous(self):
          """A registry that happened to be empty at collection time would
          make both tests above pass by looking at nothing."""
          assert len(all_tools()) >= 4   # the four non-vision v1 tools, flag-independent

      def test_the_pin_would_catch_a_real_divergence(self):
          """Anti-vacuous: build a spec, render both, and prove the
          comparison is actually comparing the schema and not two copies
          of the same object."""
          spec = make_spec(key="drift.check", params=(
              Param("query", "text", "Query", required=True, description="d"),
          ))
          schema = mcp_tool_dict(spec)["inputSchema"]
          assert schema["required"] == ["query"]
          assert schema["properties"]["query"]["type"] == "string"
  ```

  **Note on the parametrize call:** `all_tools()` is evaluated at *collection* time, when app registration has already run, so it sees whatever the current `FARABUNKER_FEATURES` registered. That is the point — under `'vision,media'` the vision tools are covered too, and the `>= 4` floor is what keeps the flag-off case honest rather than vacuous.

- [ ] Run and read the failure — `ImportError: cannot import name 'mcp_tool_dict'`:

  ```bash
  .venv/bin/pytest -q agents/contracts/tests/test_toolschema.py
  ```

- [ ] Refactor `openai_tool_dict`'s body out into `_input_schema` and add `mcp_tool_dict`:

  ```python
  def _input_schema(spec: ToolSpec) -> dict:
      """`spec.params` as one JSON Schema object.

      The single schema builder BOTH adapters call. Factored out when the
      second adapter arrived, not written speculatively: what a tool
      accepts must not depend on who is asking, or an external caller and
      an internal prompt disagree about the same tool and the
      disagreement surfaces as a validation error nobody can reproduce.

      Built from the STATIC `spec.params` alone. It does not consult
      `spec.describer` and does not filter params by any live `supported`
      fact (ruling R3).
      """
      props: dict[str, dict] = {}
      required: list[str] = []
      for p in spec.params:
          prop: dict = {"type": _JSON_TYPES[p.kind], "description": p.description or p.label}
          if p.kind == "choice" and p.choices:
              prop["enum"] = list(p.choices)
          if p.kind in ("int", "float"):
              if p.min is not None:
                  prop["minimum"] = p.min
              if p.max is not None:
                  prop["maximum"] = p.max
          if p.multiple:
              # `validate_params` answers a `multiple` param with a LIST
              # (operations.py:335-341). The schema must say so, or the
              # model sends a bare string and the tool silently runs with
              # one adapter where several were meant.
              prop = {"type": "array", "items": prop, "description": prop["description"]}
          props[p.key] = prop
          if p.required:
              required.append(p.key)
      return {"type": "object", "properties": props, "required": required}


  def openai_tool_dict(spec: ToolSpec) -> dict:
      """`spec` as the tool-calling JSON an LLM reads.

      Output is unchanged from P1; only the schema half moved into
      `_input_schema`, which `mcp_tool_dict` now shares.
      """
      return {
          "type": "function",
          "function": {
              "name": wire_name(spec.key),
              "description": spec.description,
              "parameters": _input_schema(spec),
          },
      }


  def mcp_tool_dict(spec: ToolSpec) -> dict:
      """`spec` as an MCP `tools/list` entry.

      The SAME schema object `openai_tool_dict` puts under
      `function.parameters`, under MCP's own key name. Two adapters, one
      builder -- pinned by `test_toolschema.py`'s drift test over every
      registered spec.

      Written in P2 although the MCP EDGE is a later phase (2026-08-27
      addendum). The point of writing it now is that there is currently
      one schema builder to share; writing it after an edge exists would
      mean reconciling two that had already drifted. `agents/contracts`
      stays transport-agnostic: this is an adapter, and the HTTP views,
      session handling, and auth check that would use it live outside
      this package entirely.

      `runner` and `describer` are not serialized, exactly as
      `describe_tool` refuses to serialize them: one is a dotted path into
      this codebase and the other is reserved, and neither belongs in a
      contract handed to an external caller.
      """
      return {
          "name": wire_name(spec.key),
          "description": spec.description,
          "inputSchema": _input_schema(spec),
      }
  ```

- [ ] While in this file, correct its module docstring's stale version line (`agents/contracts/toolschema.py:4-5` reads "llama-index-core 0.14.23"). Re-measure and write what is installed:

  ```bash
  .venv/bin/python -c "import importlib.metadata as m; print(m.version('llama-index-core'), m.version('llama-index-llms-ollama'), m.version('ollama'))"
  ```

  A docstring that names a version nobody has checked since P1 is worse than one that names none: it reads as a verified fact and is not one. Write the measured triple, and leave the sentence that follows it — the behaviour it describes is unchanged.

- [ ] Run green and confirm `openai_tool_dict` really is byte-identical — the refactor is only safe if the existing P1 tests still pass untouched:

  ```bash
  .venv/bin/pytest -q agents/contracts/tests/test_toolschema.py
  ```

- [ ] Full matrix, update `agents/contracts/README.md` (`toolschema.py`'s bullet gains `mcp_tool_dict` and the one-builder rule) and `agents/contracts/__init__.py`'s summary, then commit:

  ```bash
  git add agents/contracts
  git commit -m "$(cat <<'EOF'
  feat(agents): mcp_tool_dict beside openai_tool_dict, behind a drift pin

  The 2026-08-27 addendum's P2 deliverable. One schema builder
  (_input_schema), two adapters over it. Written now, while there is one
  builder to share -- writing it after an MCP edge exists would mean
  reconciling two that had already drifted.

  Pinned by a test parametrized over EVERY registered spec, not one
  example: mcp_tool_dict(spec)["inputSchema"] equals
  openai_tool_dict(spec)["function"]["parameters"], and the two names and
  descriptions agree. A tool registered in a later phase is covered the
  day it is registered. An anti-vacuous floor keeps an empty registry from
  making the whole thing pass by looking at nothing.

  openai_tool_dict's output is unchanged, byte for byte; the P1 tests that
  pin it were not touched.

  agents/contracts stays transport-agnostic: this is an adapter. The HTTP
  views, session handling, and auth check an MCP edge needs live outside
  this package and outside this phase.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 6: `agents/runtime/prompt.py` — history becomes messages, and the `FakeToolLLM` double

The first module of the runtime package, and the one worth getting exactly right, because **history replay and live loop append must be byte-identical**. A past tool turn that replays differently from how it was appended is a conversation the model sees two versions of.

#### Facts verified against the installed integration — re-verify before writing code

Recorded here so the implementer does not have to re-derive them, and so a version bump that changes any of them fails a test instead of a conversation. Versions at authoring time: `llama-index-core 0.14.24`, `llama-index-llms-ollama 0.10.1`, `ollama 0.6.2`. (P1's plan recorded core as `0.14.23`; the installed tree is `0.14.24` and every line cited below is unchanged between them — **re-run the version check rather than trusting either number**.)

```bash
.venv/bin/python -c "import importlib.metadata as m; print(m.version('llama-index-core'), m.version('llama-index-llms-ollama'), m.version('ollama'))"
```

| Fact | Where | Consequence for this task |
|---|---|---|
| `ToolCallBlock` is **not** exported from `llama_index.core.llms`; it lives in `llama_index.core.base.llms.types` (`types.py:1125-1135`) | verified by import | `from llama_index.core.base.llms.types import ToolCallBlock` — a plain `from llama_index.core.llms import ...` raises `ImportError`. `tools/rag/extract.py:20` imports `ChatMessage`/`MessageRole`/`TextBlock` from `llama_index.core.llms`, which does work; only the tool block is elsewhere. |
| `ToolCallBlock` fields are `tool_call_id` (`Optional[str] = None`), `tool_name`, `tool_kwargs` (`types.py:1127-1134`) | source | Three fields, no more. |
| `MessageRole.TOOL.value == "tool"` (`types.py:61`) and `_convert_to_ollama_messages` emits `message.role.value` verbatim as the wire role (`base.py:250`) | source | A `ChatMessage(role=MessageRole.TOOL, content=...)` reaches Ollama as a `"tool"` message with no translation. |
| `_convert_to_ollama_messages` turns each `ToolCallBlock` into `{"function": {"name": block.tool_name, "arguments": block.tool_kwargs}}` and **never reads `tool_call_id`** (`base.py:265-286`) | source | Nothing this design sends to Ollama carries the id. |
| `Ollama.chat` builds its `ToolCallBlock`s **without** `tool_call_id` at all (`base.py:427-434`) | source | **The engine supplies no id.** `Turn.tool_call["id"]` is therefore always `""` on this path today. |
| `get_tool_calls_from_response` returns `ToolSelection(tool_id=tool_call.tool_name, tool_name=..., tool_kwargs=...)` (`base.py:385-395`), with the source comment "tool ids not provided by Ollama" | source | **`ToolSelection.tool_id` is the tool NAME, not an id.** Recording it as `tool_call["id"]` would store a name in an id field and make a later OpenAI-compatible engine's correlation logic silently wrong. The loop must read `.tool_name` / `.tool_kwargs` and record `"id": ""`. |
| `Ollama.chat` pops `tools` from kwargs and forwards it to the SDK (`base.py:403,409`); `force_single_tool_call` (`base.py:63`) is reached only through `_validate_chat_with_tools_response` (`base.py:361-362`), i.e. through `chat_with_tools`, which this design does not use | source | Plain `chat()` **does not cap** the number of tool calls. Taking `calls[0]` is this platform's policy, not the library's behaviour, and Task 8 must pin it. |

**This is a spec correction.** Spec §6.2 step 2 and §7.3 both describe `tool_call["id"]` as "the model-supplied `tool_call_id`". On the installed engine there is no such thing. The field stays — an OpenAI-compatible engine adapter really would need it, and re-deriving it later from rows that never stored it is impossible — but it is **reserved and always blank today**, and the code says so where a reader will find it.

**Files**
- Create: `agents/runtime/__init__.py`, `agents/runtime/prompt.py`
- Create: `agents/runtime/tests/__init__.py`, `agents/runtime/tests/_helpers.py` (this package's own — `FakeToolLLM` lives here)
- Create: `agents/runtime/tests/test_prompt.py`

**Interfaces**
- Consumes: `agents.models.Turn`, `agents.models.Conversation`, `agents.models.Agent`; `agents.limits.HISTORY_TURNS`; `agents.contracts.toolschema.wire_name` (`toolschema.py:34`); `llama_index.core.llms.ChatMessage`/`MessageRole`; `llama_index.core.base.llms.types.ToolCallBlock`.
- Produces:
  - `agents.runtime.prompt.tool_turn_messages(turn) -> list[ChatMessage]` — exactly two messages.
  - `agents.runtime.prompt.history_messages(conversation, *, limit: int = HISTORY_TURNS, before_index: int | None = None) -> list[ChatMessage]`
  - `agents.runtime.prompt.build_messages(agent, conversation, *, user_text: str = "", before_index: int | None = None) -> list[ChatMessage]` — `user_text` is **keyword-only with a blank default**, and a blank one appends no user message. The loop passes only `before_index` and lets the USER turn come out of history as a row, so there is one source of truth for what the user said; a caller with no row yet passes `user_text`.
  - `agents.runtime.tests._helpers.FakeToolLLM` — `.chat(messages, tools=None) -> ChatResponse`, `.get_tool_calls_from_response(response, error_on_no_tool_call=False) -> list[ToolSelection]`, `.calls: list` recording every `(messages, tools)` pair.

**Steps**

- [ ] Failing test first. Create `agents/runtime/tests/test_prompt.py`:

  ```python
  """History replay must be byte-identical to live append.

  A past TOOL turn renders as EXACTLY two messages, in this order: an
  ASSISTANT message carrying one `ToolCallBlock`, then a
  `MessageRole.TOOL` message carrying the result text. That is the same
  pair `agents/runtime/loop.py` appends the moment a tool actually runs.
  If the two ever diverge, a conversation's second turn sees a different
  history than its first turn wrote, and no test that looks at one turn in
  isolation would catch it.

  The WIRE name goes in `tool_name`, never the dotted key: it must match
  what the model was originally offered
  (`agents/contracts/toolschema.py::openai_tool_dict`).
  """
  from __future__ import annotations

  import pytest
  from llama_index.core.base.llms.types import ToolCallBlock
  from llama_index.core.llms import MessageRole

  from agents.models import Turn
  from agents.runtime.prompt import build_messages, history_messages, tool_turn_messages
  from agents.tests._helpers import make_agent, make_conversation, make_turn

  pytestmark = pytest.mark.django_db


  def _tool_turn(conv, index, *, tool="rag.search", text="two results"):
      return make_turn(
          conversation=conv, index=index, role=Turn.Role.TOOL, text=text,
          tool_call={"tool": tool, "args": {"query": "q"}, "agent": "general",
                     "id": "", "discarded": []},
      )


  class TestToolTurnRendering:
      def test_a_tool_turn_is_exactly_two_messages_in_order(self):
          conv = make_conversation()
          messages = tool_turn_messages(_tool_turn(conv, 0))
          assert [m.role for m in messages] == [MessageRole.ASSISTANT, MessageRole.TOOL]

      def test_the_assistant_message_carries_one_tool_call_block(self):
          conv = make_conversation()
          assistant, _ = tool_turn_messages(_tool_turn(conv, 0))
          blocks = [b for b in assistant.blocks if isinstance(b, ToolCallBlock)]
          assert len(blocks) == 1
          assert blocks[0].tool_kwargs == {"query": "q"}

      def test_the_block_carries_the_WIRE_name_not_the_dotted_key(self):
          conv = make_conversation()
          assistant, _ = tool_turn_messages(_tool_turn(conv, 0, tool="rag.search"))
          block = next(b for b in assistant.blocks if isinstance(b, ToolCallBlock))
          assert block.tool_name == "rag__search"

      def test_the_tool_message_carries_the_result_text(self):
          conv = make_conversation()
          _, tool_message = tool_turn_messages(_tool_turn(conv, 0, text="two results"))
          assert "two results" in tool_message.content

      def test_a_blank_recorded_id_becomes_a_blank_block_id(self):
          """The installed engine supplies no tool_call_id at all
          (`Ollama.chat` builds ToolCallBlock without one). The field is
          RESERVED for an OpenAI-compatible adapter; it is never
          fabricated, and never filled from `ToolSelection.tool_id`, which
          is the tool NAME."""
          conv = make_conversation()
          assistant, _ = tool_turn_messages(_tool_turn(conv, 0))
          block = next(b for b in assistant.blocks if isinstance(b, ToolCallBlock))
          assert block.tool_call_id in ("", None)

      def test_a_malformed_tool_call_dict_raises_naming_the_turn(self):
          """A tool turn with no `tool_call` is a data defect, not a state
          to render around: rendering it as an empty assistant message
          would silently drop a step out of the model's own history."""
          conv = make_conversation()
          turn = make_turn(conversation=conv, index=0, role=Turn.Role.TOOL, tool_call=None)
          with pytest.raises(ValueError) as exc:
              tool_turn_messages(turn)
          assert str(turn.index) in str(exc.value)


  class TestHistory:
      def test_history_is_ordered_and_capped(self):
          conv = make_conversation()
          for i in range(30):
              make_turn(conversation=conv, index=i, role=Turn.Role.USER, text=f"m{i}")
          messages = history_messages(conv, limit=20)
          assert len(messages) == 20
          assert messages[0].content == "m10"
          assert messages[-1].content == "m29"

      def test_a_tool_turn_inside_history_still_expands_to_two_messages(self):
          conv = make_conversation()
          make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="hi")
          _tool_turn(conv, 1)
          make_turn(conversation=conv, index=2, role=Turn.Role.ASSISTANT, text="done")
          assert [m.role for m in history_messages(conv)] == [
              MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL, MessageRole.ASSISTANT,
          ]

      def test_a_failed_or_cancelled_turn_is_not_replayed(self):
          """A turn that never produced content is not history. Replaying
          a FAILED assistant turn would feed the model an empty assistant
          message and invite it to continue from nothing."""
          conv = make_conversation()
          make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="hi")
          make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                    text="", state=Turn.State.FAILED, error="boom")
          assert [m.role for m in history_messages(conv)] == [MessageRole.USER]

      def test_before_index_excludes_the_turn_being_answered(self):
          conv = make_conversation()
          make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="old")
          make_turn(conversation=conv, index=1, role=Turn.Role.USER, text="current")
          assert [m.content for m in history_messages(conv, before_index=1)] == ["old"]


  class TestBuildMessages:
      def test_the_system_prompt_leads_and_an_explicit_user_text_trails(self):
          agent = make_agent(system_prompt="You are helpful.")
          conv = make_conversation(agent=agent)
          messages = build_messages(agent, conv, user_text="what is this?")
          assert messages[0].role == MessageRole.SYSTEM
          assert messages[0].content == "You are helpful."
          assert messages[-1].role == MessageRole.USER
          assert messages[-1].content == "what is this?"

      def test_a_blank_system_prompt_emits_no_system_message(self):
          """An empty system message is not neutral -- it is a message.
          An agent with nothing to say about itself says nothing."""
          agent = make_agent(system_prompt="")
          conv = make_conversation(agent=agent)
          assert build_messages(agent, conv, user_text="hi")[0].role == MessageRole.USER

      def test_a_blank_user_text_appends_nothing(self):
          """The loop replays the USER turn out of HISTORY rather than
          appending the payload text again. Two copies of one message is a
          conversation the model sees double."""
          agent = make_agent(system_prompt="S")
          conv = make_conversation(agent=agent)
          make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="asked")
          assert [m.content for m in build_messages(agent, conv, before_index=1)] == [
              "S", "asked",
          ]
  ```

- [ ] Run and read the failure — `ModuleNotFoundError: No module named 'agents.runtime'`:

  ```bash
  .venv/bin/pytest -q agents/runtime/tests/test_prompt.py
  ```

- [ ] Create `agents/runtime/__init__.py`:

  ```python
  """The turn runtime: prompt building, tool invocation, the bounded loop,
  and the queue job wiring.

  A PRIVATE package inside the `agents` app. Nothing outside `agents/`
  imports it, and it imports `tools/*` through NOTHING but
  `models.contracts.jobkinds.resolve_dotted_path` -- import-law rule 3,
  the clause that keeps this column from depending on the columns whose
  tools it runs. `foundation/ops/tests/test_import_law.py` and
  `foundation/ops/tests/test_column_boundaries.py` pin both halves.

  Four modules, bottom-up:
    prompt.py   -- conversation rows -> llama-index ChatMessages.
    invoke.py   -- one tool call: validate, run, classify, audit.
    loop.py     -- the bounded ReAct loop over the two above.
    jobs.py     -- planner / handler / summarizer / on_terminal for the
                   `agent.turn` job kind.
  """
  ```

- [ ] Create `agents/runtime/prompt.py`:

  ```python
  """Conversation rows -> the message list an LLM is called with.

  ONE rule governs this module: history replay and live loop append must
  produce byte-identical messages. `agents/runtime/loop.py` appends the
  pair `tool_turn_messages` returns, from the same function, at the moment
  a tool runs -- it does not build its own. A second, hand-rolled copy in
  the loop is exactly how a conversation's second turn ends up seeing a
  different history than its first turn wrote.

  VERIFIED AGAINST THE INSTALLED INTEGRATION (llama-index-core 0.14.24,
  llama-index-llms-ollama 0.10.1):

  - `ToolCallBlock` lives in `llama_index.core.base.llms.types`
    (types.py:1125-1135), NOT in `llama_index.core.llms`. Its three fields
    are `tool_call_id`, `tool_name`, `tool_kwargs`.
  - `Ollama._convert_to_ollama_messages` renders a `ToolCallBlock` as
    `{"function": {"name": ..., "arguments": ...}}` (base.py:265-286) and
    NEVER reads `tool_call_id`; it emits `message.role.value` verbatim as
    the wire role (base.py:250), so `MessageRole.TOOL` reaches Ollama as
    `"tool"` (types.py:61).
  - `Ollama.chat` builds its ToolCallBlocks with NO `tool_call_id` at all
    (base.py:427-434). THE ENGINE SUPPLIES NO ID. `Turn.tool_call["id"]`
    is therefore always "" on this path. The key is kept because an
    OpenAI-compatible engine adapter really does need it to correlate a
    result with its call, and re-deriving it later from rows that never
    stored it is impossible -- but it is RESERVED, and it is never
    fabricated. In particular it is never filled from
    `ToolSelection.tool_id`, which the same integration sets to the tool
    NAME (base.py:390-394, "tool ids not provided by Ollama").

  The WIRE name goes in `tool_name`, never the dotted key, so replayed
  history matches what the model was originally offered
  (`agents/contracts/toolschema.py`).

  ONLY `depth=0` TURNS REPLAY. A delegated agent's turns are written into
  the same conversation at depth >= 1 (spec section 6.4) so an operator
  can audit exactly what ran; they are not part of the parent's own
  history, and replaying them would hand the parent model the inside of a
  subroutine it only ever saw the return value of.
  """
  from __future__ import annotations

  from llama_index.core.base.llms.types import ToolCallBlock
  from llama_index.core.llms import ChatMessage, MessageRole

  from agents.contracts.toolschema import wire_name
  from agents.limits import HISTORY_TURNS

  # Which turn states are real history. A FAILED or CANCELLED turn never
  # produced content; replaying it would feed the model an empty assistant
  # message and invite it to continue from nothing.
  _REPLAYABLE_STATES = ("done",)

  # Only ROOT-LEVEL turns replay. A delegate's turns are written into the
  # same conversation at depth >= 1 (agent-as-tool, spec section 6.4) so an
  # operator can audit exactly what ran -- but they are NOT part of the
  # parent's own history. Replaying them would hand the parent model the
  # inside of a subroutine it only ever saw the return value of, and would
  # do it with tool-call blocks naming tools the parent may not even hold.
  _ROOT_DEPTH = 0

  _ROLE_TO_MESSAGE_ROLE = {
      "user": MessageRole.USER,
      "assistant": MessageRole.ASSISTANT,
      "system": MessageRole.SYSTEM,
  }


  def tool_turn_messages(turn) -> list[ChatMessage]:
      """A past TOOL turn as EXACTLY two messages, in this order.

      Raises `ValueError` naming the turn when `tool_call` is missing or
      malformed. A tool turn with no recorded call is a data defect, and
      rendering it as an empty assistant message would silently drop a
      step out of the model's own history -- which is worse than failing,
      because the model would then be reasoning about a conversation that
      did not happen.
      """
      call = turn.tool_call or {}
      if not isinstance(call, dict) or not call.get("tool"):
          raise ValueError(
              f"Turn {turn.index} of conversation {turn.conversation_id} is a tool "
              f"turn with no recorded tool call; history cannot be replayed."
          )
      return [
          ChatMessage(
              role=MessageRole.ASSISTANT,
              blocks=[ToolCallBlock(
                  tool_call_id=call.get("id") or "",
                  tool_name=wire_name(call["tool"]),
                  tool_kwargs=call.get("args") or {},
              )],
          ),
          ChatMessage(role=MessageRole.TOOL, content=turn.text),
      ]


  def history_messages(conversation, *, limit: int = HISTORY_TURNS,
                       before_index: int | None = None) -> list[ChatMessage]:
      """The last `limit` replayable TURNS of `conversation`, as messages.

      `limit` counts TURNS, not messages -- a tool turn expands to two
      messages and still costs one turn of the cap. A fixed cap, NOT a
      token budget: the bound model's `context_window` is an operational
      bound the platform already owns (ADR 0010:412-435), and a token
      counter here would be a second, drifting one.

      Only `depth=0` turns replay. A delegate's turns live in this same
      conversation at depth >= 1 so an operator can audit them, but they
      belong to a subroutine the parent model only ever saw the RESULT of;
      replaying them would also feed it tool-call blocks naming tools it
      may not hold. Pinned by Task 11's
      `test_a_delegates_turns_never_replay_into_the_parents_prompt`.

      `before_index` excludes the turn currently being answered, so
      `build_messages` does not replay this turn's own user text and then
      append it again.
      """
      rows = conversation.turns.filter(
          state__in=_REPLAYABLE_STATES, depth=_ROOT_DEPTH,
      )
      if before_index is not None:
          rows = rows.filter(index__lt=before_index)
      recent = list(rows.order_by("-index")[:limit])[::-1]

      messages: list[ChatMessage] = []
      for turn in recent:
          if turn.role == "tool":
              messages.extend(tool_turn_messages(turn))
          else:
              messages.append(ChatMessage(
                  role=_ROLE_TO_MESSAGE_ROLE[turn.role], content=turn.text,
              ))
      return messages


  def build_messages(agent, conversation, *, user_text: str = "",
                     before_index: int | None = None) -> list[ChatMessage]:
      """System prompt, then history, then (optionally) a user message.

      A BLANK system prompt emits no system message at all. An empty
      system message is not neutral -- it is a message, and one that tells
      the model its instructions are empty rather than absent.

      A BLANK `user_text` appends nothing. The loop passes only
      `before_index`, so the USER turn arrives through history as the ROW
      it already is -- one source of truth for what the user said, rather
      than a row and a payload copy that can differ. `user_text` exists
      for a caller that has no row yet.
      """
      messages: list[ChatMessage] = []
      if agent.system_prompt:
          messages.append(ChatMessage(role=MessageRole.SYSTEM, content=agent.system_prompt))
      messages.extend(history_messages(conversation, before_index=before_index))
      if user_text:
          messages.append(ChatMessage(role=MessageRole.USER, content=user_text))
      return messages
  ```

- [ ] Create `agents/runtime/tests/_helpers.py` with `FakeToolLLM` — the seam double the next three tasks all use:

  ```python
  """Shared test helpers for `agents/runtime/tests`.

  Plain importable module -- not a `conftest.py`. Per-package, per the
  repo convention; nothing here is imported by another app's tests.

  `FakeToolLLM` is a SEAM double, not a method mock. It is patched in at
  `models.contracts.gateway.get_llm_for` -- where `docs/DEV.md:181-186`
  says every test already mocks the gateway -- and it implements the two
  methods the loop actually calls, returning the same TYPES the installed
  integration returns (`ChatResponse` with `ToolCallBlock`s;
  `ToolSelection`). It deliberately does NOT implement `chat_with_tools`
  or `predict_and_call`: this design does not use them, and a double that
  offered them would let a loop start using them without a test noticing.
  """
  from __future__ import annotations

  from llama_index.core.base.llms.types import ChatResponse, TextBlock, ToolCallBlock
  from llama_index.core.llms import ChatMessage, MessageRole
  from llama_index.core.llms.llm import ToolSelection


  class FakeToolLLM:
      """Scripted with an ordered list of turns, each either
      `("tool", "<wire or dotted name>", {args})` or `("final", "text")`.

      Records every `(messages, tools)` pair it was called with in
      `.calls`, so a test can assert what the model was actually offered
      -- which is the only way to prove `granted_tools` filtering reached
      the prompt.

      A script entry may also be a LIST of `("tool", ...)` triples, which
      produces ONE response carrying several `ToolCallBlock`s -- the case
      that proves this platform's take-`calls[0]` policy, since plain
      `Ollama.chat` does not cap them (base.py:400-445).
      """

      def __init__(self, script):
          self.script = list(script)
          self.calls: list[tuple[list, object]] = []

      def chat(self, messages, tools=None, **kwargs):
          self.calls.append((list(messages), tools))
          if not self.script:
              raise AssertionError("FakeToolLLM ran out of script entries")
          entry = self.script.pop(0)
          if isinstance(entry, list):
              blocks = [
                  ToolCallBlock(tool_name=self._wire(name), tool_kwargs=args)
                  for _, name, args in entry
              ]
              return self._response(blocks)
          kind = entry[0]
          if kind == "tool":
              _, name, args = entry
              return self._response([
                  ToolCallBlock(tool_name=self._wire(name), tool_kwargs=args),
              ])
          _, text = entry
          return self._response([TextBlock(text=text)])

      def get_tool_calls_from_response(self, response, error_on_no_tool_call=True):
          """Mirrors `llama_index.llms.ollama.base.Ollama.
          get_tool_calls_from_response` (base.py:365-397), INCLUDING its
          one surprising behaviour: `tool_id` is set to the tool NAME,
          because Ollama supplies no ids. A double that invented real ids
          would hide the exact bug this platform must not have."""
          blocks = [b for b in response.message.blocks if isinstance(b, ToolCallBlock)]
          if not blocks:
              if error_on_no_tool_call:
                  raise ValueError("Expected at least one tool call, but got 0 tool calls.")
              return []
          return [
              ToolSelection(tool_id=b.tool_name, tool_name=b.tool_name, tool_kwargs=b.tool_kwargs)
              for b in blocks
          ]

      @staticmethod
      def _wire(name: str) -> str:
          from agents.contracts.toolschema import wire_name

          return wire_name(name)

      @staticmethod
      def _response(blocks) -> ChatResponse:
          return ChatResponse(message=ChatMessage(role=MessageRole.ASSISTANT, blocks=blocks))
  ```

  and, at the top of that same module, **import rather than re-copy** the pieces the app already has:

  ```python
  def make_tool_ctx(**overrides):
      """A `ToolContext` for calling a runner or a loop directly. Built on
      the app-level helpers imported below rather than re-deriving them."""
      from agents.contracts.tools import ToolContext

      fields = dict(
          conversation_id="00000000-0000-0000-0000-000000000000",
          principal=make_principal(),
          depth=0,
          budget=make_budget(),
          job=make_job_ctx(),
          tool_key="",
      )
      fields.update(overrides)
      return ToolContext(**fields)


  # Same APP, not another one -- `agents/tests/_helpers.py` is this app's
  # helper module and `agents/runtime/` is a package inside it. The
  # per-app duplication rule exists to stop one app's test scaffolding
  # becoming load-bearing for another's; a sixth copy of `make_job_ctx`
  # INSIDE the same app would be duplication with no boundary to justify
  # it. Re-exported here so a test module in this package can import
  # everything it needs from one place.
  from agents.tests._helpers import (  # noqa: F401
      CALLS, bind_chat_role, bound_chat_role, bound_embed_role, make_agent,
      make_budget, make_conversation, make_job_ctx, make_principal, make_turn,
      restore_tools, snapshot_tools,
  )
  ```

- [ ] Add to **`agents/tests/_helpers.py`** the pieces this task and Tasks 8–12 need, including the real role binding that M1 turns on. `_supports_tool_calling` and `resolve()` are the two things a loop test cannot fake away, and a `ModelConnection` + `RoleBinding` pair is cheaper than teaching every test to patch two seams:

  ```python
  def make_job_ctx(**overrides):
      """A `models.contracts.jobkinds.JobContext` with inert writers -- no
      real worker and no DB behind it."""
      from models.contracts.jobkinds import JobContext

      fields = dict(
          job_id=1,
          attempt=0,
          checkpoint_state=None,
          _report=lambda progress: None,
          _checkpoint=lambda state: None,
      )
      fields.update(overrides)
      return JobContext(**fields)


  def make_budget(**overrides):
      """A `StepBudget` with room to spare and a deadline far enough out
      that a test never trips it by accident."""
      import time

      from agents.contracts.tools import StepBudget

      fields = dict(steps=8, deadline_monotonic=time.monotonic() + 900.0, recoveries=1)
      fields.update(overrides)
      return StepBudget(**fields)


  def make_principal(**overrides):
      from agents.contracts.tools import Principal

      fields = dict(kind="resident_agent", key="test-agent")
      fields.update(overrides)
      return Principal(**fields)


  def bind_chat_role(role_key, *, name="test-chat", capability="chat", embed_dim=None):
      """A real `ModelConnection` bound to `role_key`, so
      `models.contracts.bindings.resolve` RESOLVES instead of raising.

      The loop and the planner both call `resolve()` for real (they are
      the code under test), and a delegate resolves its own agent's role
      too -- so a loop test that patched `resolve` would be testing a
      different function than the one that ships. A row is cheaper and
      more honest than two patches.

      A test importing `models.registry.models` directly is the exemption
      `foundation/ops/tests/test_import_law.py:19-30` already documents:
      this codebase has no shared factory layer, and the gate polices
      PRODUCTION imports only.
      """
      from models.registry.models import ModelConnection, RoleBinding

      # `capabilities` (plural) is a JSONField LIST drawn from
      # `models.contracts.roles.CAPABILITIES` -- `models/registry/models.py:63`.
      # There is no singular `capability` field; passing one is a silent
      # TypeError at construction. Precedent:
      # `models/registry/tests/_helpers.py:18-33`'s `make_chat_connection`.
      fields = dict(
          name=name, engine="ollama", endpoint="http://localhost:11434",
          model_id="test-model", capabilities=[capability],
      )
      if embed_dim is not None:
          fields["embed_dim"] = embed_dim
      connection = ModelConnection.objects.create(**fields)
      RoleBinding.objects.update_or_create(
          role_key=role_key, defaults={"connection": connection},
      )
      return connection


  @pytest.fixture
  def bound_chat_role(db):
      from models.contracts.roles import CHAT_CONVERSE_ROLE

      return bind_chat_role(CHAT_CONVERSE_ROLE)


  @pytest.fixture
  def bound_embed_role(db):
      from models.contracts.roles import RAG_EMBED_ROLE

      return bind_chat_role(
          RAG_EMBED_ROLE, name="test-embed", capability="embeddings", embed_dim=768,
      )
  ```

  **The field is `capabilities`, plural, and it is a JSONField LIST** (`models/registry/models.py:63`) — not a singular `capability` string. `models/registry/tests/_helpers.py:18-51` is the precedent for both builders; read it before writing this one. `embed_dim` matters for an embeddings connection (`_helpers.py:47`) and nothing else here does.

- [ ] Run green, then the full matrix:

  ```bash
  .venv/bin/pytest -q agents/runtime/tests/test_prompt.py
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  ```

- [ ] Commit:

  ```bash
  git add agents/runtime
  git commit -m "$(cat <<'EOF'
  feat(agents): conversation rows -> llama-index messages

  One rule: history replay and live loop append must be byte-identical.
  The loop calls the SAME tool_turn_messages() this module exports rather
  than building its own pair -- a second hand-rolled copy is exactly how a
  conversation's second turn ends up seeing a different history than its
  first turn wrote.

  A past TOOL turn is exactly two messages: an ASSISTANT message carrying
  one ToolCallBlock, then a MessageRole.TOOL message with the result text.
  The WIRE name goes in tool_name, never the dotted key, so replayed
  history matches what the model was originally offered.

  Spec correction, verified against the installed integration: sections
  6.2 and 7.3 describe tool_call["id"] as "the model-supplied
  tool_call_id". Ollama.chat builds its ToolCallBlocks with no
  tool_call_id at all, and get_tool_calls_from_response sets
  ToolSelection.tool_id to the tool NAME ("tool ids not provided by
  Ollama"). The key is kept -- an OpenAI-compatible adapter needs it and
  it cannot be re-derived later -- but it is RESERVED, always blank on
  this path, and never filled from tool_id.

  A FAILED or CANCELLED turn is not replayed: it never produced content,
  and feeding the model an empty assistant message invites it to continue
  from nothing. A tool turn with no recorded call raises naming the turn
  rather than rendering as an empty message that would silently drop a
  step out of the model's own history.

  FakeToolLLM is a SEAM double at gateway.get_llm_for, returning the real
  ChatResponse/ToolSelection types -- including Ollama's own tool_id
  quirk, so a double never hides the bug the platform must not have.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 7: `agents/runtime/invoke.py` — one tool call: validate, run, classify, audit

Every tool call in this platform goes through this one function, whether it came from an agent's loop, from an agent-as-tool delegate, or — later — from the MCP edge. It writes the `ToolInvocation` row (addendum consequence 3) and it turns four possible outcomes into a closed classification the loop can act on without parsing prose.

#### The except-ordering rule, and where the retry policy is settled

`agents/contracts/README.md`'s **"Two failure classes, and two more outcomes beside them"** section is the authority, and it says the thing that matters: `ToolRefused` and `ParamError` are **both `ValueError` subclasses** (`agents/contracts/tools.py:46`, `models/contracts/operations.py:228`). A loop that reaches `except ValueError` before it reaches the two narrower clauses never lands in either, and every honest refusal silently becomes whatever the wide clause does. **`invoke_tool` catches `ToolRefused`, then `ParamError`, then `ValueError`, in that order**, and a test plants a subclass of each to prove the ordering bites.

**A reconciliation, stated rather than fudged.** Spec §10.1's table gives "tool raises" one retry; `agents/contracts/README.md` says a plain `ValueError` gets none. Both are describing the same intent from different angles, and P2 implements it as one rule:

| Outcome | Step spent | Recovery spent | Fed back to the model | Still offered next step |
|---|---|---|---|---|
| `ok` | yes | no | the `ToolResult.text` | yes |
| `degraded` | yes | no | the `ToolResult.text` (which says what degraded) | yes |
| `param_error` | yes | **yes** | `ParamError.errors`, the per-arg map of key → reason | **yes** — this is the repairable one |
| `error` | yes | **yes** | `str(exc)`, never a traceback | yes — the failure may be about an argument's *referent* (a missing row id), and a different one may work |
| `refused` | yes | **yes** | the refusal message | **no — the tool is dropped from the rest of this turn** |

Dropping a refused tool is what makes the README's "no retry" literally true: the model is not offered a tool it will be refused for using, the same mechanism §6.4 uses for the depth cap ("enforced by omission, not by refusal"). A second failure of any class with no recovery left ends the turn with an honest final naming both failures (§10.2).

#### `degraded` is defined here and produced by nobody yet, on purpose

The README's fourth outcome — "a `ToolResult` whose `.text` reports a degraded state" — cannot be inferred from a `ToolResult` generically, so `invoke_tool` reads an explicit opt-in: `result.data.get("degraded") is True`. **No v1 runner sets it today** (`rag.ingest`'s queue-down branch and `vision.generate`'s still-running branch are the two natural candidates, and both are outside this plan's scope — one is `tools/rag`, the other is VISION-OWNED). The classifier is nonetheless implemented and tested with a stub tool that does set it, so the column is defined and proven rather than reserved and untested. Making the two runners set it is a one-line change in each, in whichever phase owns them.

**Files**
- Create: `agents/runtime/invoke.py`
- Create: `agents/runtime/tests/test_invoke.py`

**Interfaces**
- Consumes: `agents.contracts.tools.ToolSpec`/`ToolResult`/`ToolRefused`/`ToolContext`/`validate_tool_args` (`tools.py:203`); `models.contracts.operations.ParamError` (`operations.py:228`); `models.contracts.jobkinds.resolve_dotted_path` (`jobkinds.py:273`); `agents.models.ToolInvocation`; `django.utils.timezone`.
- Produces:
  - `agents.runtime.invoke.ToolOutcome` — frozen dataclass: `.outcome: str`, `.text: str`, `.args: dict`, `.result: ToolResult | None`, `.invocation_id: int | None`; properties `.failed: bool`, `.bars_retry: bool`.
  - `agents.runtime.invoke.invoke_tool(spec: ToolSpec, args: dict, tool_ctx: ToolContext) -> ToolOutcome` — **never raises for a tool failure.** It re-raises only what is not a tool failure at all.
  - `agents.runtime.invoke.invoke_unknown_tool(wire: str, args: dict, tool_ctx: ToolContext) -> ToolOutcome` — an unknown wire name is a tool error with an audit row, never a crash and never a fuzzy match (§10.3).

**Steps**

- [ ] Failing test first. Create `agents/runtime/tests/test_invoke.py`:

  ```python
  """One tool call, four outcome classes, and the ordering that keeps them
  apart.

  `ToolRefused` and `ParamError` are BOTH `ValueError` subclasses
  (`agents/contracts/tools.py:46`, `models/contracts/operations.py:228`).
  `agents/contracts/README.md`'s "Two failure classes" section is the
  authority on what that means: a handler that reaches `except ValueError`
  first never lands in either narrower branch, and every honest refusal
  becomes whatever the wide clause does. These tests plant a SUBCLASS of
  each so a reordered try/except is caught by classification, not by luck.
  """
  from __future__ import annotations

  import pytest

  from agents.contracts.tools import ToolRefused, ToolResult, ToolSpec
  from agents.models import ToolInvocation
  from agents.runtime.invoke import invoke_tool, invoke_unknown_tool
  from agents.runtime.tests import _helpers
  from agents.runtime.tests._helpers import make_tool_ctx, restore_tools, snapshot_tools
  from models.contracts.operations import Param, ParamError

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  def _spec(runner: str, **overrides) -> ToolSpec:
      fields = dict(key="stub.tool", label="Stub", description="A stub.", runner=runner)
      fields.update(overrides)
      return ToolSpec(**fields)


  class TestOutcomeClassification:
      def test_a_returned_result_is_ok(self):
          out = invoke_tool(_spec("agents.runtime.tests._helpers.runner_ok"), {}, make_tool_ctx())
          assert out.outcome == ToolInvocation.Outcome.OK
          assert out.text == "it worked"
          assert out.failed is False

      def test_a_result_flagged_degraded_is_degraded_not_ok(self):
          out = invoke_tool(
              _spec("agents.runtime.tests._helpers.runner_degraded"), {}, make_tool_ctx()
          )
          assert out.outcome == ToolInvocation.Outcome.DEGRADED
          assert out.failed is False        # degraded is NOT a failure
          assert "queue" in out.text

      def test_a_tool_refused_subclass_classifies_as_refused_not_error(self):
          out = invoke_tool(
              _spec("agents.runtime.tests._helpers.runner_refused_subclass"), {}, make_tool_ctx()
          )
          assert out.outcome == ToolInvocation.Outcome.REFUSED
          assert out.failed is True
          assert out.bars_retry is True

      def test_a_param_error_subclass_classifies_as_param_error(self):
          out = invoke_tool(
              _spec("agents.runtime.tests._helpers.runner_param_error_subclass"), {},
              make_tool_ctx(),
          )
          assert out.outcome == ToolInvocation.Outcome.PARAM_ERROR
          assert out.bars_retry is False

      def test_a_param_error_feeds_back_its_per_arg_reasons(self):
          """`.errors` maps a param key to a human-readable reason
          (`operations.py:228-236`), which is exactly the information a
          model needs to fix its own call -- and the reason this is the
          one class worth a repair attempt."""
          out = invoke_tool(
              _spec("agents.runtime.tests._helpers.runner_param_error_subclass"), {},
              make_tool_ctx(),
          )
          assert "top_k" in out.text and "too large" in out.text

      def test_a_plain_value_error_classifies_as_error(self):
          out = invoke_tool(
              _spec("agents.runtime.tests._helpers.runner_value_error"), {}, make_tool_ctx()
          )
          assert out.outcome == ToolInvocation.Outcome.ERROR
          assert out.bars_retry is False

      def test_a_non_value_error_exception_is_caught_and_classified_as_error(self):
          """Never-500: a runner that raises something nobody classified
          still becomes an honest tool error with `str(exc)`, never a
          traceback and never a crashed turn."""
          out = invoke_tool(
              _spec("agents.runtime.tests._helpers.runner_runtime_error"), {}, make_tool_ctx()
          )
          assert out.outcome == ToolInvocation.Outcome.ERROR
          assert "Traceback" not in out.text

      def test_validation_runs_before_the_runner_is_even_resolved(self):
          """A bad argument must not reach a runner at all -- the schema
          floor is the first gate, and a runner that never ran cannot have
          side effects to undo."""
          spec = _spec(
              "agents.runtime.tests._helpers.runner_ok",
              params=(Param("query", "text", "Query", required=True),),
          )
          _helpers.CALLS.clear()
          out = invoke_tool(spec, {}, make_tool_ctx())
          assert out.outcome == ToolInvocation.Outcome.PARAM_ERROR
          assert _helpers.CALLS == []


  class TestTheAuditRow:
      def test_every_call_writes_exactly_one_invocation_row(self):
          invoke_tool(_spec("agents.runtime.tests._helpers.runner_ok"), {}, make_tool_ctx())
          assert ToolInvocation.objects.count() == 1

      def test_the_row_carries_the_principal_and_the_validated_args(self):
          spec = _spec(
              "agents.runtime.tests._helpers.runner_ok",
              params=(Param("query", "text", "Query", required=True),),
          )
          invoke_tool(spec, {"query": "hello"}, make_tool_ctx())
          row = ToolInvocation.objects.get()
          assert (row.principal_kind, row.principal_key) == ("resident_agent", "test-agent")
          assert row.tool_key == "stub.tool"
          assert row.args == {"query": "hello"}

      def test_a_failure_row_carries_the_error_and_no_result_text(self):
          invoke_tool(
              _spec("agents.runtime.tests._helpers.runner_value_error"), {}, make_tool_ctx()
          )
          row = ToolInvocation.objects.get()
          assert row.error and row.text == ""

      def test_the_row_is_finished_and_has_a_duration(self):
          invoke_tool(_spec("agents.runtime.tests._helpers.runner_ok"), {}, make_tool_ctx())
          row = ToolInvocation.objects.get()
          assert row.finished_at is not None
          assert row.duration_ms is not None and row.duration_ms >= 0

      def test_a_row_is_written_even_when_validation_fails(self):
          """An invocation that never reached its runner still HAPPENED. A
          principal that spends a turn sending malformed calls must be
          visible in the same table as one that succeeds."""
          spec = _spec(
              "agents.runtime.tests._helpers.runner_ok",
              params=(Param("query", "text", "Query", required=True),),
          )
          invoke_tool(spec, {}, make_tool_ctx())
          assert ToolInvocation.objects.get().outcome == ToolInvocation.Outcome.PARAM_ERROR


  class TestToolKeyIsThreadedIn:
      def test_the_runner_sees_its_own_spec_key_on_the_context(self):
          """Deviation D4's mechanism: N `agent.<slug>` specs share one
          runner, and `ctx.tool_key` is the only way one can learn which
          spec invoked it."""
          _helpers.CALLS.clear()
          invoke_tool(
              _spec("agents.runtime.tests._helpers.runner_ok", key="agent.library"),
              {}, make_tool_ctx(),
          )
          _args, ctx = _helpers.CALLS[-1]
          assert ctx.tool_key == "agent.library"

      def test_the_callers_context_is_not_mutated(self):
          """`ToolContext` is frozen; `invoke_tool` builds a replacement
          rather than reaching into the caller's."""
          ctx = make_tool_ctx()
          invoke_tool(_spec("agents.runtime.tests._helpers.runner_ok"), {}, ctx)
          assert ctx.tool_key == ""


  class TestUnknownToolName:
      def test_an_unknown_wire_name_is_a_tool_error_with_an_audit_row(self):
          """Section 10.3: never guessed at, never fuzzy-matched, never
          silently ignored."""
          out = invoke_unknown_tool("no__such__tool", {"a": 1}, make_tool_ctx())
          assert out.outcome == ToolInvocation.Outcome.ERROR
          assert out.failed is True
          row = ToolInvocation.objects.get()
          assert row.tool_key == "no.such.tool"      # round-tripped, then reported honestly
          assert "no.such.tool" in out.text
  ```

  with the six named runners added to `agents/runtime/tests/_helpers.py` — `runner_ok`, `runner_degraded`, `runner_refused_subclass` (raises a locally-defined `ToolRefused` subclass), `runner_param_error_subclass` (raises a `ParamError` subclass with `{"top_k": "50 is too large"}`), `runner_value_error`, `runner_runtime_error` — each appending to `CALLS`.

- [ ] Run and read the failure — `ModuleNotFoundError: No module named 'agents.runtime.invoke'`:

  ```bash
  .venv/bin/pytest -q agents/runtime/tests/test_invoke.py
  ```

- [ ] Create `agents/runtime/invoke.py`:

  ```python
  """One tool call: validate, run, classify, audit.

  EVERY tool call this platform makes goes through `invoke_tool` -- an
  agent's own loop, an agent-as-tool delegate, and (once the MCP edge
  lands, 2026-08-27 addendum) an external `tools/call`. That is why the
  audit row it writes is keyed on a PRINCIPAL and not on a conversation,
  and why this function never assumes a `Turn` exists.

  IT NEVER RAISES FOR A TOOL FAILURE. A failure is a classified outcome,
  because the loop's recovery policy is defined per class and a caller
  that had to re-derive the class from an exception type would be a second
  copy of the rule below.

  THE EXCEPT ORDERING IS LOAD BEARING, and
  `agents/contracts/README.md`'s "Two failure classes, and two more
  outcomes beside them" section is its authority. `ToolRefused`
  (`agents/contracts/tools.py:46`) and `ParamError`
  (`models/contracts/operations.py:228`) are BOTH `ValueError` subclasses.
  Catching `ValueError` before either narrower clause means never reaching
  them: every honest refusal and every repairable argument error alike
  falls into the wide branch. So:

      except ToolRefused -> refused
      except ParamError  -> param_error
      except ValueError  -> error
      except Exception   -> error        (never-500: str(exc), never a traceback)

  Reordering these is a silent behaviour change, not a style choice.

  `degraded` is an OPT-IN a runner declares with `data["degraded"] = True`
  -- it cannot be inferred from a `ToolResult` generically. No v1 runner
  sets it yet; the classifier is implemented and tested against a stub so
  the outcome class is defined and proven rather than reserved and
  untested.
  """
  from __future__ import annotations

  import logging
  from dataclasses import dataclass, replace

  from django.utils import timezone

  from agents.contracts.tools import (
      ToolContext, ToolRefused, ToolResult, ToolSpec, validate_tool_args,
  )
  from agents.contracts.toolschema import key_from_wire_name
  from agents.models import ToolInvocation
  from models.contracts.jobkinds import resolve_dotted_path
  from models.contracts.operations import ParamError

  logger = logging.getLogger(__name__)


  @dataclass(frozen=True)
  class ToolOutcome:
      """What one tool call produced, as a closed classification.

      `text` is what the model is told -- the ONLY part of this a prompt
      ever sees. `result` is the runner's own `ToolResult` on a
      non-failure and `None` otherwise, so the loop can read `.data` and
      `.artifacts` without a second lookup. `args` is the VALIDATED
      argument dict (the raw ones when validation itself failed), so the
      loop records in `Turn.tool_call["args"]` what actually ran rather
      than what was asked for -- that dict is replayed into a later
      prompt, and replaying a rejected argument would teach the model it
      was accepted.
      """

      outcome: str
      text: str
      args: dict
      result: ToolResult | None
      invocation_id: int | None

      @property
      def failed(self) -> bool:
          """`degraded` is deliberately NOT a failure: the call succeeded
          and what it has to report is a less-than-ideal state."""
          return self.outcome in (
              ToolInvocation.Outcome.REFUSED,
              ToolInvocation.Outcome.PARAM_ERROR,
              ToolInvocation.Outcome.ERROR,
          )

      @property
      def bars_retry(self) -> bool:
          """A refused tool is DROPPED from the rest of the turn, which is
          what makes "no retry" literal rather than hoped for: the model
          is never offered a tool it will be refused for using -- the same
          enforcement-by-omission spec section 6.4 uses for the depth cap.
          """
          return self.outcome == ToolInvocation.Outcome.REFUSED


  def invoke_tool(spec: ToolSpec, args: dict, tool_ctx: ToolContext) -> ToolOutcome:
      """Validate `args` against `spec`, run its runner, classify, audit."""
      row = ToolInvocation.objects.create(
          principal_kind=tool_ctx.principal.kind,
          principal_key=tool_ctx.principal.key,
          tool_key=spec.key,
          args={},
          outcome=ToolInvocation.Outcome.ERROR,   # overwritten below; never left as a guess
      )
      try:
          clean = validate_tool_args(spec, args)
      except ParamError as exc:
          row.args = args if isinstance(args, dict) else {}
          return _finish(row, ToolInvocation.Outcome.PARAM_ERROR,
                         text=_param_error_text(spec, exc), error=str(exc))
      row.args = clean
      row.save(update_fields=["args"])

      # Resolved lazily, by dotted path: this module never imports
      # `tools.*` (import-law rule 3), which is what keeps `agents/` free
      # of the columns whose tools it runs.
      runner = resolve_dotted_path(spec.runner)
      # `ToolContext` is frozen; build a replacement rather than reaching
      # into the caller's. `tool_key` is how a runner shared by several
      # specs learns which one invoked it (deviation D4).
      ctx = replace(tool_ctx, tool_key=spec.key)

      try:
          result = runner(clean, ctx)
      except ToolRefused as exc:                       # MUST precede ParamError/ValueError
          return _finish(row, ToolInvocation.Outcome.REFUSED, text=str(exc), error=str(exc))
      except ParamError as exc:                        # MUST precede ValueError
          return _finish(row, ToolInvocation.Outcome.PARAM_ERROR,
                         text=_param_error_text(spec, exc), error=str(exc))
      except ValueError as exc:
          return _finish(row, ToolInvocation.Outcome.ERROR, text=str(exc), error=str(exc))
      except Exception as exc:  # noqa: BLE001 -- never-500: an unclassified runner failure
          logger.exception("agents: tool %r raised an unclassified exception", spec.key)
          return _finish(row, ToolInvocation.Outcome.ERROR, text=str(exc), error=str(exc))

      outcome = (
          ToolInvocation.Outcome.DEGRADED
          if isinstance(result.data, dict) and result.data.get("degraded") is True
          else ToolInvocation.Outcome.OK
      )
      return _finish(row, outcome, text=result.text, result=result)


  def invoke_unknown_tool(wire: str, args: dict, tool_ctx: ToolContext) -> ToolOutcome:
      """A wire name the model emitted that maps to no registered tool.

      A tool error, not a crash (spec section 10.3): it produces a tool
      turn saying the tool does not exist, spends a step, and spends the
      recovery. NEVER guessed at, fuzzy-matched, or silently ignored -- a
      model that is quietly given a different tool than it asked for
      produces an answer nobody can audit.
      """
      key = key_from_wire_name(wire)
      row = ToolInvocation.objects.create(
          principal_kind=tool_ctx.principal.kind,
          principal_key=tool_ctx.principal.key,
          tool_key=key,
          args=args if isinstance(args, dict) else {},
          outcome=ToolInvocation.Outcome.ERROR,
      )
      message = (
          f"There is no tool called {key!r} on this system. Use one of the tools you "
          f"were given, or answer without one."
      )
      return _finish(row, ToolInvocation.Outcome.ERROR, text=message, error=message)


  def _param_error_text(spec: ToolSpec, exc: ParamError) -> str:
      """A `ParamError` rendered as the per-arg reasons a model can act on.

      `.errors` maps a param key to a human-readable reason
      (`models/contracts/operations.py:228-236`). Handing that back
      verbatim is the whole reason this is the one failure class worth a
      repair attempt.
      """
      reasons = "; ".join(f"{key}: {reason}" for key, reason in sorted(exc.errors.items()))
      return f"The arguments for {spec.key} were not accepted -- {reasons}"


  def _finish(row: ToolInvocation, outcome: str, *, text: str = "", error: str = "",
              result: ToolResult | None = None) -> ToolOutcome:
      row.outcome = outcome
      row.text = text if result is not None else ""
      row.error = error
      row.finished_at = timezone.now()
      row.save(update_fields=["args", "outcome", "text", "error", "finished_at"])
      return ToolOutcome(
          outcome=outcome, text=text, args=dict(row.args or {}),
          result=result, invocation_id=row.pk,
      )
  ```

- [ ] Run green, then **prove the ordering guard is not vacuous** by deliberately reordering the `except` clauses in a scratch edit, re-running, and confirming `test_a_tool_refused_subclass_classifies_as_refused_not_error` goes red. Revert the scratch edit. A guard nobody has watched bite is a guard nobody knows works.

- [ ] Full matrix, then commit:

  ```bash
  git add agents/runtime
  git commit -m "$(cat <<'EOF'
  feat(agents): invoke_tool -- validate, run, classify, audit

  Every tool call goes through this one function: an agent loop, an
  agent-as-tool delegate, and later an external MCP tools/call. That is
  why the ToolInvocation row it writes is keyed on a PRINCIPAL and never
  assumes a Turn exists.

  It never raises for a tool failure. A failure is a classified outcome,
  because the loop's recovery policy is defined per class and a caller
  re-deriving the class from an exception type would be a second copy of
  the rule.

  The except ORDERING is load-bearing and agents/contracts/README.md's
  "Two failure classes" section is its authority: ToolRefused and
  ParamError are both ValueError subclasses, so catching ValueError first
  means never reaching either. ToolRefused -> ParamError -> ValueError ->
  Exception, in that order. Tests plant a SUBCLASS of each, and the
  ordering was watched to bite by deliberately reversing it.

  A refused tool is DROPPED from the rest of the turn rather than merely
  "not retried" -- enforcement by omission, the same mechanism section 6.4
  uses for the depth cap. A ParamError feeds back .errors, the per-arg map
  that makes it the one class worth a repair attempt.

  `degraded` is an opt-in a runner declares with data["degraded"]=True; no
  v1 runner sets it yet, and the classifier is tested against a stub that
  does, so the outcome class is defined and proven rather than reserved
  and untested.

  An unknown wire name is a tool error with an audit row -- never guessed
  at, never fuzzy-matched, never silently ignored.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 8: `agents/runtime/loop.py` — the bounded ReAct loop

Spec §6.2, end to end. Everything the model actually does happens here, and every way this can end is an honest one.

#### Two shape decisions this task settles

**1. `run_turn` lives here, and nowhere else.** The P2 brief lists `run_turn` under both `loop.py` and `jobs.py`. Two functions with one name, one calling the other, is a name nobody can grep for. `agents/runtime/loop.py::run_turn` is the job kind's `handler` dotted path; `jobs.py` owns `plan_turn`, `summarize_turn`, and `on_turn_terminal` and does not wrap it.

**2. Turn indices, and why the assistant turn moves.** The enqueuing caller (Task 12) creates **two** rows before it enqueues: the USER turn (`state=done`) and a placeholder ASSISTANT turn (`state=queued`), and the payload carries the assistant turn's pk — that placeholder is what `on_turn_terminal` flips when the job never runs (§10.3), and it is why the hook can exist at all. But the loop writes TOOL turns *while it runs*, and those must sort **before** the assistant answer. So: tool turns take the next free indices, and immediately before writing its final content the loop **moves the assistant turn to the end** with one `UPDATE`. This leaves a gap at the placeholder's original index. That is fine and deliberate — `ordering = ["index"]` and `uniq_turn_index` are both indifferent to gaps, and the alternative (reserving indices up front) would mean guessing how many tools a turn will call.

#### One `resolve_chat`, shared by the planner and the loop

`plan_turn` (Task 9) and `run_turn` both have to answer "which model answers this turn" — the picked connection if the caller supplied one, otherwise the agent's own role. **That is one rule and it gets one implementation**, `agents/runtime/bindings.py::resolve_chat`, imported by both.

This deliberately does *not* follow `tools/rag/jobs.py`'s two-resolution-pass shape. That module keeps `plan_ask` and `_precheck` separate because its run-time pass does something extra and different — it **health-checks** each endpoint and folds failures into a user-facing message (`jobs.py:151-209`). P2's loop does no health check, so the two calls here are the same call, and duplicating it would be duplication with no difference to justify it. `_resolve_answer` (`tools/rag/jobs.py:109-130`) is the shape `resolve_chat` mirrors — a single helper both passes call — not a precedent for splitting it.

#### Post-deadline latency: documented, not fixed

`budget.expired` is checked at the top of every iteration and before every tool call — **never during one**. A `vision.generate` that enters `services.wait_for` just under the deadline can return long after it (vision's own wait timeout is 600s). The turn then ends immediately with the honest deadline sentence *plus that tool's actual result*. Killing a tool mid-flight would strand a submitted generation and throw away work that really was done, so this is the deliberate trade and `loop.py`'s docstring says so.

#### The recovery policy, as implemented

From Task 7's table. `StepBudget.recoveries_left` starts at 1 (`agents/contracts/tools.py:160-165`). Every failure spends a step and spends the recovery; a refusal additionally drops that tool from the rest of the turn; a second failure with none left ends the turn naming both.

**Files**
- Create: `agents/runtime/bindings.py` — `resolve_chat`, the one place the picker-override rule lives
- Create: `agents/runtime/loop.py`
- Create: `agents/runtime/tests/test_loop.py`
- Modify: `agents/runtime/tests/_helpers.py` — the `patch_llm` context manager

**Interfaces**
- Consumes: `agents.models.Agent`/`Conversation`/`Turn`/`ToolInvocation`; `agents.limits.*`; `agents.contracts.tools.Principal`/`StepBudget`/`ToolContext`/`granted_tools`/`get_tool`/`_TOOLS`; `agents.contracts.toolschema.openai_tool_dict`/`key_from_wire_name`; `agents.runtime.prompt.build_messages`/`tool_turn_messages`; `agents.runtime.invoke.invoke_tool`/`invoke_unknown_tool`; `models.contracts.gateway.get_llm_for` (`gateway.py:65`); `models.contracts.bindings.resolve` (`bindings.py:130`); `models.registry.bindings.resolve_connection_named` (`bindings.py:317`, the one sanctioned cross-column import); `models.contracts.engines.get_engine`; `models.contracts.jobkinds.JobContext`.
- Produces:
  - `agents.runtime.loop.run_turn(payload: dict, models: list, ctx: JobContext) -> dict` returning `{"turn_id", "text", "artifacts", "tool_calls", "steps_used", "answered_by", "summary"}`. **`answered_by`** is the picked connection's display name on the override path and `""` on the role path — the same "answered by" fact `tools/rag/jobs.py:253` records, and the one thing the P3 chat surface will need that it cannot re-derive after the fact.
  - `agents.runtime.bindings.resolve_chat(agent, connection) -> tuple[ResolvedModel, str]` — see the shared-resolution note below.

**Steps**

- [ ] Add `patch_llm` to `agents/runtime/tests/_helpers.py` — a context manager that patches **two** things and nothing else:

  1. **`models.contracts.gateway.get_llm_for`** → the supplied `FakeToolLLM`. This is the seam `docs/DEV.md`'s offline-suite paragraph names, and patching it leaves the loop's own resolution, filtering, message building, and schema rendering really running. **This only works because `loop.py` and `delegate.py` call `gateway.get_llm_for(...)` through the module rather than importing the name.** A `from models.contracts.gateway import get_llm_for` at the top of either file copies the function object into that module's namespace at import time, and patching the attribute on `models.contracts.gateway` afterwards would never be seen — the test would pass a double to nobody and drive a real engine. Both files carry a comment saying so at the call site.
  2. **`agents.runtime.loop._supports_tool_calling`** → `None`. **This one is load-bearing, not convenience.** M1 gives these tests a real `ModelConnection` and `RoleBinding`, so `resolve()` returns a real `ResolvedModel` — and the unpatched `_supports_tool_calling` would then reach `get_engine(...).supports_tool_calling(...)`, which is a live HTTP call to Ollama's `/api/show`. `docs/DEV.md` promises "**Ollama does not need to be running**" for the suite, so leaving it unpatched would break that promise on every loop test. `None` is also the honest value: it is what an engine that does not report the fact returns, and it is the state a test that is not about the capability gate should be in. The two tests that ARE about the gate override it themselves.

  **Do not patch `models.contracts.bindings.resolve`.** It is code under test — the loop, the planner, and every delegate call it for real — and a test that patched it would be exercising a different function than the one that ships. A row is cheaper and more honest than a patch (M1).

- [ ] Failing test first. Create `agents/runtime/tests/test_loop.py`:

  ```python
  """The bounded loop: every way a turn can end, and each one honest.

  The double is patched in at `models.contracts.gateway.get_llm_for` --
  the seam `docs/DEV.md:181-186` names -- so the loop's own resolution,
  message building, and schema rendering all really run.
  """
  from __future__ import annotations

  import time

  import pytest

  from agents.contracts.tools import ToolSpec, register_tool
  from agents.models import ToolInvocation, Turn
  from agents.runtime.loop import run_turn
  from agents.runtime.tests._helpers import (
      FakeToolLLM, bind_chat_role, make_agent, make_conversation, make_job_ctx,
      make_turn, patch_llm, restore_tools, snapshot_tools,
  )
  from models.contracts.roles import CHAT_CONVERSE_ROLE

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  def _setup(tool_keys=(), *, max_steps=8, script=(("final", "done"),)):
      # A REAL connection and role binding, not a patched `resolve()`:
      # `resolve` is code under test here (M1). `patch_llm` still supplies
      # the LLM itself at the gateway seam, so nothing reaches a network.
      bind_chat_role(CHAT_CONVERSE_ROLE)
      agent = make_agent(slug="general", tool_keys=list(tool_keys), max_steps=max_steps)
      conv = make_conversation(agent=agent)
      make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="a question")
      assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                            state=Turn.State.QUEUED)
      payload = {"conversation": str(conv.id), "turn": assistant.pk,
                 "agent": agent.slug, "text": "a question",
                 "connection": None, "mode": "chat"}
      return agent, conv, assistant, payload, FakeToolLLM(script)


  class TestTheSimplePaths:
      def test_a_no_tool_agent_makes_one_llm_call_and_one_assistant_turn(self):
          _a, conv, assistant, payload, llm = _setup()
          with patch_llm(llm):
              result = run_turn(payload, [], make_job_ctx())
          assert len(llm.calls) == 1
          assistant.refresh_from_db()
          assert assistant.state == Turn.State.DONE
          assert assistant.text == "done"
          assert result["steps_used"] == 1
          assert conv.turns.filter(role=Turn.Role.TOOL).count() == 0

      def test_a_no_tool_agent_is_offered_no_tools_at_all(self):
          """`tools=None`, not `tools=[]`: an empty list is a claim that
          there are tools and none apply."""
          _a, _c, _t, payload, llm = _setup()
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assert llm.calls[0][1] is None

      def test_one_tool_path_is_llm_tool_llm_and_three_turn_rows(self):
          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          _a, conv, assistant, payload, llm = _setup(
              ["stub.safe"],
              script=[("tool", "stub.safe", {}), ("final", "here is the answer")],
          )
          with patch_llm(llm):
              result = run_turn(payload, [], make_job_ctx())
          assert len(llm.calls) == 2
          assert [t.role for t in conv.turns.all()] == [
              Turn.Role.USER, Turn.Role.TOOL, Turn.Role.ASSISTANT,
          ]
          assert result["steps_used"] == 2      # the tool step, then the final step
          assistant.refresh_from_db()
          assert assistant.text == "here is the answer"

      def test_the_assistant_turn_moves_to_the_end_after_tool_turns(self):
          """The placeholder was created before the tool turns existed. It
          is moved so the conversation reads in the order it happened."""
          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          _a, conv, assistant, payload, llm = _setup(
              ["stub.safe"], script=[("tool", "stub.safe", {}), ("final", "x")],
          )
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assistant.refresh_from_db()
          tool_turn = conv.turns.get(role=Turn.Role.TOOL)
          assert assistant.index > tool_turn.index

      def test_the_tool_turn_records_all_five_keys_and_the_invocation(self):
          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          _a, conv, _t, payload, llm = _setup(
              ["stub.safe"], script=[("tool", "stub.safe", {}), ("final", "x")],
          )
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          turn = conv.turns.get(role=Turn.Role.TOOL)
          assert set(turn.tool_call) == {"tool", "args", "agent", "id", "discarded"}
          assert turn.tool_call["tool"] == "stub.safe"
          assert turn.tool_call["agent"] == "general"
          assert turn.tool_call["id"] == ""          # the engine supplies none
          assert turn.invocation_id is not None
          assert turn.invocation.outcome == ToolInvocation.Outcome.OK


  class TestTheToolListIsFiltered:
      def test_an_unregistered_granted_key_never_reaches_the_prompt(self):
          _a, _c, _t, payload, llm = _setup(["not.registered"])
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assert llm.calls[0][1] is None

      def test_a_granted_registered_tool_is_offered_under_its_WIRE_name(self):
          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          _a, _c, _t, payload, llm = _setup(["stub.safe"])
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          names = [t["function"]["name"] for t in llm.calls[0][1]]
          assert names == ["stub__safe"]


  class TestParallelCallsArePolicy:
      def test_three_calls_in_one_response_run_one_and_record_two_discarded(self):
          """Plain `Ollama.chat` does NOT cap tool calls -- `base.py:400-445`
          passes them through uncapped, and `force_single_tool_call`
          (`base.py:63`) is only reachable through `chat_with_tools`, which
          this design does not use. Taking `calls[0]` is THIS PLATFORM'S
          policy, so it must be pinned here or it is not a rule at all."""
          for key in ("stub.a", "stub.b", "stub.c"):
              register_tool(ToolSpec(key=key, label="S", description="d",
                                     runner="agents.runtime.tests._helpers.runner_ok"))
          _a, conv, _t, payload, llm = _setup(
              ["stub.a", "stub.b", "stub.c"],
              script=[
                  [("tool", "stub.a", {}), ("tool", "stub.b", {}), ("tool", "stub.c", {})],
                  ("final", "x"),
              ],
          )
          with patch_llm(llm):
              result = run_turn(payload, [], make_job_ctx())
          turn = conv.turns.get(role=Turn.Role.TOOL)
          assert turn.tool_call["tool"] == "stub.a"
          assert [d["tool"] for d in turn.tool_call["discarded"]] == ["stub.b", "stub.c"]
          assert ToolInvocation.objects.count() == 1      # exactly one tool actually ran
          assert result["steps_used"] == 2                # one tool step, one final step

      def test_the_tool_message_says_which_call_was_executed(self):
          """The discard is recorded, never silent -- in the row AND in
          what the model is told, so its next step is reasoning about what
          really happened."""
          for key in ("stub.a", "stub.b"):
              register_tool(ToolSpec(key=key, label="S", description="d",
                                     runner="agents.runtime.tests._helpers.runner_ok"))
          _a, conv, _t, payload, llm = _setup(
              ["stub.a", "stub.b"],
              script=[[("tool", "stub.a", {}), ("tool", "stub.b", {})], ("final", "x")],
          )
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assert "stub.b" in conv.turns.get(role=Turn.Role.TOOL).text


  class TestHonestEndings:
      def test_budget_exhaustion_produces_an_honest_final_and_spends_max_steps(self):
          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          _a, _c, assistant, payload, llm = _setup(
              ["stub.safe"], max_steps=3,
              script=[("tool", "stub.safe", {})] * 5,
          )
          with patch_llm(llm):
              result = run_turn(payload, [], make_job_ctx())
          assistant.refresh_from_db()
          assert result["steps_used"] == 3
          assert "ran out of steps" in assistant.text
          assert assistant.state == Turn.State.DONE     # honest, not failed

      def test_an_exhausted_turn_still_carries_the_last_tool_result(self):
          """Never a fabricated answer, and never a silent truncation: the
          user gets the honest sentence AND whatever was actually found."""
          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          _a, _c, assistant, payload, llm = _setup(
              ["stub.safe"], max_steps=2, script=[("tool", "stub.safe", {})] * 3,
          )
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assistant.refresh_from_db()
          assert "it worked" in assistant.text

      def test_an_expired_deadline_ends_the_turn_before_the_first_call(self, monkeypatch):
          _a, _c, assistant, payload, llm = _setup()
          monkeypatch.setattr(
              "agents.runtime.loop.TURN_DEADLINE_SECONDS", -1.0,
          )
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assistant.refresh_from_db()
          assert "time limit" in assistant.text
          assert llm.calls == []

      def test_a_bound_model_that_cannot_call_tools_says_so_and_calls_nothing(self, monkeypatch):
          """Section 10.1's row, reached from inside the loop rather than a
          preflight. NO prompt-hacking: never a hand-rolled tool syntax in
          the system prompt, never a "please respond in JSON" nudge, never
          parsing a tool call out of prose (`models/contracts/engines/
          base.py:450-458` says all three outright)."""
          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          _a, _c, assistant, payload, llm = _setup(["stub.safe"])
          monkeypatch.setattr("agents.runtime.loop._supports_tool_calling", lambda r: False)
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assistant.refresh_from_db()
          assert "cannot call tools" in assistant.text
          assert llm.calls == []

      def test_an_unreported_tool_capability_attempts_the_turn(self, monkeypatch):
          """`None` means the engine does not report the fact at all
          (`base.py:450-458`). The turn RUNS; a model that emits no tool
          call is honestly indistinguishable from one that chose not to."""
          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          _a, _c, _t, payload, llm = _setup(["stub.safe"])
          monkeypatch.setattr("agents.runtime.loop._supports_tool_calling", lambda r: None)
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assert len(llm.calls) == 1


  class TestFailureAndRecovery:
      def test_a_failing_tool_produces_a_tool_turn_and_exactly_one_recovery(self):
          register_tool(ToolSpec(key="stub.bad", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_value_error"))
          _a, conv, assistant, payload, llm = _setup(
              ["stub.bad"], script=[("tool", "stub.bad", {}), ("final", "recovered")],
          )
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assistant.refresh_from_db()
          assert assistant.text == "recovered"
          assert conv.turns.filter(role=Turn.Role.TOOL).count() == 1

      def test_a_second_failure_ends_the_turn_naming_both(self):
          register_tool(ToolSpec(key="stub.bad", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_value_error"))
          _a, _c, assistant, payload, llm = _setup(
              ["stub.bad"], script=[("tool", "stub.bad", {}), ("tool", "stub.bad", {})],
          )
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assistant.refresh_from_db()
          assert assistant.text.count("stub.bad") >= 2

      def test_a_refused_tool_is_dropped_from_the_rest_of_the_turn(self):
          """"No retry" made literal: the model is not offered a tool it
          will be refused for using -- enforcement by omission, the same
          mechanism section 6.4 uses for the depth cap."""
          register_tool(ToolSpec(key="stub.refuse", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_refused_subclass"))
          _a, _c, _t, payload, llm = _setup(
              ["stub.refuse"], script=[("tool", "stub.refuse", {}), ("final", "ok")],
          )
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assert llm.calls[0][1] is not None            # offered on the first call
          assert llm.calls[1][1] is None                # gone on the second

      def test_an_unknown_wire_name_is_a_tool_error_not_a_crash(self):
          _a, conv, assistant, payload, llm = _setup(
              script=[("tool", "no.such.tool", {}), ("final", "recovered")],
          )
          with patch_llm(llm):
              run_turn(payload, [], make_job_ctx())
          assistant.refresh_from_db()
          assert assistant.text == "recovered"
          assert conv.turns.get(role=Turn.Role.TOOL).invocation.tool_key == "no.such.tool"

      def test_every_failed_call_still_spends_a_step(self):
          """A failing loop cannot outrun the budget."""
          register_tool(ToolSpec(key="stub.bad", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_value_error"))
          _a, _c, _t, payload, llm = _setup(
              ["stub.bad"], max_steps=4, script=[("tool", "stub.bad", {})] * 6,
          )
          with patch_llm(llm):
              result = run_turn(payload, [], make_job_ctx())
          assert result["steps_used"] <= 4


  class TestTheHandlerWritesItsOwnFailure:
      def test_a_raising_loop_marks_the_turn_failed_and_re_raises(self, monkeypatch):
          """Section 6.2 step 8. `on_terminal` does NOT fire when the
          handler ran and raised -- `Worker._execute` gates it on a
          `handler_started` flag (`models/queue/worker.py:618,623,670`)
          whose docstring says so outright. So the handler must write its
          own failure or nothing ever does."""
          _a, _c, assistant, payload, llm = _setup()
          monkeypatch.setattr(
              "agents.runtime.loop.build_messages",
              lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
          )
          with patch_llm(llm), pytest.raises(RuntimeError):
              run_turn(payload, [], make_job_ctx())
          assistant.refresh_from_db()
          assert assistant.state == Turn.State.FAILED
          assert assistant.error == "boom"
          assert "Traceback" not in assistant.error


  class TestProgress:
      def test_progress_is_reported_each_iteration_with_a_named_unit(self):
          """`"items"` is one of the three units `JobContext.
          report_progress` names (`models/contracts/jobkinds.py:123-136`),
          and the WORKER throttles the write
          (`models/queue/worker.py:125`), so calling it every iteration is
          free."""
          reports = []
          ctx = make_job_ctx(_report=reports.append)
          _a, _c, _t, payload, llm = _setup(script=[("final", "x")])
          with patch_llm(llm):
              run_turn(payload, [], ctx)
          assert reports and reports[0]["unit"] == "items"
          assert reports[0]["total"] == 8
  ```

- [ ] Create `agents/runtime/bindings.py` — the shared resolver both the planner and the loop call:

  ```python
  """Which model answers this turn.

  ONE function, called by BOTH `agents.runtime.jobs.plan_turn` (at enqueue
  time) and `agents.runtime.loop.run_turn` (at run time). They ask the
  same question and must not be able to answer it differently.

  This deliberately does NOT copy `tools/rag/jobs.py`'s two-pass shape.
  That module keeps `plan_ask` and `_precheck` apart because its run-time
  pass genuinely does something extra -- it HEALTH-CHECKS each endpoint
  and folds failures into a user-facing message (`jobs.py:151-209`). P2's
  loop does no health check, so the two calls here are the same call.
  `tools.rag.jobs._resolve_answer` (`jobs.py:109-130`) is the shape this
  mirrors: a single helper both passes use.

  `models.registry.bindings` is the ONE module of that package an
  `agents/*` app may import (import-law rule 2), and this is the only
  place in the column that imports it.
  """
  from __future__ import annotations

  from models.contracts.bindings import resolve
  from models.registry.bindings import resolve_connection_named


  def resolve_chat(agent, connection) -> tuple:
      """`(resolved, display_name)` for `agent`'s chat model.

      The picked connection when `connection` is a non-blank pk, else the
      agent's own `llm_role`. NEVER `get_llm()`-by-default: the picker
      override is explicit.

      `resolve_connection_named` returns a `tuple[ResolvedModel, str]`
      (`models/registry/bindings.py:317`), not a bare `ResolvedModel`. The
      name is what the assistant turn's "answered by" line records, and
      it is `""` on the role path -- the role path's own label is only
      meaningful once the turn has actually been answered, exactly as
      `tools.rag.jobs._resolve_answer` documents.

      Raises `ValueError` for an unbound role or an unknown/non-chat pk.
      Both callers WANT that: the command preflights it before enqueuing,
      and the planner treats it as the one non-tolerant resolution.
      """
      if connection not in (None, ""):
          return resolve_connection_named(int(connection), "chat")
      return resolve(agent.llm_role), ""
  ```

- [ ] Run and read the failure, then create `agents/runtime/loop.py`:

  ```python
  """One agent turn, bounded, inline (spec section 6.2).

  THE TURN IS THE JOB. Tools run INLINE inside it -- a tool may enqueue a
  queue job and return its id, and it must never wait on one. On a default
  install `JobSettings.memory_budget_bytes` is null, which
  `models/queue/scheduler.py:375` reads as sequential mode -- at most one
  job on the whole machine -- so a turn that blocked on a job it enqueued
  would hold the machine's one slot while the job it waits for can never
  be admitted, and the orphan sweep would then fail it permanently.
  Deadlock, then data loss. Certain, not probable.

  POST-DEADLINE LATENCY IS REAL AND IS NOT FIXED HERE. `budget.expired` is
  checked at the top of every iteration and before every tool call, NEVER
  during one. A tool that enters a long wait just under the deadline can
  return well after it -- `tools.vision.services.wait_for` polls the image
  engine for up to its own timeout, which is longer than a turn's
  remaining time can be. The turn then ends immediately with the honest
  deadline sentence PLUS that tool's actual result. Killing a tool
  mid-flight would strand a submitted generation and throw away work that
  really was done; ending late and saying so is the better failure.

  EVERY ENDING IS HONEST. A turn ends by producing an answer, by running
  out of steps, by running out of time, by failing twice, or by being
  bound to a model that cannot call tools. In every case the assistant
  turn says which, and carries whatever the last tool actually returned.
  It never fabricates an answer and never silently truncates.

  NO PROMPT-HACKING, EVER. When the bound engine reports it cannot call
  tools, this loop says so and stops. It does not inject a hand-rolled
  tool syntax into the system prompt, does not nudge with "please respond
  in JSON", and does not parse a tool call out of prose --
  `models/contracts/engines/base.py:450-458` forbids all three by name.

  THIS MODULE IMPORTS NO `tools.*` MODULE. Every runner is reached through
  `agents.runtime.invoke.invoke_tool`, which resolves a dotted-path string
  (import-law rule 3).
  """
  from __future__ import annotations

  import logging
  import time

  from agents.contracts.tools import Principal, StepBudget, ToolContext, granted_tools
  from agents.contracts.toolschema import key_from_wire_name, openai_tool_dict
  from agents.limits import MAX_STEPS_DEFAULT, TURN_DEADLINE_SECONDS
  from agents.models import Turn
  from agents.runtime.bindings import resolve_chat
  from agents.runtime.invoke import invoke_tool, invoke_unknown_tool
  from agents.runtime.prompt import build_messages, tool_turn_messages
  from models.contracts import gateway
  from models.contracts.bindings import resolve
  from models.contracts.jobkinds import JobContext

  logger = logging.getLogger(__name__)

  _OUT_OF_STEPS = (
      "I ran out of steps for this turn before reaching an answer. Here is what the "
      "last tool returned."
  )
  _OUT_OF_TIME = (
      "This turn hit its time limit before I reached an answer. Here is what the last "
      "tool returned."
  )
  _NO_TOOL_CALLING = (
      "The model currently assigned to this agent's role reports that it cannot call "
      "tools, so I cannot use the tools this agent was granted. Assign a tool-capable "
      "model to that role, or use an agent that needs no tools."
  )


  def run_turn(payload: dict, models: list, ctx: JobContext) -> dict:
      """Run one agent turn to completion, inline.

      Wraps the whole body in its own try/except and writes the failure
      ITSELF before re-raising. That is required, not defensive:
      `on_terminal` does NOT fire when the handler ran and raised.
      `Worker._execute` gates the hook on a `handler_started` flag
      (`models/queue/worker.py:618,623,670`) whose docstring says so
      outright, and `JobKind.on_terminal`'s own contract restricts it to a
      job that reaches a terminal state WITHOUT ever running its handler
      (`models/contracts/jobkinds.py:207-215`). `on_turn_terminal`
      (`agents/runtime/jobs.py`) covers the three paths where the handler
      never started; this `except` covers the one where it did.

      `str(exc)`, never a traceback: this text is shown to a person.
      """
      try:
          return _run_turn(payload, models, ctx)
      except Exception as exc:
          Turn.objects.filter(pk=payload["turn"]).update(
              state=Turn.State.FAILED, error=str(exc),
          )
          raise


  def _run_turn(payload: dict, models: list, ctx: JobContext) -> dict:
      turn = Turn.objects.select_related("conversation__agent").get(pk=payload["turn"])
      conversation = turn.conversation
      agent = conversation.agent

      Turn.objects.filter(pk=turn.pk).update(
          state=Turn.State.RUNNING, queue_job_id=ctx.job_id,
      )

      budget = StepBudget(
          steps=agent.max_steps or MAX_STEPS_DEFAULT,
          deadline_monotonic=time.monotonic() + TURN_DEADLINE_SECONDS,
      )
      principal = Principal(
          kind="resident_agent" if agent.resident else "user_agent", key=agent.slug,
      )

      available = _available_tools(principal, agent)
      resolved, answered_by = resolve_chat(agent, payload.get("connection"))

      # Section 10.1's "bound model cannot call tools" row, reached from
      # inside the loop because P2 has no view. `False` means the engine
      # REPORTED the fact; `None` means it does not report it at all, and
      # the turn runs (`models/contracts/engines/base.py:450-458`).
      if available and _supports_tool_calling(resolved) is False:
          return _finish(turn, conversation, _NO_TOOL_CALLING, [], 0, answered_by)

      # `gateway.get_llm_for(...)`, never a `from ... import get_llm_for`
      # bound at import time: the name is looked up on the MODULE at call
      # time, which is what lets a test patch
      # `models.contracts.gateway.get_llm_for` and have this call see it.
      # A hoisted `from`-import copies the function object into this
      # module's namespace and the patch never reaches it.
      llm = gateway.get_llm_for(resolved)
      messages = build_messages(agent, conversation, before_index=turn.index)

      artifacts: list[str] = []
      tool_calls: list[dict] = []
      failures: list[str] = []
      last_tool_text = ""
      steps_used = 0
      final_text = ""

      while True:
          if budget.expired:
              final_text = _honest_ending(_OUT_OF_TIME, last_tool_text)
              break
          if budget.exhausted:
              final_text = _honest_ending(_OUT_OF_STEPS, last_tool_text)
              break

          ctx.report_progress(
              steps_used, total=agent.max_steps, unit="items", label="thinking",
          )
          tool_dicts = [openai_tool_dict(spec) for spec in available.values()] or None
          response = llm.chat(messages, tools=tool_dicts)
          calls = llm.get_tool_calls_from_response(response, error_on_no_tool_call=False)
          budget.spend(1)
          steps_used += 1

          if not calls:
              final_text = _response_text(response)
              break

          # TAKE calls[0], DISCARD THE REST. This is this platform's
          # policy, not the library's behaviour: plain `Ollama.chat`
          # (`base.py:400-445`) passes the model's tool calls through
          # uncapped -- `force_single_tool_call` (`base.py:63`) is only
          # reached through `chat_with_tools`, which this design does not
          # use. A step is the unit the budget is denominated in, and
          # running N tools per step makes the budget mean N times less;
          # and the recovery policy is defined per failing call.
          chosen, discarded = calls[0], calls[1:]
          key = key_from_wire_name(chosen.tool_name)
          spec = available.get(key)

          tool_ctx = ToolContext(
              conversation_id=str(conversation.id),
              principal=principal,
              depth=turn.depth,
              budget=budget,
              job=ctx,
          )
          raw_args = dict(chosen.tool_kwargs or {})
          if spec is None:
              outcome = invoke_unknown_tool(chosen.tool_name, raw_args, tool_ctx)
          else:
              outcome = invoke_tool(spec, raw_args, tool_ctx)

          tool_text = _tool_message_text(outcome.text, discarded, key)
          last_tool_text = outcome.text
          # `"id": ""` ALWAYS on this engine: `Ollama.chat` builds its
          # ToolCallBlocks with no tool_call_id, and `ToolSelection.
          # tool_id` is the tool NAME, not an id (`base.py:390-394`).
          # Recording that would put a name in an id field.
          call_record = {
              # From the outcome, never from `chosen.tool_kwargs`: the
              # VALIDATED args on an ok/refused/error/degraded outcome,
              # and the RAW dict on a param_error (validation is exactly
              # what did not happen there, so there is no validated form).
              # `Turn.tool_call["args"]` is replayed into a later prompt,
              # so it must be what actually ran.
              "tool": key,
              "args": outcome.args,
              "agent": agent.slug,
              "id": "",
              "discarded": [
                  {"tool": key_from_wire_name(d.tool_name), "args": dict(d.tool_kwargs or {})}
                  for d in discarded
              ],
          }
          tool_turn = Turn.objects.create(
              conversation=conversation,
              index=Turn.next_index(conversation),
              role=Turn.Role.TOOL,
              text=tool_text,
              tool_call=call_record,
              data=(outcome.result.data if outcome.result is not None else None),
              artifacts=list(outcome.result.artifacts) if outcome.result is not None else [],
              depth=turn.depth,
              state=Turn.State.DONE,
              invocation_id=outcome.invocation_id,
              queue_job_id=ctx.job_id,
          )
          tool_calls.append(call_record)
          if outcome.result is not None:
              artifacts.extend(outcome.result.artifacts)
          messages.extend(tool_turn_messages(tool_turn))

          if outcome.failed:
              failures.append(f"{key}: {outcome.text}")
              if outcome.bars_retry:
                  # Enforcement by omission: the model is never offered a
                  # tool it will be refused for using (section 6.4's own
                  # mechanism for the depth cap).
                  available.pop(key, None)
              if budget.recoveries_left <= 0:
                  final_text = _two_failures_text(failures)
                  break
              budget.recoveries_left -= 1

      return _finish(turn, conversation, final_text, artifacts, steps_used, answered_by,
                     tool_calls=tool_calls)


  def _available_tools(principal: Principal, agent) -> dict:
      """The granted, registered, non-mutating, role-resolvable tools, in
      the agent row's own order.

      The SAME filter `plan_turn` applies (`agents/runtime/jobs.py`), so
      `get_tool` is only ever called on a key that is present and every
      tool the model is offered had its roles declared to the queue at
      enqueue time.
      """
      from agents.contracts.tools import get_tool

      out = {}
      for key in granted_tools(principal, agent.tool_keys):
          spec = get_tool(key)
          if _roles_resolve(spec):
              out[key] = spec
      return out


  def _roles_resolve(spec) -> bool:
      for role in spec.roles:
          try:
              resolve(role)
          except Exception:  # noqa: BLE001 -- an unresolvable role is a DROP, not a fault
              logger.info(
                  "agents: tool %r needs role %r, which does not resolve here; dropped "
                  "from this turn's tool list.", spec.key, role,
              )
              return False
      return True


  def _supports_tool_calling(resolved) -> bool | None:
      """Three-valued, and each value means something different
      (`models/contracts/engines/base.py:439-460`).

      Reached through `getattr`, so an adapter predating the method
      degrades to `None` rather than `AttributeError`. Any failure asking
      is also `None`: "we could not find out" and "the engine does not
      report it" lead to the same honest behaviour -- attempt the turn.
      """
      from models.contracts.engines import get_engine

      try:
          engine = get_engine(resolved.engine)
      except Exception:  # noqa: BLE001 -- an unknown engine is not a reason to refuse
          return None
      probe = getattr(engine, "supports_tool_calling", None)
      if probe is None:
          return None
      try:
          return probe(resolved.model_id, resolved.endpoint)
      except Exception:  # noqa: BLE001 -- a probe failure is "unknown", never "no"
          logger.info("agents: could not ask %r whether it can call tools", resolved.engine)
          return None


  def _tool_message_text(text: str, discarded, executed_key: str) -> str:
      """What the model is told a tool returned.

      When calls were discarded, the message SAYS which one ran. The
      discard is recorded, never silent: a model reasoning about three
      calls it thinks it made would be reasoning about a turn that did not
      happen.
      """
      if not discarded:
          return text
      names = ", ".join(key_from_wire_name(d.tool_name) for d in discarded)
      return (
          f"{text}\n\n(Only {executed_key} was run this step. These were not run: "
          f"{names}. Call one of them on your next step if you still need it.)"
      )


  def _honest_ending(sentence: str, last_tool_text: str) -> str:
      return f"{sentence}\n\n{last_tool_text}".rstrip()


  def _two_failures_text(failures: list[str]) -> str:
      joined = "\n".join(f"- {f}" for f in failures)
      return (
          "Two tool calls failed in this turn, so I stopped rather than guess at an "
          f"answer:\n{joined}"
      )


  def _response_text(response) -> str:
      """The assistant text out of a `ChatResponse`. `str(response.message)`
      would include the role prefix llama-index renders; `.content` is the
      concatenated text blocks."""
      return response.message.content or ""


  def _finish(turn, conversation, text, artifacts, steps_used, answered_by,
              tool_calls=None) -> dict:
      """Move the assistant turn to the end, write it, and return.

      The placeholder was created before this turn's tool turns existed,
      so it currently sorts BEFORE them. One UPDATE moves it. This leaves
      a gap at its original index, which is deliberate: `ordering =
      ["index"]` and `uniq_turn_index` are both indifferent to gaps, and
      reserving indices up front would mean guessing how many tools a turn
      would call.
      """
      if Turn.objects.filter(conversation=conversation, index__gt=turn.index).exists():
          turn.index = Turn.next_index(conversation)
      turn.text = text
      turn.artifacts = list(artifacts)
      turn.state = Turn.State.DONE
      turn.save(update_fields=["index", "text", "artifacts", "state"])
      return {
          "turn_id": turn.pk,
          "text": text,
          "artifacts": list(artifacts),
          "tool_calls": tool_calls or [],
          "steps_used": steps_used,
          "answered_by": answered_by,
          "summary": f"{conversation.agent.slug}: {steps_used} step(s)",
      }
  ```

- [ ] Run green. Then run the loop tests **in isolation and again as part of the whole suite** — a module-global tool registry that leaks between tests shows up here first:

  ```bash
  .venv/bin/pytest -q agents/runtime/tests/test_loop.py
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  ```

- [ ] Commit:

  ```bash
  git add agents/runtime
  git commit -m "$(cat <<'EOF'
  feat(agents): the bounded ReAct loop

  Spec section 6.2 end to end. Every way a turn can end is honest: an
  answer, out of steps, out of time, two failures, or a bound model that
  reports it cannot call tools. Each ending says which, and carries
  whatever the last tool actually returned -- never a fabricated answer,
  never a silent truncation.

  calls[0] is taken and the rest are DISCARDED, and that is this
  platform's policy rather than the library's behaviour: plain
  Ollama.chat passes tool calls through uncapped, and
  force_single_tool_call is only reachable through chat_with_tools, which
  this design does not use. So the policy is pinned by a test that scripts
  three calls in one response. The discard is recorded in the tool turn's
  tool_call["discarded"] AND in what the model is told -- a model
  reasoning about three calls it thinks it made is reasoning about a turn
  that did not happen.

  tool_call["id"] is always "" on this engine, and never filled from
  ToolSelection.tool_id, which is the tool NAME.

  A refused tool is dropped from the rest of the turn: enforcement by
  omission, so "no retry" is literal rather than hoped for. Every failed
  call still spends a step, so a failing loop cannot outrun the budget.

  run_turn writes its own Turn(FAILED) before re-raising, because
  on_terminal does NOT fire when a handler ran and raised --
  Worker._execute gates it on handler_started and says so in its own
  docstring.

  No prompt-hacking on the cannot-call-tools path: no injected syntax, no
  JSON nudge, no parsing a call out of prose. base.py:450-458 forbids all
  three by name, and None (not reported) still attempts the turn.

  Post-deadline latency is documented, not fixed: expiry is checked
  before a tool call, never during one, so a long generate can return
  late. Ending late and saying so beats stranding a submitted job.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 9: `agents/runtime/jobs.py` and the `agent.turn` job kind

The planner, the summarizer, the terminal hook, and the registration that ties them to the queue.

#### `default_priority=100`, and what that buys

`JobSettings.DEFAULT_PRIORITY_DEFAULT = 100` (`models/queue/models.py:181`) and lower runs first (`models/queue/models.py:47-48`). So a chat turn at 100 sits **ahead of** `rag.ingest` and `vision.generate` (both 200, `tools/rag/apps.py:99`, `tools/vision/apps.py:61`) and **level with** `rag.ask` (which declares `None` and therefore takes the global default). A person waiting on a conversation should not queue behind a batch ingest.

#### `on_terminal` is required here, not optional

A turn has a durable side-effect that **predates the job**: the placeholder assistant `Turn` row the enqueuing caller wrote. That is exactly the stranded-row condition `JobKind.on_terminal` exists for (`models/contracts/jobkinds.py:195-231`) and that `rag.ingest` already uses for its `Document` (`tools/rag/apps.py:100`, `tools/rag/jobs.py:503`). Without the hook, a turn cancelled from `/queue/` or failed by a second orphaning stays `queued` forever.

**It covers exactly the three paths where the handler never started** — cancelled while queued, permanently failed by a second orphaning, and a handler that could not be resolved. It does **not** fire when the handler ran and raised; `Worker._execute` gates it on a `handler_started` flag (`models/queue/worker.py:618,623,670`) and that gate is why `run_turn` writes its own failure (Task 8). A test proves the hook does **not** fire on that path — a hook that silently also ran there would mean two writers racing for one row's final state.

#### `exclusive=True`, honestly declared

A turn may load a chat model, then an embedding model, then an image checkpoint, sequentially, inside one job. Declaring that is better than being folded into it by `effectively_exclusive`'s unmeasured-footprint branch, which is where an undeclared turn would land anyway. `Worker._evict_to_match_plan` then evicts to match the plan before the turn launches, uncapped for an exclusive job.

**Files**
- Create: `agents/runtime/jobs.py`
- Create: `agents/runtime/tests/test_jobs.py`
- Modify: `agents/apps.py` — `ready()` registers the job kind

**Interfaces**
- Consumes: `models.contracts.jobkinds.ModelRef` (`jobkinds.py:40-56`), `JobKind`, `register_job_kind` (`jobkinds.py:243`); `models.contracts.bindings.resolve` (`bindings.py:130`); `agents.runtime.bindings.resolve_chat` (Task 8 — the same function `run_turn` calls); `agents.contracts.tools.Principal`/`granted_tools`/`get_tool`; `agents.models.Agent`/`Turn`; `agents.limits.MAX_AGENT_DEPTH`; `agents.resident.AGENT_TOOL_PREFIX` (Task 11 — a bare string constant in a pure module, so a module-scope import costs nothing and removes a duplicated literal).

  **Ordering note:** `AGENT_TOOL_PREFIX` lands in Task 11. Until then `jobs.py` may carry the literal `"agent."` with a `# replaced by the shared constant in Task 11` comment, or Tasks 9 and 11 may be executed adjacently. Do not leave two definitions of the string behind.
- Produces:
  - `agents.runtime.jobs.plan_turn(payload: dict) -> tuple[list[ModelRef], bool]`
  - `agents.runtime.jobs.summarize_turn(payload: dict) -> str` — **touches no database.**
  - `agents.runtime.jobs.on_turn_terminal(payload: dict, state: str) -> None`
  - `AgentsConfig.ready()` registers `JobKind(key="agent.turn", planner="agents.runtime.jobs.plan_turn", handler="agents.runtime.loop.run_turn", summarizer="agents.runtime.jobs.summarize_turn", default_priority=100, on_terminal="agents.runtime.jobs.on_turn_terminal")`.
- **Payload shape** (JSON-safe, no ORM objects, no filesystem paths — ADR 0014:208-211's rule):

  ```python
  {"conversation": "<uuid str>", "turn": <int Turn pk>, "agent": "<Agent.slug>",
   "text": "<the user's message>", "connection": "<ModelConnection pk as str>" | None,
   "mode": "chat"}
  ```

  `"connection"` is a pk **as a string**, matching both existing precedents (`tools/rag/jobs.py:9-10`, `tools/vision/jobs.py:11-24`). `"mode"` has exactly one legal value in P2; P3 adds `"flow"` (and, when it means something, a `"flow"` key) without a payload migration — deviation D7.

  **`"agent"` is for the SUMMARIZER only.** Both `plan_turn` and `run_turn` derive the agent from `turn.conversation.agent` — the turn pk is in the payload, and the row is the authority on which agent owns the conversation. A planner that trusted the payload's slug could plan for one agent while the loop ran another, which is precisely the disagreement an admission snapshot exists to prevent. `summarize_turn` reads the slug because it must not touch the database at all (`/queue/` calls it per row), and a stale label in a listing is a cosmetic problem where a stale plan is a correctness one.

**Steps**

- [ ] Failing test first. Create `agents/runtime/tests/test_jobs.py`:

  ```python
  """The planner declares what a turn may load; the hook cleans up after a
  turn that never ran; the summarizer never touches the database.
  """
  from __future__ import annotations

  import pytest

  from agents.contracts.tools import ToolSpec, register_tool
  from agents.models import Turn
  from agents.runtime.jobs import on_turn_terminal, plan_turn, summarize_turn
  from agents.runtime.tests._helpers import (  # noqa: F401 -- the two fixture
      bound_chat_role, bound_embed_role,       # imports ARE their registration
      make_agent, make_conversation, make_turn, restore_tools, snapshot_tools,
  )
  from models.contracts.roles import CHAT_CONVERSE_ROLE

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  def _turn_for(agent):
      """An ASSISTANT placeholder on a conversation THIS agent owns.

      `plan_turn` reads `turn.conversation.agent`, not `payload["agent"]`,
      so a test that built the agent and the turn independently would
      either plan for the wrong agent or trip `uniq_agent_slug_ci` on the
      second `make_agent()` `make_conversation()` creates for itself.
      """
      return make_turn(
          conversation=make_conversation(agent=agent),
          role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
      )


  def _payload(turn, **overrides):
      """`"agent"` carries the slug because the real payload does -- but
      it is the SUMMARIZER's only. `plan_turn` and `run_turn` both derive
      the agent from `turn.conversation.agent`, so a payload whose slug
      disagreed with the row would change nothing about what is planned.
      """
      fields = dict(conversation=str(turn.conversation_id), turn=turn.pk,
                    agent=turn.conversation.agent.slug, text="hi",
                    connection=None, mode="chat")
      fields.update(overrides)
      return fields


  class TestPlanTurn:
      def test_it_declares_the_agents_own_chat_role(self, bound_chat_role):
          agent = make_agent()
          turn = _turn_for(agent)
          refs, exclusive = plan_turn(_payload(turn))
          assert [r.role for r in refs] == [CHAT_CONVERSE_ROLE]
          assert exclusive is True

      def test_it_unions_the_roles_of_every_granted_tool(self, bound_chat_role, bound_embed_role):
          register_tool(ToolSpec(key="stub.embeds", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok",
                                 roles=("rag.embed",)))
          agent = make_agent(tool_keys=["stub.embeds"])
          turn = _turn_for(agent)
          refs, _ = plan_turn(_payload(turn))
          assert {r.role for r in refs} == {CHAT_CONVERSE_ROLE, "rag.embed"}

      def test_a_role_is_declared_once_even_when_two_tools_need_it(self, bound_chat_role,
                                                                  bound_embed_role):
          for key in ("stub.a", "stub.b"):
              register_tool(ToolSpec(key=key, label="S", description="d",
                                     runner="agents.runtime.tests._helpers.runner_ok",
                                     roles=("rag.embed",)))
          agent = make_agent(tool_keys=["stub.a", "stub.b"])
          turn = _turn_for(agent)
          refs, _ = plan_turn(_payload(turn))
          assert len(refs) == 2

      def test_a_tool_whose_role_will_not_resolve_is_DROPPED_not_raised(self, bound_chat_role):
          """The same tolerant shape `plan_ingest` uses for its extract
          role (`tools/rag/jobs.py:408-424`), and the same filter the loop
          applies -- so a tool the planner dropped is a tool the model was
          never offered."""
          register_tool(ToolSpec(key="stub.unbound", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok",
                                 roles=("nothing.bound.here",)))
          agent = make_agent(tool_keys=["stub.unbound"])
          turn = _turn_for(agent)
          refs, _ = plan_turn(_payload(turn))
          assert [r.role for r in refs] == [CHAT_CONVERSE_ROLE]

      def test_an_unresolvable_CHAT_role_is_a_hard_failure(self):
          """Not tolerant, deliberately: the enqueuing caller preflights
          this and refuses before the planner ever runs. Reaching here
          with no chat model means the preflight was skipped, and a turn
          that queued anyway would fail later and less legibly."""
          agent = make_agent(llm_role="nothing.bound.here")
          turn = _turn_for(agent)
          with pytest.raises(ValueError):
              plan_turn(_payload(turn))

      def test_footprints_are_left_unresolved(self, bound_chat_role):
          """`models/queue/scheduler.py`'s provenance contract: a planner
          never resolves footprints; claim-time code fills them in from a
          FRESH lookup, never from this snapshot."""
          agent = make_agent()
          turn = _turn_for(agent)
          refs, _ = plan_turn(_payload(turn))
          assert all(r.footprint_bytes is None for r in refs)

      def test_a_delegate_agents_tool_roles_are_declared_too(self, bound_chat_role,
                                                             bound_embed_role):
          """Agent-as-tool declares empty `roles` -- a delegate's roles are
          not knowable at registration time, so the PLANNER supplies them
          by walking the closure (spec section 6.1)."""
          register_tool(ToolSpec(key="stub.embeds", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok",
                                 roles=("rag.embed",)))
          make_agent(slug="library", tool_keys=["stub.embeds"])
          register_tool(ToolSpec(key="agent.library", label="Ask the library agent",
                                 description="d",
                                 runner="agents.runtime.delegate.run_agent_tool"))
          root = make_agent(slug="general", tool_keys=["agent.library"])
          refs, _ = plan_turn(_payload(_turn_for(root)))
          assert "rag.embed" in {r.role for r in refs}

      def test_a_delegation_cycle_terminates(self, bound_chat_role):
          """Deduped by slug and bounded by MAX_AGENT_DEPTH. A cycle that
          hung the PLANNER would hang the enqueue, i.e. the request."""
          for slug in ("a", "b"):
              register_tool(ToolSpec(key=f"agent.{slug}", label="S", description="d",
                                     runner="agents.runtime.delegate.run_agent_tool"))
          make_agent(slug="a", tool_keys=["agent.b"])
          make_agent(slug="b", tool_keys=["agent.a"])
          root = make_agent(slug="a2", tool_keys=["agent.a"])
          refs, _ = plan_turn(_payload(_turn_for(root)))
          assert refs


  class TestOnTurnTerminal:
      def test_a_cancelled_job_flips_a_queued_turn_to_cancelled(self):
          turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED)
          on_turn_terminal({"turn": turn.pk}, "cancelled")
          turn.refresh_from_db()
          assert turn.state == Turn.State.CANCELLED
          assert turn.error

      def test_a_failed_job_flips_a_running_turn_to_failed(self):
          turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.RUNNING)
          on_turn_terminal({"turn": turn.pk}, "failed")
          turn.refresh_from_db()
          assert turn.state == Turn.State.FAILED

      def test_it_leaves_a_done_turn_alone(self):
          """ONE conditional UPDATE filtered on the states a stranded turn
          can be in -- never a read-then-save. This hook runs AFTER the job
          row's terminal write commits, so a still-alive "stale" worker can
          be writing DONE in the same window; the `state__in` guard makes
          clobbering it impossible."""
          turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.DONE, text="answered")
          on_turn_terminal({"turn": turn.pk}, "failed")
          turn.refresh_from_db()
          assert turn.state == Turn.State.DONE
          assert turn.text == "answered"

      def test_a_missing_turn_is_a_no_op_not_an_exception(self):
          """`invoke_on_terminal` catches and logs
          (`models/contracts/jobkinds.py:291-320`), but a hook that relies
          on its caller's tolerance is a hook that will one day be called
          by something less tolerant."""
          on_turn_terminal({"turn": 999_999}, "cancelled")

      def test_a_payload_with_no_turn_key_is_a_no_op(self):
          on_turn_terminal({}, "failed")

      def test_it_does_not_fire_when_the_handler_ran_and_raised(self):
          """Proven at the seam, not assumed: `Worker._execute` gates the
          hook on `handler_started` (`models/queue/worker.py:618,623,670`),
          whose own docstring says a handler that ran and raised owns its
          failure account. `run_turn` therefore writes Turn(FAILED) itself
          (Task 8), and if this hook ALSO fired there would be two writers
          for one row's final state."""
          import inspect

          from models.queue import worker

          source = inspect.getsource(worker.Worker._execute)
          assert "handler_started" in source
          assert "if error is not None and not handler_started" in source


  class TestSummarizeTurn:
      def test_it_never_touches_the_database(self, django_assert_num_queries):
          """The never-500 philosophy `models/queue/views.py::_summarize`
          already applies to every job kind's summarizer: a listing page
          renders many rows, and a summarizer that queried would turn one
          page render into N."""
          with django_assert_num_queries(0):
              summarize_turn({"agent": "general", "text": "what does the library say?"})

      def test_it_names_the_agent_and_the_question(self):
          out = summarize_turn({"agent": "general", "text": "what does the library say?"})
          assert "general" in out and "library" in out

      def test_it_survives_a_payload_missing_every_key(self):
          assert summarize_turn({})

      def test_a_long_question_is_truncated(self):
          assert len(summarize_turn({"agent": "a", "text": "x" * 500})) < 200


  class TestRegistration:
      def test_agent_turn_is_registered_with_all_five_dotted_paths(self):
          from models.contracts.jobkinds import get_job_kind, resolve_dotted_path

          kind = get_job_kind("agent.turn")
          assert kind.default_priority == 100
          assert kind.on_terminal is not None
          for path in (kind.planner, kind.handler, kind.summarizer, kind.on_terminal):
              assert callable(resolve_dotted_path(path))
  ```

  `bound_chat_role` / `bound_embed_role` are the fixtures Task 6 added to `agents/tests/_helpers.py` and re-exported through `agents/runtime/tests/_helpers.py`. **They must be imported by name in this module or pytest will not see them** — there is no `conftest.py`, so the import IS the registration (Global Constraints). A *test* reaching `models.registry.models` to build the rows is the exemption `foundation/ops/tests/test_import_law.py:19-30` already documents.

  `_turn_for(agent)` exists because `plan_turn` reads `turn.conversation.agent`, never `payload["agent"]`. A test that built the agent and the turn independently would hit one of two failures: `make_turn()` with no conversation calls `make_conversation()`, which calls `make_agent()` for itself — a **second** row at the default slug, so either `uniq_agent_slug_ci` raises or the planner silently plans for an agent the test never configured. Both are the kind of failure that reads like a bug in the code under test. `_payload` keeps the `agent` slug because the real payload carries it, and its docstring says it is the summarizer's only.

- [ ] Run and read the failure, then create `agents/runtime/jobs.py`:

  ```python
  """The `agent.turn` job kind's planner, summarizer, and terminal hook.

  The HANDLER is `agents.runtime.loop.run_turn` and is not re-exported
  here: two functions with one name, one calling the other, is a name
  nobody can grep for.

  Registered by `agents/apps.py::AgentsConfig.ready()`, which imports none
  of this -- every field is a dotted-path STRING, resolved lazily by
  whoever calls it (`models/contracts/jobkinds.py:16-26`).
  """
  from __future__ import annotations

  import logging

  from agents.contracts.tools import Principal, get_tool, granted_tools
  from agents.limits import MAX_AGENT_DEPTH
  from agents.models import Agent, Turn
  from agents.resident import AGENT_TOOL_PREFIX
  from agents.runtime.bindings import resolve_chat
  from models.contracts.bindings import resolve
  from models.contracts.jobkinds import ModelRef

  logger = logging.getLogger(__name__)

  _SUMMARY_MAX = 120


  def plan_turn(payload: dict) -> tuple[list[ModelRef], bool]:
      """The models a turn may load, resolved fresh at ENQUEUE time -- the
      admission snapshot `ModelRef`'s own docstring describes
      (`models/contracts/jobkinds.py:40-52`).

      The agent's own chat role, plus the UNION of the roles of every tool
      it may call, taken over the transitive closure of agent-as-tool
      delegation, bounded by MAX_AGENT_DEPTH and de-duped by agent slug so
      a delegation cycle terminates. An agent-as-tool spec declares EMPTY
      `roles` -- a delegate's roles are not knowable at registration time,
      so this planner supplies them by walking the closure.

      THREE TOLERANT DROPS, producing exactly the same filtered set
      `agents.runtime.loop._available_tools` later builds its schemas
      from: a granted key absent from the registry is dropped
      (`granted_tools`); a `mutates=True` key is dropped (`granted_tools`);
      and a tool whose declared role will not resolve is dropped here. The
      same tolerant shape `plan_ingest` uses for its extract role
      (`tools/rag/jobs.py:408-424`). A tool the planner dropped is a tool
      the model is never offered -- which is what makes ruling R1's
      permissive save safe.

      THE AGENT'S OWN CHAT ROLE IS NOT TOLERANT. An unresolvable chat role
      raises, because the enqueuing caller preflights it and refuses
      before this planner runs; reaching here without one means the
      preflight was skipped, and a turn that queued anyway would fail
      later and less legibly.

      `exclusive=True`: a turn may load a chat model, then an embedding
      model, then an image checkpoint, sequentially, inside one job.
      Declaring that honestly is better than being folded into it by
      `effectively_exclusive`'s unmeasured-footprint branch, which is
      where an undeclared turn would land anyway.

      Footprints are left `None`, per `models/queue/scheduler.py`'s
      provenance contract: claim-time code fills them in from a FRESH
      lookup, never from this snapshot.
      """
      turn = Turn.objects.select_related("conversation__agent").get(pk=payload["turn"])
      agent = turn.conversation.agent
      chat_resolved, chat_name = resolve_chat(agent, payload.get("connection"))
      refs = [_ref(agent.llm_role, chat_resolved, chat_name)]

      seen_roles = {agent.llm_role}
      for role in sorted(_tool_roles(agent)):
          if role in seen_roles:
              continue
          try:
              refs.append(_ref(role, resolve(role)))
              seen_roles.add(role)
          except Exception:  # noqa: BLE001 -- tolerant drop, see docstring
              logger.info("agent.turn: role %r does not resolve; its tool is dropped", role)
      return refs, True


  def _tool_roles(agent) -> set[str]:
      """Every role reachable from `agent`, walking agent-as-tool to
      MAX_AGENT_DEPTH. De-duped by slug, so a cycle terminates."""
      roles: set[str] = set()
      frontier = [(agent, 0)]
      visited = {agent.slug.lower()}
      while frontier:
          current, depth = frontier.pop()
          principal_kind = "resident_agent" if current.resident else "user_agent"
          for key in granted_tools(Principal(kind=principal_kind, key=current.slug),
                                   current.tool_keys):
              spec = get_tool(key)
              roles.update(spec.roles)
              if not key.startswith(AGENT_TOOL_PREFIX) or depth >= MAX_AGENT_DEPTH:
                  continue
              slug = key[len(AGENT_TOOL_PREFIX):]
              if slug.lower() in visited:
                  continue
              visited.add(slug.lower())
              delegate = Agent.objects.filter(slug__iexact=slug, enabled=True).first()
              if delegate is not None:
                  frontier.append((delegate, depth + 1))
      return roles


  def _ref(role: str, resolved, connection_name: str = "") -> ModelRef:
      return ModelRef(
          role=role,
          engine=resolved.engine,
          endpoint=resolved.endpoint,
          model_id=resolved.model_id,
          connection_name=connection_name,
      )


  def summarize_turn(payload: dict) -> str:
      """A one-line row summary for `/queue/`.

      TOUCHES NO DATABASE and raises nothing. `/queue/` renders many rows
      and calls this for each; a summarizer that queried would turn one
      page render into N, and `models/queue/views.py::_summarize` already
      treats a summarizer failure as something to degrade around rather
      than surface. Everything it needs is in the payload, which is why
      the payload carries `agent` and `text` at all.
      """
      agent = str(payload.get("agent") or "an agent")
      text = " ".join(str(payload.get("text") or "").split())
      if len(text) > _SUMMARY_MAX:
          text = text[:_SUMMARY_MAX].rstrip() + "..."
      return f"{agent}: {text}" if text else f"{agent}: a turn"


  def on_turn_terminal(payload: dict, state: str) -> None:
      """Fix up the placeholder assistant turn of a job that reached a
      terminal state WITHOUT its handler ever running.

      THREE paths, all of them handler-never-started: cancelled while
      still queued (`models/queue/backend.py::cancel_job`), permanently
      failed by a SECOND orphaning (`models/queue/claim.py::
      _sweep_orphans`), and a handler that could not be resolved at all
      (`models/queue/worker.py::Worker._execute`'s `handler_started`
      guard). It does NOT fire when the handler ran and raised -- that
      case is `run_turn`'s own `except`, and two writers for one row's
      final state is exactly what the `handler_started` gate exists to
      prevent.

      ONE conditional UPDATE, filtered on the states a stranded turn can
      be in -- never a read-then-save. This runs AFTER the job row's
      terminal write commits (all three call sites schedule it with
      `transaction.on_commit`), so a still-alive "stale" worker -- one the
      orphan sweep gave up on but that had not actually died -- can be
      writing DONE in the window between a read and a write. The
      `state__in` guard makes clobbering that impossible: it only ever
      matches a row still queued or running at the instant of the write
      itself.

      Raises nothing. `invoke_on_terminal` catches and logs
      (`models/contracts/jobkinds.py:291-320`), but a hook that relies on
      its caller's tolerance is a hook that will one day be called by
      something less tolerant.
      """
      turn_id = payload.get("turn")
      if not turn_id:
          return
      Turn.objects.filter(
          pk=turn_id, state__in=(Turn.State.QUEUED, Turn.State.RUNNING),
      ).update(
          state=Turn.State.CANCELLED if state == "cancelled" else Turn.State.FAILED,
          error=(
              "Cancelled from the queue before it ran."
              if state == "cancelled"
              else "The worker running this turn stopped responding; it was not retried again."
          ),
      )
  ```

- [ ] Register the job kind in `AgentsConfig.ready()`, after the role registration:

  ```python
          from models.contracts.jobkinds import JobKind, register_job_kind

          register_job_kind(
              JobKind(
                  # Literals, not imports: this method imports no handler
                  # module by design (see its docstring), which is also
                  # why every path below is a string.
                  key="agent.turn",
                  label="Agent turn",
                  planner="agents.runtime.jobs.plan_turn",
                  handler="agents.runtime.loop.run_turn",
                  summarizer="agents.runtime.jobs.summarize_turn",
                  # The queue-wide default (`models/queue/models.py:181`).
                  # Lower runs first, so a conversation sits AHEAD of
                  # rag.ingest and vision.generate (both 200) and level
                  # with rag.ask. A person waiting on a reply should not
                  # queue behind a batch ingest.
                  default_priority=100,
                  # REQUIRED, not optional: a turn has a durable
                  # side-effect that predates the job -- the placeholder
                  # assistant Turn row -- which is the exact stranded-row
                  # condition this hook exists for.
                  on_terminal="agents.runtime.jobs.on_turn_terminal",
              )
          )
  ```

- [ ] Run green, then **restart the worker before any manual probe** — `docs/DEV.md:279-290`: `watcher` and `worker` run single management commands with no reload machinery, and `agent.turn` is job-kind code:

  ```bash
  .venv/bin/pytest -q agents/runtime/tests/test_jobs.py
  docker compose restart watcher worker
  ```

- [ ] Full matrix, then commit:

  ```bash
  git add agents/runtime agents/apps.py
  git commit -m "$(cat <<'EOF'
  feat(agents): the agent.turn job kind -- planner, summarizer, terminal hook

  plan_turn declares the agent's chat role plus the UNION of the roles of
  every tool it may call, over the transitive closure of agent-as-tool,
  bounded by MAX_AGENT_DEPTH and deduped by slug so a cycle terminates in
  the ENQUEUE path rather than hanging a request. Agent-as-tool specs
  declare empty roles -- a delegate's roles are not knowable at
  registration time -- so the planner supplies them by walking.

  Three tolerant drops, producing exactly the set the loop later builds
  its schemas from: unregistered, mutating, and unresolvable-role. That
  identity is what makes ruling R1's permissive save safe -- a key the
  planner dropped is a key the model is never offered. The agent's own
  chat role is NOT tolerant: the caller preflights it, so reaching the
  planner without one means the preflight was skipped.

  exclusive=True, declared honestly rather than being folded into it by
  the unmeasured-footprint branch. Footprints left None, per the
  scheduler's provenance contract.

  on_terminal is REQUIRED here: the placeholder assistant Turn row
  predates the job, which is the stranded-row condition the hook exists
  for. ONE conditional UPDATE filtered on queued/running, never a
  read-then-save -- a still-alive stale worker can be writing DONE in the
  same window. A test proves the hook does NOT fire when the handler ran
  and raised, by reading Worker._execute's handler_started gate at the
  seam.

  summarize_turn touches no database and raises nothing: /queue/ calls it
  per row, and a querying summarizer turns one page render into N.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 10: `agents/resident.py` and `manage.py sync_agents`

Three code-declared agents (deviation D3), synced to rows by a management command (deviation D5).

**`agents/resident.py` is PURE DATA — no Django import.** That is what lets `agents/apps.py::ready()` read it to build the agent-as-tool specs (Task 11) while keeping its no-DB promise, and what lets `sync_resident_agents` take the model **class** as an argument rather than importing it.

#### Why a management command and not `ready()` or a data migration

- **Not `ready()`** — Django forbids DB access there, and §7.5 says so itself. A tolerant `ready()` that swallowed the resulting error would be a hook that silently does nothing on a fresh box: worse than not having one, because it looks like it worked.
- **Not a data migration** — a migration would freeze *today's* resident list into history and re-run nothing when the list changes. The list changes with code, so its sync belongs to deploy, not to schema history. **Deviating from spec §7.5, which names a data migration as one of two callers.**
- **So: `manage.py sync_agents`**, run after `migrate` on every deploy. Task 14 documents that in `docs/DEV.md` and `docs/OPERATIONS.md`.

#### A retired resident is DISABLED, never deleted

`Conversation.agent` is `PROTECT` and `Agent.delete()` refuses outright for a resident row (Task 3). So when a slug disappears from `RESIDENT_AGENTS`, `sync_resident_agents` sets `enabled=False` and leaves the row — every past conversation stays readable, and nothing dangles.

#### The unregistered-key path is the normal one

`general` grants the vision tools, which are **not registered at all** when the `vision` feature flag is off. `Agent.save()` accepts and logs them (ruling R1, Task 3), `granted_tools` drops them again at prompt time (Task 4), and `sync_resident_agents` reports them under `unregistered_keys` rather than raising. The one thing it can still raise on is a resident declaring a **registered mutating** tool — which is correct, and is a code bug caught at deploy, exactly as ADR 0010:266-276 demands. **`rag.ingest` is that tool today** (the one `mutates=True` spec in the registry), and no resident grants it.

**Files**
- Create: `agents/resident.py`
- Create: `agents/management/__init__.py`, `agents/management/commands/__init__.py`, `agents/management/commands/sync_agents.py`
- Create: `agents/tests/test_resident.py`

**Interfaces**
- Consumes: `models.contracts.roles.CHAT_CONVERSE_ROLE`; `agents.limits.MAX_STEPS_DEFAULT`; `agents.contracts.tools.all_tools` (lazy, inside `sync_resident_agents`).
- Produces:
  - `agents.resident.AgentSpec` — frozen dataclass: `.slug`, `.name`, `.description`, `.system_prompt`, `.tool_keys: tuple[str, ...]`, `.llm_role: str = CHAT_CONVERSE_ROLE`, `.max_steps: int = MAX_STEPS_DEFAULT`, `.as_tool: bool = False`.
  - `agents.resident.RESIDENT_AGENTS: tuple[AgentSpec, ...]` — `general`, `library`, `illustrator`.
  - `agents.resident.sync_resident_agents(AgentModel) -> dict` — `{"created": int, "updated": int, "disabled": int, "unregistered_keys": list[str]}`. Idempotent.
  - `manage.py sync_agents` — prints the counts, exits non-zero on a raise.

**Steps**

- [ ] Failing test first. Create `agents/tests/test_resident.py`:

  ```python
  """Three code-declared agents, synced to rows, tolerantly.

  The test that matters most is the tolerant one: a resident grants tools
  that are not registered when a feature flag is off, and `sync_agents`
  must succeed anyway on that perfectly legal install. A strict check here
  would make a deploy fail because of a feature the operator chose not to
  enable.
  """
  from __future__ import annotations

  import pytest

  from agents.contracts.tools import ToolSpec, grantable_tools, register_tool
  from agents.models import Agent
  from agents.resident import RESIDENT_AGENTS, sync_resident_agents
  from agents.tests._helpers import restore_tools, snapshot_tools

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  class TestTheDeclarations:
      def test_there_are_exactly_three_and_the_slugs_are_the_expected_ones(self):
          assert {s.slug for s in RESIDENT_AGENTS} == {"general", "library", "illustrator"}

      def test_no_resident_grants_a_mutating_tool(self):
          """ADR 0010:266-276. Checked against `grantable_tools()` rather
          than a hand-written list, so a tool that BECOMES mutating later
          fails here instead of at somebody's deploy."""
          grantable = {spec.key for spec in grantable_tools()}
          registered = {spec.key for spec in __import__(
              "agents.contracts.tools", fromlist=["all_tools"]).all_tools()}
          for spec in RESIDENT_AGENTS:
              for key in spec.tool_keys:
                  if key in registered:
                      assert key in grantable, f"{spec.slug} grants mutating {key}"

      def test_every_declared_key_is_registered_or_a_known_gap(self):
          """A typo still fails; a legal gap does not. The known gaps are
          exactly the feature-flagged and later-phase keys, listed here by
          hand so adding one is a decision rather than an accident."""
          known_later = {"vision.operations", "vision.generate",
                         "agent.library", "agent.illustrator"}
          registered = {spec.key for spec in __import__(
              "agents.contracts.tools", fromlist=["all_tools"]).all_tools()}
          for spec in RESIDENT_AGENTS:
              for key in spec.tool_keys:
                  assert key in registered or key in known_later, f"{spec.slug}: {key}"

      def test_general_delegates_to_the_other_two(self):
          general = next(s for s in RESIDENT_AGENTS if s.slug == "general")
          assert {"agent.library", "agent.illustrator"} <= set(general.tool_keys)

      def test_only_the_two_specialists_are_exposed_as_tools(self):
          """`general` is the root an operator talks to, not a subroutine.
          Exposing it would let a delegate delegate back to the whole
          system for no gain the shared budget does not already bound."""
          assert {s.slug for s in RESIDENT_AGENTS if s.as_tool} == {"library", "illustrator"}

      def test_no_system_prompt_names_a_model(self):
          """ADR 0010's third amendment: the platform never names a model
          for the operator. A resident's prompt says what it DOES."""
          for spec in RESIDENT_AGENTS:
              lowered = spec.system_prompt.lower()
              for banned in ("gpt", "llama", "claude", "qwen", "mistral", "gemma"):
                  assert banned not in lowered

      def test_resident_py_imports_no_django(self):
          """Read as source, not as a live module: `agents/apps.py::ready()`
          imports this to build tool specs, and `ready()` must not pull
          Django models in. An AST check catches the import that a
          successful `import agents.resident` inside a configured Django
          would not."""
          import ast
          from pathlib import Path

          from django.conf import settings

          tree = ast.parse((Path(settings.BASE_DIR) / "agents" / "resident.py").read_text())
          for node in ast.walk(tree):
              if isinstance(node, ast.ImportFrom) and node.module:
                  assert not node.module.startswith("django")
                  assert node.module != "agents.models"
              elif isinstance(node, ast.Import):
                  for alias in node.names:
                      assert not alias.name.startswith("django")


  class TestSync:
      def test_it_creates_every_resident_and_marks_them_resident(self):
          out = sync_resident_agents(Agent)
          assert out["created"] == 3
          assert Agent.objects.filter(resident=True).count() == 3

      def test_it_is_idempotent_across_two_runs(self):
          sync_resident_agents(Agent)
          out = sync_resident_agents(Agent)
          assert out["created"] == 0
          assert Agent.objects.count() == 3

      def test_a_second_run_re_applies_a_changed_declaration(self):
          """The rows are a projection of the code, not a second source of
          truth. `Agent.save()` refuses an ordinary edit to a resident row;
          this is the one sanctioned path through it."""
          sync_resident_agents(Agent)
          row = Agent.objects.get(slug="library")
          Agent.objects.filter(pk=row.pk).update(system_prompt="drifted")
          sync_resident_agents(Agent)
          row.refresh_from_db()
          assert row.system_prompt != "drifted"

      def test_it_does_not_raise_when_a_declared_tool_is_unregistered(self):
          """A feature-gated tool with its flag off is a LEGAL install,
          not a broken one, and a deploy must not fail because of a
          feature the operator chose not to enable.

          The subject here is the REGISTRY, not the install, so this
          removes an entry from `_TOOLS` under the autouse snapshot
          fixture rather than overriding `FARABUNKER_FEATURES`. That is
          narrower (it names the exact key), it needs no flag override at
          all, and it therefore cannot trip the vision-flag rule --
          `config/urls.py` builds its vision mount once, at import time,
          and a flag override anywhere in the process is a hazard to every
          later test that resolves a URL.
          """
          from agents.contracts.tools import _TOOLS

          removed = _TOOLS.pop("vision.generate", None)
          assert removed is not None, (
              "this test is vacuous unless vision.generate was registered; "
              "run the suite with the vision feature enabled"
          )
          out = sync_resident_agents(Agent)
          assert out["created"] == 3
          assert "vision.generate" in out["unregistered_keys"]

      def test_an_unregistered_key_is_written_verbatim_to_the_row(self):
          sync_resident_agents(Agent)
          general = Agent.objects.get(slug="general")
          declared = next(s for s in RESIDENT_AGENTS if s.slug == "general")
          assert general.tool_keys == list(declared.tool_keys)

      def test_it_raises_when_a_resident_declares_a_REGISTERED_mutating_tool(self,
                                                                            monkeypatch):
          """A code bug caught at deploy, exactly as ADR 0010 demands. Not
          swallowed: a resident quietly stripped of a tool it declared
          would be a lie about what is running."""
          import agents.resident as resident

          register_tool(ToolSpec(key="stub.mutating", label="S", description="d",
                                 runner="agents.tests._helpers.stub_runner", mutates=True))
          bad = resident.AgentSpec(
              slug="bad", name="Bad", description="", system_prompt="",
              tool_keys=("stub.mutating",),
          )
          monkeypatch.setattr(resident, "RESIDENT_AGENTS", (bad,))
          with pytest.raises(ValueError):
              sync_resident_agents(Agent)

      def test_a_retired_resident_is_disabled_not_deleted(self, monkeypatch):
          """`Conversation.agent` is PROTECT and `Agent.delete()` refuses
          for a resident row. Disabling keeps every past conversation
          readable and leaves nothing dangling."""
          import agents.resident as resident

          sync_resident_agents(Agent)
          keep = tuple(s for s in resident.RESIDENT_AGENTS if s.slug != "illustrator")
          monkeypatch.setattr(resident, "RESIDENT_AGENTS", keep)
          out = sync_resident_agents(Agent)
          assert out["disabled"] == 1
          assert Agent.objects.get(slug="illustrator").enabled is False

      def test_it_takes_the_model_CLASS_so_it_imports_no_django(self):
          import inspect

          assert "AgentModel" in inspect.signature(sync_resident_agents).parameters


  class TestTheCommand:
      def test_it_syncs_and_reports(self, capsys):
          from django.core.management import call_command

          call_command("sync_agents")
          assert Agent.objects.filter(resident=True).count() == 3
          assert "3" in capsys.readouterr().out

      def test_running_it_twice_is_safe(self):
          from django.core.management import call_command

          call_command("sync_agents")
          call_command("sync_agents")
          assert Agent.objects.count() == 3
  ```

- [ ] Run and read the failure, then create `agents/resident.py`:

  ```python
  """The code-declared resident agents (spec section 7.5, deviation D3).

  PURE DATA. No Django import of any kind -- not `django.db`, not
  `agents.models`. Two things depend on that: `agents/apps.py::ready()`
  reads this module to build the agent-as-tool specs while keeping its
  no-DB promise, and `sync_resident_agents` takes the model CLASS as an
  argument so this file never reaches for one. An AST test pins it.

  THREE agents, not the spec's four. The owner's P2 direction dropped
  `critic` (the self-delegation worked example) and named the RAG one
  `library` rather than `librarian`. Agent-as-tool is still built and
  still proven, with `library` and `illustrator` as `general`'s
  delegates.

  A resident's rows are a PROJECTION of these declarations, not a second
  source of truth: `Agent.save()` refuses an ordinary edit to a resident
  row, and `sync_resident_agents` is the one sanctioned path through it.
  """
  from __future__ import annotations

  from dataclasses import dataclass

  from agents.limits import MAX_STEPS_DEFAULT
  from models.contracts.roles import CHAT_CONVERSE_ROLE


  @dataclass(frozen=True)
  class AgentSpec:
      """One code-declared agent.

      `as_tool` exposes this agent to OTHER agents as an `agent.<slug>`
      tool (Task 11). Only code-declared agents can be exposed that way:
      the specs are registered in `AppConfig.ready()`, which may not touch
      the database, so a row-declared agent has no way in. A named
      deferral, not an oversight.
      """

      slug: str
      name: str
      description: str
      system_prompt: str
      tool_keys: tuple[str, ...]
      llm_role: str = CHAT_CONVERSE_ROLE
      max_steps: int = MAX_STEPS_DEFAULT
      as_tool: bool = False


  RESIDENT_AGENTS: tuple[AgentSpec, ...] = (
      AgentSpec(
          slug="general",
          name="General assistant",
          description="Conversation with every safe tool on this box.",
          system_prompt=(
              "You are the general assistant on a private, offline-first box. You have "
              "tools for searching the operator's own document library, for looking at "
              "and generating images, and for reporting which models this box currently "
              "has assigned to each role.\n\n"
              "Call a tool when it would give you a fact you do not have. Do not "
              "describe calling a tool instead of calling it, and do not invent a "
              "result. If a tool fails, say what failed and what you can still do.\n\n"
              "Two specialist agents are available to you as tools: one that answers "
              "only from the document library, and one that turns a description into an "
              "image. Delegate when the whole request belongs to one of them; do the "
              "work yourself when it does not."
          ),
          # rag.ingest is deliberately absent: it is the one mutates=True
          # spec in the registry, and ADR 0010:266-276 makes a mutating
          # tool ungrantable until Identity & Auth lands. `Agent.save()`
          # would refuse this row outright if it were listed.
          tool_keys=(
              "rag.search", "rag.ask", "models.status",
              "vision.operations", "vision.generate",
              "agent.library", "agent.illustrator",
          ),
      ),
      AgentSpec(
          slug="library",
          name="Library",
          description="Answers only from the operator's own documents, with citations.",
          system_prompt=(
              "You answer only from the operator's own ingested documents. Search the "
              "library before you answer, and base every claim on what you found.\n\n"
              "If the library has nothing relevant, say so plainly. Do not fill the gap "
              "with general knowledge -- an answer that is not in the documents is not "
              "an answer this agent gives."
          ),
          tool_keys=("rag.search", "rag.ask"),
          as_tool=True,
      ),
      AgentSpec(
          slug="illustrator",
          name="Illustrator",
          description="Turns a description into an image prompt and generates it.",
          system_prompt=(
              "You turn a description into a generated image.\n\n"
              "First ask what image operations this box actually offers and what each "
              "one accepts -- never assume. Then compose a clear, concrete prompt from "
              "what the person asked for, pick the operation that fits, and generate.\n\n"
              "You can also search the document library when a request refers to "
              "something in it. Report what you generated and what you passed; if an "
              "argument was dropped because the picked operation does not accept it, "
              "say which."
          ),
          tool_keys=("vision.operations", "vision.generate", "rag.search"),
          as_tool=True,
      ),
  )


  def sync_resident_agents(AgentModel) -> dict:
      """Upsert every `RESIDENT_AGENTS` entry as `resident=True`, keyed
      case-insensitively on `slug`.

      Takes the model CLASS so this module imports no Django (see the
      module docstring) and so a caller can pass a historical model if one
      ever needs to.

      Idempotent. Uses an explicit get-then-set-then-save rather than
      `update_or_create` (which spec section 7.5 suggests) because
      `Agent.save()`'s resident guard is bypassed only by the keyword-only
      `_from_resident_sync=True`, and `update_or_create` calls `save()`
      itself with no way to pass it.

      NEVER raises for a tool key that is not currently registered -- a
      feature-gated tool (vision, with the flag off) or a later-phase one
      is the normal case, not a fault (ruling R1). Such a key is written
      to the row verbatim and returned under `unregistered_keys`; the
      prompt builder drops it at run time.

      DOES raise when a resident declares a REGISTERED `mutates=True`
      tool. That is a code bug caught at deploy, exactly as
      ADR 0010:266-276 demands, and swallowing it would leave a resident
      quietly stripped of a tool it declared -- a lie about what is
      running.

      A slug that has disappeared from `RESIDENT_AGENTS` is DISABLED, never
      deleted: `Conversation.agent` is `PROTECT` and `Agent.delete()`
      refuses for a resident row, and a disabled row keeps every past
      conversation readable.
      """
      from agents.contracts.tools import all_tools

      registered = {spec.key for spec in all_tools()}
      created = updated = disabled = 0
      unregistered: list[str] = []

      declared_slugs = set()
      for spec in RESIDENT_AGENTS:
          declared_slugs.add(spec.slug.lower())
          unregistered.extend(k for k in spec.tool_keys if k not in registered)

          row = AgentModel.objects.filter(slug__iexact=spec.slug).first()
          if row is None:
              row = AgentModel(slug=spec.slug)
              created += 1
          else:
              updated += 1
          row.name = spec.name
          row.description = spec.description
          row.system_prompt = spec.system_prompt
          row.llm_role = spec.llm_role
          row.tool_keys = list(spec.tool_keys)
          row.max_steps = spec.max_steps
          row.resident = True
          row.enabled = True
          row.save(_from_resident_sync=True)

      for row in AgentModel.objects.filter(resident=True, enabled=True):
          if row.slug.lower() not in declared_slugs:
              row.enabled = False
              row.save(_from_resident_sync=True)
              disabled += 1

      return {
          "created": created,
          "updated": updated,
          "disabled": disabled,
          "unregistered_keys": sorted(set(unregistered)),
      }
  ```

- [ ] Create `agents/management/commands/sync_agents.py`:

  ```python
  """`python manage.py sync_agents` -- apply `agents/resident.py`'s
  declarations to their rows.

  Run after `migrate` on every deploy that changed `RESIDENT_AGENTS`.
  This is the ONLY caller of `sync_resident_agents`: `AppConfig.ready()`
  may not touch the database, and a data migration would freeze today's
  list into history and re-run nothing when the code changed
  (deviation D5).

  Thin on purpose, the same division `run_jobs` draws between itself and
  `Worker`: it owns none of the sync's behaviour, it only calls it and
  reports what happened.
  """
  from __future__ import annotations

  from django.core.management.base import BaseCommand

  from agents.models import Agent
  from agents.resident import sync_resident_agents


  class Command(BaseCommand):
      help = "Sync the code-declared resident agents to their database rows."

      def handle(self, *args, **options) -> None:
          result = sync_resident_agents(Agent)
          self.stdout.write(
              f"agents: {result['created']} created, {result['updated']} updated, "
              f"{result['disabled']} disabled"
          )
          if result["unregistered_keys"]:
              # INFORMATION, not a warning: a feature-gated tool with its
              # flag off is a legal install, and the prompt builder drops
              # the key at run time.
              self.stdout.write(
                  "not registered on this install (dropped at prompt time): "
                  + ", ".join(result["unregistered_keys"])
              )
  ```

- [ ] Run green, then sync for real and look at what landed:

  ```bash
  .venv/bin/pytest -q agents/tests/test_resident.py
  .venv/bin/python manage.py sync_agents
  .venv/bin/python manage.py shell -c "from agents.models import Agent; print([(a.slug, a.tool_keys) for a in Agent.objects.all()])"
  ```

- [ ] Full matrix, then commit:

  ```bash
  git add agents/resident.py agents/management agents/tests/test_resident.py
  git commit -m "$(cat <<'EOF'
  feat(agents): three code-declared residents, synced by manage.py sync_agents

  general (every safe tool plus the other two as tools), library (the
  document library only), illustrator (image operations, generation, and
  library search). Three, not the spec's four: the owner's P2 direction
  dropped `critic` and renamed `librarian` to `library`.

  agents/resident.py is PURE DATA and an AST test pins it -- no django
  import, no agents.models import. That is what lets AppConfig.ready()
  read it to build the agent-as-tool specs while keeping its no-DB
  promise, and what lets sync_resident_agents take the model CLASS.

  Deviating from spec 7.5's two callers: manage.py sync_agents is the ONLY
  one. ready() may not touch the database, and a tolerant ready() that
  swallowed the error would silently do nothing on a fresh box. A data
  migration would freeze today's list into history and re-run nothing when
  the code changed.

  Tolerant where the spec says tolerant: an unregistered key (vision with
  its flag off) is written verbatim, reported, and dropped again at prompt
  time -- a deploy must not fail because of a feature the operator chose
  not to enable. NOT tolerant where ADR 0010 says not: a resident
  declaring a REGISTERED mutating tool raises at deploy. rag.ingest is
  that tool today and no resident grants it.

  A retired resident is DISABLED, never deleted: Conversation.agent is
  PROTECT and Agent.delete() refuses for a resident row.

  No system prompt names a model, and a test enforces it.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 11: agent-as-tool — `agent.<slug>`, the shared budget, and the recursion guard

Spec §6.4, reconciled with deviation D4: **one spec per code-declared resident that declares `as_tool=True`**, all sharing one runner.

#### Three properties make this safe, and only one of them is the depth cap

1. **The budget is SHARED, not nested.** A delegate spends from the root turn's `StepBudget` — the same mutable object, passed through `ToolContext`. Total LLM calls per turn are bounded by `Agent.max_steps` regardless of how the delegation tree is shaped. **This, not the depth cap, is the real guard.**
2. **The depth cap is enforced by OMISSION.** At `depth == MAX_AGENT_DEPTH - 1`, the `agent.*` specs are simply left out of the delegate's tool list. The model is never offered a tool it will be refused for using. The `ToolRefused` branch in the runner is belt-and-braces for a malformed call, not the mechanism.
3. **A delegate gets a fresh message list, not the parent's history.** Its own system prompt plus the task string. A delegate is a subroutine with an assignment, not a second participant in the conversation — replaying the parent's history would hand it context it was not asked about and make its own budget spend unpredictable.

#### `run_loop` is extracted here, in the task that needs it

Task 8 wrote the loop inside `_run_turn` because nothing else called it. The delegate needs the same loop at a different depth, with a different agent, a different LLM, and no `Turn` row of its own to update — so this task extracts `run_loop` and has both callers use it. Extracting it speculatively in Task 8 would have been a shape guessed at rather than one two callers asked for.

**Files**
- Modify: `agents/runtime/loop.py` — extract `LoopResult` and `run_loop`; `_run_turn` calls it
- Create: `agents/runtime/delegate.py`
- Modify: `agents/resident.py` — `AGENT_TOOL_PREFIX`, `agent_tool_specs()`
- Modify: `agents/runtime/jobs.py` — use the shared `AGENT_TOOL_PREFIX`
- Modify: `agents/apps.py` — `ready()` registers the agent-as-tool specs
- Create: `agents/runtime/tests/test_delegate.py`

**Interfaces**
- Consumes: `agents.runtime.loop.run_loop`/`_available_tools`; `agents.contracts.tools.Principal`/`ToolContext`/`ToolRefused`/`ToolResult`/`ToolSpec`; `agents.limits.MAX_AGENT_DEPTH`; `agents.models.Agent`/`Conversation`; `models.contracts.gateway.get_llm_for`; `models.contracts.operations.Param`.
- Produces:
  - `agents.runtime.loop.LoopResult` — frozen dataclass: `.text: str`, `.artifacts: tuple[str, ...]`, `.tool_calls: tuple[dict, ...]`, `.steps_used: int`.
  - `agents.runtime.loop.run_loop(*, agent, conversation, messages, llm, budget, principal, job_ctx, depth, available) -> LoopResult`
  - `agents.runtime.delegate.run_agent_tool(args: dict, ctx: ToolContext) -> ToolResult`
  - `agents.resident.AGENT_TOOL_PREFIX: str = "agent."`
  - `agents.resident.agent_tool_specs() -> tuple[ToolSpec, ...]` — one `ToolSpec` per `as_tool=True` resident: key `agent.<slug>`, params `(task,)`, **`roles=()`**, runner `"agents.runtime.delegate.run_agent_tool"`.

**Steps**

- [ ] Extract `run_loop` from `_run_turn` **first, with no behaviour change**, and confirm Task 8's whole test module still passes untouched. A refactor that needed its own tests changed was not a refactor:

  ```bash
  .venv/bin/pytest -q agents/runtime/tests/test_loop.py
  ```

  `run_loop` takes everything the loop body reads and returns a `LoopResult`; `_run_turn` keeps ownership of the `Turn` row it updates, the RUNNING write, and `_finish`. The tool-turn writes stay inside `run_loop` — they are what a loop does, at whatever depth it runs.

- [ ] Failing test first. Create `agents/runtime/tests/test_delegate.py`:

  ```python
  """Agent-as-tool: a nested loop, in the same job, on the same budget.

  The budget is the real guard. The depth cap is enforced by OMISSION --
  the model is never offered a tool it would be refused for using -- and
  the ToolRefused branch is belt-and-braces for a malformed call.
  """
  from __future__ import annotations

  import pytest

  from agents.contracts.tools import Principal, ToolRefused, ToolSpec, register_tool
  from agents.limits import MAX_AGENT_DEPTH
  from agents.models import Turn
  from agents.resident import agent_tool_specs
  from agents.runtime.delegate import run_agent_tool
  from agents.runtime.tests._helpers import (  # noqa: F401 -- the fixture import
      FakeToolLLM, bind_chat_role, bound_chat_role, make_agent, make_budget,   # IS the
      make_conversation, make_tool_ctx, make_turn, patch_llm, restore_tools,   # registration
      snapshot_tools,
  )
  from models.contracts.roles import CHAT_CONVERSE_ROLE

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  @pytest.fixture(autouse=True)
  def _bind(db):
      """EVERY test in this module runs a delegate, and every delegate
      resolves its own agent's chat role through `resolve_chat` -- which
      raises when nothing is bound. Binding once, autouse, rather than in
      each test: a per-test call is a line seven of the eight tests below
      would forget, and the failure it produces (`ValueError` from
      `resolve`) reads like a bug in the code under test rather than a
      missing fixture."""
      bind_chat_role(CHAT_CONVERSE_ROLE)


  class TestTheSpecs:
      def test_one_spec_per_as_tool_resident_and_no_others(self):
          assert {s.key for s in agent_tool_specs()} == {"agent.library", "agent.illustrator"}

      def test_each_declares_a_required_task_param_and_no_roles(self):
          """Empty `roles` is deliberate (spec section 6.4): a delegate's
          roles are not knowable at registration time, so `plan_turn`
          supplies them by walking the closure."""
          for spec in agent_tool_specs():
              assert spec.roles == ()
              assert [p.key for p in spec.params] == ["task"]
              assert spec.params[0].required is True

      def test_they_all_share_one_runner(self):
          """Which is exactly why `ToolContext.tool_key` exists: a runner's
          signature is (args, ctx), so N specs sharing one runner have no
          other way to learn which one invoked them."""
          assert len({s.runner for s in agent_tool_specs()}) == 1

      def test_none_of_them_mutates(self):
          assert not any(s.mutates for s in agent_tool_specs())


  class TestTheRunner:
      def test_it_runs_the_named_agents_loop_and_returns_its_text(self):
          make_agent(slug="library", system_prompt="You are the library.")
          conv = make_conversation(agent=make_agent(slug="general"))
          llm = FakeToolLLM([("final", "the library says yes")])
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
          with patch_llm(llm):
              result = run_agent_tool({"task": "what does it say?"}, ctx)
          assert result.text == "the library says yes"
          assert result.data["agent"] == "library"

      def test_the_delegate_gets_its_OWN_system_prompt_and_the_task(self):
          """A fresh message list, not the parent's history: a delegate is
          a subroutine with an assignment, not a second participant."""
          make_agent(slug="library", system_prompt="You are the library.")
          conv = make_conversation(agent=make_agent(slug="general"))
          llm = FakeToolLLM([("final", "x")])
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
          with patch_llm(llm):
              run_agent_tool({"task": "the assignment"}, ctx)
          contents = [m.content for m in llm.calls[0][0]]
          assert contents == ["You are the library.", "the assignment"]

      def test_the_budget_is_SHARED_not_nested(self):
          """The real guard. A delegate spends from the ROOT turn's
          budget, so total LLM calls per turn stay bounded by
          Agent.max_steps however the delegation tree is shaped."""
          make_agent(slug="library")
          conv = make_conversation(agent=make_agent(slug="general"))
          budget = make_budget(steps=5)
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                              budget=budget)
          with patch_llm(FakeToolLLM([("final", "x")])):
              run_agent_tool({"task": "t"}, ctx)
          assert budget.steps_left == 4

      def test_the_delegates_turns_are_written_one_level_deeper(self):
          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          make_agent(slug="library", tool_keys=["stub.safe"])
          conv = make_conversation(agent=make_agent(slug="general"))
          llm = FakeToolLLM([("tool", "stub.safe", {}), ("final", "x")])
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library", depth=0)
          with patch_llm(llm):
              run_agent_tool({"task": "t"}, ctx)
          assert conv.turns.get(role=Turn.Role.TOOL).depth == 1

      def test_the_delegates_principal_is_the_delegate_not_the_caller(self):
          """The audit trail must say who actually called the tool. A
          delegate acting under the root agent's principal would make the
          ToolInvocation table lie about what ran on whose behalf."""
          from agents.models import ToolInvocation

          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          make_agent(slug="library", tool_keys=["stub.safe"], resident=True)
          conv = make_conversation(agent=make_agent(slug="general"))
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
          with patch_llm(FakeToolLLM([("tool", "stub.safe", {}), ("final", "x")])):
              run_agent_tool({"task": "t"}, ctx)
          assert ToolInvocation.objects.get().principal_key == "library"


  class TestHistoryIsolation:
      def test_a_delegates_turns_never_replay_into_the_parents_prompt(self):
          """M6. A delegate's turns live in this same conversation at
          depth >= 1 so an operator can audit exactly what ran -- but they
          are NOT the parent's history. Replaying them would hand the
          parent model the inside of a subroutine it only ever saw the
          return value of, with tool-call blocks naming tools the parent
          may not even hold."""
          from agents.runtime.prompt import history_messages

          register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                                 runner="agents.runtime.tests._helpers.runner_ok"))
          make_agent(slug="library", tool_keys=["stub.safe"])
          conv = make_conversation(agent=make_agent(slug="general"))
          make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="ask")
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
          with patch_llm(FakeToolLLM([("tool", "stub.safe", {}), ("final", "x")])):
              run_agent_tool({"task": "t"}, ctx)


          assert conv.turns.filter(depth=1).exists()          # they WERE written
          assert [m.content for m in history_messages(conv)] == ["ask"]


  class TestTheGuards:
      def test_at_the_depth_cap_the_agent_tools_are_OMITTED_from_the_delegate(self):
          """Enforcement by omission. The model is never offered a tool it
          will be refused for using."""
          for spec in agent_tool_specs():
              register_tool(spec)
          make_agent(slug="library", tool_keys=["agent.illustrator"])
          make_agent(slug="illustrator")
          conv = make_conversation(agent=make_agent(slug="general"))
          llm = FakeToolLLM([("final", "x")])
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                              depth=MAX_AGENT_DEPTH - 1)
          with patch_llm(llm):
              run_agent_tool({"task": "t"}, ctx)
          assert llm.calls[0][1] is None

      def test_at_the_depth_cap_a_direct_call_is_REFUSED(self):
          make_agent(slug="library")
          conv = make_conversation(agent=make_agent(slug="general"))
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                              depth=MAX_AGENT_DEPTH)
          with pytest.raises(ToolRefused):
              run_agent_tool({"task": "t"}, ctx)

      def test_an_unknown_or_disabled_agent_is_REFUSED_not_errored(self):
          """Nothing the model can say makes a disabled agent run, so this
          gets no retry (section 10.1)."""
          make_agent(slug="library", enabled=False)
          conv = make_conversation(agent=make_agent(slug="general"))
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
          with pytest.raises(ToolRefused):
              run_agent_tool({"task": "t"}, ctx)

      def test_an_exhausted_budget_refuses_before_starting_a_nested_loop(self):
          make_agent(slug="library")
          conv = make_conversation(agent=make_agent(slug="general"))
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                              budget=make_budget(steps=0))
          with pytest.raises(ToolRefused):
              run_agent_tool({"task": "t"}, ctx)

      def test_a_blank_tool_key_refuses_rather_than_guessing(self):
          """`ctx.tool_key` is set by `invoke_tool`. A blank one means this
          runner was called some other way, and guessing which agent was
          meant would run the wrong one."""
          conv = make_conversation()
          with pytest.raises(ToolRefused):
              run_agent_tool({"task": "t"}, make_tool_ctx(conversation_id=str(conv.id)))

      def test_a_delegation_cycle_terminates_on_the_shared_budget(self):
          """Two agents naming each other. The depth cap stops the tree and
          the shared budget stops the total -- both, not either."""
          for spec in agent_tool_specs():
              register_tool(spec)
          make_agent(slug="library", tool_keys=["agent.illustrator"])
          make_agent(slug="illustrator", tool_keys=["agent.library"])
          conv = make_conversation(agent=make_agent(slug="general"))
          budget = make_budget(steps=6)
          ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                              budget=budget, depth=0)
          script = [("tool", "agent.illustrator", {"task": "t"})] * 10
          with patch_llm(FakeToolLLM(script)):
              run_agent_tool({"task": "t"}, ctx)
          assert budget.steps_left <= 0        # terminated, and on the shared budget
  ```

- [ ] Run and read the failure, then add to `agents/resident.py`:

  ```python
  # The prefix every agent-as-tool spec key carries. Shared with
  # `agents/runtime/jobs.py`'s planner closure walk, which needs to
  # recognise one without importing the delegate runner it never calls.
  AGENT_TOOL_PREFIX = "agent."


  def agent_tool_specs() -> tuple:
      """One `ToolSpec` per `as_tool=True` resident (spec section 6.4,
      deviation D4).

      The spec says ONE `agent.delegate` tool with an `agent` param,
      "because tools are code-registered; agents are rows". This registers
      one spec PER AGENT and stays inside that constraint by building them
      from CODE -- `RESIDENT_AGENTS` -- never from rows, so
      `AppConfig.ready()` still touches no database. A user-built agent is
      therefore NOT exposed as a tool in P2; that needs either DB access
      in `ready()` (forbidden) or a dynamic registry (not designed). A
      named deferral.

      All of them share ONE runner, which is what
      `ToolContext.tool_key` exists for: a runner's signature is
      `(args, ctx)`, and N specs behind one function have no other way to
      learn which one invoked them.

      `roles=()` on every spec, deliberately: a delegate's roles are not
      knowable at registration time, and `agents.runtime.jobs.plan_turn`
      supplies them at enqueue time by walking the closure.
      """
      from agents.contracts.tools import ToolSpec
      from models.contracts.operations import Param

      return tuple(
          ToolSpec(
              key=f"{AGENT_TOOL_PREFIX}{spec.slug}",
              label=f"Ask the {spec.name} agent",
              description=(
                  f"{spec.description} Hand it one self-contained task and it will "
                  f"work on it with its own tools and hand back what it found. It "
                  f"cannot see this conversation, so say everything it needs."
              ),
              params=(Param(
                  "task", "text", "Task", required=True,
                  description="The complete, self-contained assignment for that agent.",
              ),),
              roles=(),
              runner="agents.runtime.delegate.run_agent_tool",
          )
          for spec in RESIDENT_AGENTS
          if spec.as_tool
      )
  ```

  and replace `agents/runtime/jobs.py`'s local `AGENT_TOOL_PREFIX` with `from agents.resident import AGENT_TOOL_PREFIX` (a module-scope import of a pure module — legal, and it removes a duplicated literal).

- [ ] Create `agents/runtime/delegate.py`:

  ```python
  """Agent-as-tool: a nested agent loop, inline, inside the SAME turn job
  and on the SAME budget (spec section 6.4, deviation D4).

  THREE properties make this safe, and only one of them is the depth cap:

  1. THE BUDGET IS SHARED, NOT NESTED. A delegate spends from the root
     turn's `StepBudget` -- the same mutable object, reached through
     `ToolContext`. Total LLM calls per turn stay bounded by
     `Agent.max_steps` however the delegation tree is shaped. This is the
     real guard.
  2. THE DEPTH CAP IS ENFORCED BY OMISSION. At
     `depth == MAX_AGENT_DEPTH - 1` the `agent.*` specs are left out of the
     delegate's tool list, so the model is never offered a tool it would
     be refused for using. The `ToolRefused` branch below is
     belt-and-braces for a malformed call, not the mechanism.
  3. A DELEGATE GETS A FRESH MESSAGE LIST. Its own system prompt plus the
     task string -- never the parent's history. A delegate is a subroutine
     with an assignment, not a second participant in the conversation;
     replaying the parent's history would hand it context nobody asked it
     about and make its budget spend unpredictable.

  It runs INLINE, in the same job. It enqueues nothing and waits on
  nothing -- the rule every tool runner obeys.
  """
  from __future__ import annotations

  import logging

  from llama_index.core.llms import ChatMessage, MessageRole

  from agents.contracts.tools import Principal, ToolRefused, ToolResult
  from agents.limits import MAX_AGENT_DEPTH
  from agents.models import Agent, Conversation
  from agents.resident import AGENT_TOOL_PREFIX
  from agents.runtime.bindings import resolve_chat
  from agents.runtime.loop import _available_tools, run_loop
  from models.contracts import gateway

  logger = logging.getLogger(__name__)

  # Module scope, not in-body: nothing here is heavy, nothing touches a
  # database at import time, and `agents/apps.py::ready()` never imports
  # this module at all (the runner is a dotted-path STRING). The lazy-import
  # discipline exists to keep `ready()` light; it is not a house style to
  # apply where it buys nothing. `agents.runtime.loop` does not import this
  # module back, so there is no cycle.


  def run_agent_tool(args: dict, ctx) -> ToolResult:
      """Run the agent named by `ctx.tool_key` on `args["task"]`.

      Refuses -- `ToolRefused`, no retry -- when the depth cap is reached,
      the budget is already spent, `ctx.tool_key` names no agent, or the
      named agent does not exist or is disabled. None of those is
      something the model can talk its way out of, which is exactly what
      separates a refusal from an error (section 10.1).
      """
      slug = (ctx.tool_key or "")[len(AGENT_TOOL_PREFIX):] if ctx.tool_key else ""
      if not slug:
          # `invoke_tool` sets `tool_key`. A blank one means this runner
          # was reached some other way, and guessing which agent was meant
          # would run the wrong one.
          raise ToolRefused(
              "This delegation tool was invoked without naming an agent, so nothing ran."
          )
      if ctx.depth >= MAX_AGENT_DEPTH:
          raise ToolRefused(
              f"Delegation is limited to {MAX_AGENT_DEPTH} levels and this turn is "
              f"already at that limit. Do the work yourself or answer with what you have."
          )
      if ctx.budget.exhausted or ctx.budget.expired:
          raise ToolRefused(
              "This turn has no steps or time left, so another agent cannot be started."
          )

      agent = Agent.objects.filter(slug__iexact=slug, enabled=True).first()
      if agent is None:
          raise ToolRefused(f"There is no enabled agent called {slug!r} on this system.")

      conversation = Conversation.objects.get(id=ctx.conversation_id)
      depth = ctx.depth + 1
      principal = Principal(
          kind="resident_agent" if agent.resident else "user_agent", key=agent.slug,
      )

      available = _available_tools(principal, agent)
      if depth >= MAX_AGENT_DEPTH:
          # Property 2: omission, not refusal.
          available = {
              key: spec for key, spec in available.items()
              if not key.startswith(AGENT_TOOL_PREFIX)
          }

      messages = []
      if agent.system_prompt:
          messages.append(ChatMessage(role=MessageRole.SYSTEM, content=agent.system_prompt))
      messages.append(ChatMessage(role=MessageRole.USER, content=args["task"]))

      result = run_loop(
          agent=agent,
          conversation=conversation,
          messages=messages,
          # `resolve_chat(agent, None)` -- the same one function the
          # planner and the loop call, so a delegate cannot resolve its
          # own role by a different rule. `None` because a delegate never
          # inherits the caller's picker override: it binds its own role.
          # `gateway.get_llm_for` is reached through the MODULE so a test
          # patching that attribute is actually seen (see `patch_llm`).
          llm=gateway.get_llm_for(resolve_chat(agent, None)[0]),
          budget=ctx.budget,          # SHARED, not a fresh one
          principal=principal,
          job_ctx=ctx.job,
          depth=depth,
          available=available,
      )
      return ToolResult(
          text=result.text,
          data={"agent": agent.slug, "steps_used": result.steps_used,
                "tool_calls": list(result.tool_calls)},
          artifacts=result.artifacts,
      )
  ```

- [ ] Register the specs in `AgentsConfig.ready()`, after the job kind:

  ```python
          # Agent-as-tool (spec section 6.4, deviation D4). Built from
          # CODE -- `RESIDENT_AGENTS` -- never from rows, so this method
          # still touches no database. The runner stays a dotted-path
          # STRING, so `agents.runtime.delegate` is not imported here.
          from agents.contracts.tools import register_tool
          from agents.resident import agent_tool_specs

          for spec in agent_tool_specs():
              register_tool(spec)
  ```

- [ ] Run green. Then confirm `ready()` still imports nothing heavy, by the property rather than by reading it:

  ```bash
  .venv/bin/pytest -q agents/runtime/tests/test_delegate.py
  .venv/bin/python -c "
  import os, django
  os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
  django.setup()
  import sys
  assert 'agents.runtime.delegate' not in sys.modules, 'ready() imported the delegate runner'
  assert 'agents.runtime.loop' not in sys.modules, 'ready() imported the loop'
  print('ready() stayed light')
  "
  ```

- [ ] Full matrix, then commit:

  ```bash
  git add agents
  git commit -m "$(cat <<'EOF'
  feat(agents): agent-as-tool -- agent.<slug>, shared budget, depth by omission

  One spec per code-declared resident that declares as_tool, all sharing
  one runner. Spec section 6.4 says ONE agent.delegate tool "because tools
  are code-registered; agents are rows"; this stays inside that constraint
  by building the specs from RESIDENT_AGENTS -- code, never rows -- so
  ready() still touches no database. A user-built agent is not exposed as
  a tool in P2: that needs DB access in ready() (forbidden) or a dynamic
  registry (not designed). A named deferral.

  Three properties, and only one is the depth cap. The budget is SHARED,
  not nested -- total LLM calls per turn stay bounded by Agent.max_steps
  however the tree is shaped, and that is the real guard. The depth cap is
  enforced by OMISSION: at the last level the agent.* specs are left out
  of the delegate's tool list, so the model is never offered a tool it
  would be refused for using. And a delegate gets a FRESH message list --
  its own system prompt plus the task -- because it is a subroutine with
  an assignment, not a second participant in the conversation.

  The delegate's tool calls are audited under the DELEGATE's principal,
  not the caller's: the ToolInvocation table has to say who actually ran
  what.

  run_loop was extracted from _run_turn in this task rather than
  speculatively in Task 8 -- a shape two callers asked for, not one
  guessed at. Task 8's tests passed unchanged across the refactor.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 12: `manage.py agent_turn` — the proof surface, and the preflight

P2 ships no page, so this command is how a human drives a turn and how §12.3's gate ("`agent.turn` runs end to end from a shell") is met. It is also where §10.1's *preflight* rows live in this phase: the unbound-role 503 and the cannot-call-tools 503 have no view to be raised from yet, so the command refuses **before enqueuing**, with the same three-state honesty `tools.vision.services.preflight` uses (`tools/vision/services.py:68-88,150-165`).

#### A management command may poll the queue. A tool runner may not.

The never-block rule (Global Constraints) is about a **tool runner**, which executes *inside* a job and holds the machine's one execution slot in sequential mode. This command holds no slot: it is the thing standing outside the queue waiting for the queue to finish, exactly as a browser poller would. Task 14's source-text guard sweeps `agents/runtime/` and the three `tools.py` modules — **it must not sweep `agents/management/`**, and Task 14 states that exclusion with this reason attached rather than leaving it to be inferred from a missing path.

#### Two rows before the enqueue, and why

The command writes the USER turn (`state=done`) **and** a placeholder ASSISTANT turn (`state=queued`) before it enqueues, and the payload carries the assistant turn's pk. That placeholder is the durable side-effect that predates the job — the thing `on_turn_terminal` exists to fix up (Task 9) — and creating it after the enqueue would open a window where a cancel finds nothing to flip.

**Files**
- Create: `agents/management/commands/agent_turn.py`
- Create: `agents/tests/test_agent_turn_command.py`
- Modify: `agents/tests/_helpers.py` — `_finished_job` and the four queue fixtures

**Interfaces**
- Consumes: `agents.models.Agent`/`Conversation`/`Turn`; `agents.contracts.tools.granted_tools`; `agents.runtime.loop._supports_tool_calling`; `models.contracts.bindings.resolve`; `models.registry.bindings.resolve_connection_named`; `models.contracts.queue.enqueue`/`get_job`/`QueueUnavailable` (`models/contracts/queue.py:51,78,93`).
- Produces: `manage.py agent_turn <agent-slug> "<message>" [--conversation <uuid>] [--connection <pk>] [--timeout <seconds>]`. Prints the assistant text, then each tool call with its outcome and any artifacts. Raises `CommandError` (never a traceback) on every refusal.

**Steps**

- [ ] Failing test first. Create `agents/tests/test_agent_turn_command.py`:

  ```python
  """The CLI proof surface, and the preflight that keeps a doomed turn out
  of the queue.

  Vision-flag rule: these tests run a management command and never call
  `reverse()` or the test `Client`, so they are not subject to the
  keep-vision clause -- but none of them overrides the flag either.
  """
  from __future__ import annotations

  import pytest
  from django.core.management import call_command
  from django.core.management.base import CommandError

  from agents.models import Conversation, Turn
  from agents.tests._helpers import (  # noqa: F401 -- a fixture is invisible to
      _finished_job, bound_chat_role, fake_failed_queue,   # pytest until a test
      fake_queue, fake_queue_with_tool, fake_running_queue,  # module imports it
      make_agent,
  )

  pytestmark = pytest.mark.django_db


  class TestPreflight:
      def test_an_unknown_agent_is_refused_before_anything_is_written(self):
          with pytest.raises(CommandError) as exc:
              call_command("agent_turn", "nope", "hello")
          assert "nope" in str(exc.value)
          assert Conversation.objects.count() == 0

      def test_a_disabled_agent_is_refused(self):
          make_agent(slug="off", enabled=False)
          with pytest.raises(CommandError):
              call_command("agent_turn", "off", "hello")

      def test_a_blank_message_is_refused(self):
          make_agent(slug="general")
          with pytest.raises(CommandError):
              call_command("agent_turn", "general", "   ")

      def test_an_unbound_chat_role_is_refused_and_names_the_role(self, monkeypatch):
          """Section 10.1's "chat role unbound" row. No page exists yet, so
          the command is where it surfaces -- and it surfaces BEFORE the
          enqueue, so a turn that cannot possibly run never becomes a
          queued job somebody has to cancel."""
          make_agent(slug="general", llm_role="nothing.bound.here")
          with pytest.raises(CommandError) as exc:
              call_command("agent_turn", "general", "hello")
          assert "nothing.bound.here" in str(exc.value)
          assert Turn.objects.count() == 0

      def test_a_model_that_cannot_call_tools_is_refused_when_the_agent_has_tools(
              self, monkeypatch, bound_chat_role):
          """Section 10.1's "bound model cannot call tools" row. The LOOP
          also handles this (Task 8) as belt-and-braces; refusing here is
          what keeps the honest message in front of the person who typed
          the command rather than buried in a finished turn."""
          monkeypatch.setattr(
              "agents.management.commands.agent_turn._supports_tool_calling", lambda r: False
          )
          make_agent(slug="general", tool_keys=["rag.search"])
          with pytest.raises(CommandError) as exc:
              call_command("agent_turn", "general", "hello")
          assert "cannot call tools" in str(exc.value)

      def test_a_no_tool_agent_is_NOT_refused_by_the_tool_calling_gate(
              self, monkeypatch, bound_chat_role, fake_queue):
          """An agent with no tools does not need a tool-capable model, and
          refusing it would be a lie about what it needs."""
          monkeypatch.setattr(
              "agents.management.commands.agent_turn._supports_tool_calling", lambda r: False
          )
          make_agent(slug="plain", tool_keys=[])
          call_command("agent_turn", "plain", "hello")
          assert Turn.objects.filter(role=Turn.Role.USER).count() == 1

      def test_an_unavailable_queue_is_refused_with_operator_copy(
              self, monkeypatch, bound_chat_role):
          """`QueueUnavailable` (`models/contracts/queue.py:51`) means "no
          queue", which must never look like "no job"."""
          from models.contracts.queue import QueueUnavailable

          make_agent(slug="general")
          monkeypatch.setattr(
              "agents.management.commands.agent_turn.enqueue",
              lambda *a, **k: (_ for _ in ()).throw(QueueUnavailable("down")),
          )
          with pytest.raises(CommandError) as exc:
              call_command("agent_turn", "general", "hello")
          assert "migrate" in str(exc.value).lower() or "queue" in str(exc.value).lower()


  class TestRowsAndPayload:
      def test_it_writes_a_user_turn_and_a_QUEUED_assistant_placeholder(
              self, bound_chat_role, fake_queue):
          make_agent(slug="general")
          call_command("agent_turn", "general", "hello")
          conv = Conversation.objects.get()
          roles = [(t.role, t.state) for t in conv.turns.all()]
          assert roles == [
              (Turn.Role.USER, Turn.State.DONE),
              (Turn.Role.ASSISTANT, Turn.State.QUEUED),
          ]

      def test_the_placeholder_exists_BEFORE_the_enqueue(self, bound_chat_role, monkeypatch):
          """Otherwise a cancel between enqueue and write finds nothing to
          flip, and the hook that exists for exactly that case cannot fire.
          """
          seen = {}

          def _enqueue(kind, payload, **kwargs):
              seen["turns"] = Turn.objects.count()
              return 1

          monkeypatch.setattr("agents.management.commands.agent_turn.enqueue", _enqueue)
          monkeypatch.setattr("agents.management.commands.agent_turn.get_job",
                              lambda job_id: _finished_job())
          make_agent(slug="general")
          call_command("agent_turn", "general", "hello")
          assert seen["turns"] == 2

      def test_the_payload_is_json_safe_and_carries_the_documented_keys(
              self, bound_chat_role, monkeypatch):
          import json

          captured = {}
          monkeypatch.setattr(
              "agents.management.commands.agent_turn.enqueue",
              lambda kind, payload, **kw: (captured.update(kind=kind, payload=payload), 1)[1],
          )
          monkeypatch.setattr("agents.management.commands.agent_turn.get_job",
                              lambda job_id: _finished_job())
          make_agent(slug="general")
          call_command("agent_turn", "general", "hello")
          assert captured["kind"] == "agent.turn"
          assert set(captured["payload"]) == {
              "conversation", "turn", "agent", "text", "connection", "mode",
          }
          assert captured["payload"]["mode"] == "chat"
          json.dumps(captured["payload"])          # JSON-safe: no ORM objects, no paths

      def test_a_conversation_id_continues_an_existing_thread(
              self, bound_chat_role, fake_queue):
          make_agent(slug="general")
          call_command("agent_turn", "general", "first")
          conv = Conversation.objects.get()
          call_command("agent_turn", "general", "second", conversation=str(conv.id))
          assert Conversation.objects.count() == 1
          assert conv.turns.count() == 4

      def test_the_title_comes_from_the_first_user_turn_not_from_a_model(
              self, bound_chat_role, fake_queue):
          """A generated title would be a second, invisible model call per
          conversation."""
          make_agent(slug="general")
          call_command("agent_turn", "general", "what does the library say about attention?")
          assert Conversation.objects.get().title.startswith("what does the library say")


  class TestPollingAndOutput:
      def test_it_prints_the_assistant_text(self, bound_chat_role, fake_queue, capsys):
          make_agent(slug="general")
          call_command("agent_turn", "general", "hello")
          assert "the answer" in capsys.readouterr().out

      def test_it_prints_each_tool_call_with_its_outcome(self, bound_chat_role,
                                                         fake_queue_with_tool, capsys):
          make_agent(slug="general")
          call_command("agent_turn", "general", "hello")
          out = capsys.readouterr().out
          assert "rag.search" in out and "ok" in out

      def test_a_failed_job_prints_the_error_and_exits_non_zero(self, bound_chat_role,
                                                               fake_failed_queue):
          make_agent(slug="general")
          with pytest.raises(CommandError) as exc:
              call_command("agent_turn", "general", "hello")
          assert "boom" in str(exc.value)

      def test_polling_stops_at_the_timeout_and_says_the_job_is_still_running(
              self, bound_chat_role, fake_running_queue):
          """It does NOT cancel the job. The turn may still finish; the
          command simply stops waiting and says where to look."""
          make_agent(slug="general")
          with pytest.raises(CommandError) as exc:
              call_command("agent_turn", "general", "hello", timeout=0)
          assert "still" in str(exc.value).lower()
  ```

- [ ] Add the queue doubles to `agents/tests/_helpers.py`. **They must simulate the handler's own writeback**, not merely return a terminal `JobStatus` — the command's `_report` reads ROWS, so a double that only flips a job state leaves it printing "(no answer)" and every output test passes for the wrong reason:

  ```python
  def _finished_job(state="done", error=""):
      """A `models.queue.backend.JobStatus`-shaped stand-in
      (`backend.py:77-115`). A `SimpleNamespace` with the REAL field names,
      so a test breaks if that shape changes rather than quietly agreeing
      with a double nobody updated."""
      from types import SimpleNamespace

      return SimpleNamespace(
          id=1, kind="agent.turn", state=state, position=None, priority=100,
          created_at=None, started_at=None, finished_at=None, models=[],
          result={}, error=error, progress=None, summary="a turn",
      )


  def _write_assistant(payload, text="the answer"):
      """What `agents.runtime.loop.run_turn` would have written. The
      command reports from rows, so a double that skips this makes every
      output assertion pass against an empty turn."""
      from agents.models import Turn

      Turn.objects.filter(pk=payload["turn"]).update(
          state=Turn.State.DONE, text=text,
      )


  def _write_tool_turn(payload, tool_key="rag.search"):
      """A TOOL turn plus its ToolInvocation, written BEFORE the assistant
      turn is moved to the end -- the same order the real loop uses, so the
      command's `index__lt` filter is exercised rather than sidestepped."""
      from agents.models import ToolInvocation, Turn

      placeholder = Turn.objects.get(pk=payload["turn"])
      conversation = placeholder.conversation
      invocation = ToolInvocation.objects.create(
          principal_kind="resident_agent", principal_key=payload["agent"],
          tool_key=tool_key, args={"query": "q"},
          outcome=ToolInvocation.Outcome.OK, text="two results",
      )
      Turn.objects.create(
          conversation=conversation, index=Turn.next_index(conversation),
          role=Turn.Role.TOOL, text="two results",
          tool_call={"tool": tool_key, "args": {"query": "q"},
                     "agent": payload["agent"], "id": "", "discarded": []},
          artifacts=["document:7"], invocation=invocation, state=Turn.State.DONE,
      )
      placeholder.index = Turn.next_index(conversation)
      placeholder.save(update_fields=["index"])


  def _patch_queue(monkeypatch, *, on_enqueue=None, state="done", error=""):
      module = "agents.management.commands.agent_turn"

      def _enqueue(kind, payload, **kwargs):
          if on_enqueue is not None:
              on_enqueue(payload)
          return 1

      monkeypatch.setattr(f"{module}.enqueue", _enqueue)
      monkeypatch.setattr(f"{module}.get_job", lambda job_id: _finished_job(state, error))


  @pytest.fixture
  def fake_queue(monkeypatch):
      """A turn that runs and answers."""
      _patch_queue(monkeypatch, on_enqueue=_write_assistant)


  @pytest.fixture
  def fake_queue_with_tool(monkeypatch):
      """A turn that calls one tool, then answers."""
      def _run(payload):
          _write_tool_turn(payload)
          _write_assistant(payload)

      _patch_queue(monkeypatch, on_enqueue=_run)


  @pytest.fixture
  def fake_failed_queue(monkeypatch):
      """The handler ran and raised: it wrote its own Turn(FAILED) (spec
      section 6.2 step 8) and the job is failed."""
      def _fail(payload):
          from agents.models import Turn

          Turn.objects.filter(pk=payload["turn"]).update(
              state=Turn.State.FAILED, error="boom",
          )

      _patch_queue(monkeypatch, on_enqueue=_fail, state="failed", error="boom")


  @pytest.fixture
  def fake_running_queue(monkeypatch):
      """A job that never reaches a terminal state, so the command's
      timeout branch is what ends the wait. Nothing is written: the point
      is that the command reports honestly about a turn still in flight
      and does NOT cancel it."""
      _patch_queue(monkeypatch, state="running")
  ```

  Two of these deliberately do the writeback **synchronously inside `enqueue`**: the real worker is a separate process and a test that spawned one would be testing the worker. What is under test here is the command — its preflight, its row writes, its polling, and its reporting — so the double supplies the one thing the command cannot see, which is that the handler ran.

- [ ] Run and read the failure, then create `agents/management/commands/agent_turn.py`:

  ```python
  """`python manage.py agent_turn <agent-slug> "<message>"` -- run one
  agent turn and print what happened.

  P2's proof surface. There is no `/chat/` page until P3, so this is how a
  human drives a turn end to end and how section 12.3's gate is met.

  IT POLLS THE QUEUE, AND THAT IS LEGAL. The never-block rule
  (`agents/contracts/README.md`, and the guard in
  `foundation/ops/tests/test_column_boundaries.py`) governs a TOOL RUNNER
  -- code that executes INSIDE a job and holds the machine's one
  execution slot in sequential mode. This command holds no slot: it stands
  OUTSIDE the queue waiting for it, exactly as a browser poller would. The
  guard's swept list therefore excludes `agents/management/` deliberately,
  and says so.

  IT PREFLIGHTS BEFORE IT ENQUEUES. Section 10.1's "chat role unbound" and
  "bound model cannot call tools" rows have no view to be raised from in
  this phase, so they are raised here -- BEFORE anything is written, so a
  turn that cannot possibly run never becomes a queued job somebody has to
  go and cancel. Three honest states, mirroring
  `tools.vision.services.preflight` (`tools/vision/services.py:150-165`).

  Thin, the same division `run_jobs` draws between itself and `Worker`: it
  owns none of the turn's behaviour.
  """
  from __future__ import annotations

  import time

  from django.core.management.base import BaseCommand, CommandError, CommandParser

  from agents.contracts.tools import Principal, granted_tools
  from agents.models import Agent, Conversation, Turn
  from agents.runtime.loop import _supports_tool_calling
  from models.contracts.queue import QueueUnavailable, enqueue, get_job

  # Longer than a turn's own deadline (`agents/limits.py`), so a turn that
  # runs to its full budget is still waited out rather than reported as
  # stuck by a command that gave up first.
  DEFAULT_TIMEOUT_SECONDS = 960
  POLL_INTERVAL_SECONDS = 1.0
  _TITLE_MAX = 60


  class Command(BaseCommand):
      help = "Run one agent turn from the command line and print the result."

      def add_arguments(self, parser: CommandParser) -> None:
          parser.add_argument("agent", type=str, help="Agent slug (e.g. general).")
          parser.add_argument("message", type=str, help="What to say to it.")
          parser.add_argument("--conversation", dest="conversation", default=None,
                              help="Continue an existing conversation by id.")
          parser.add_argument("--connection", dest="connection", default=None,
                              help="Override the agent's chat model with a "
                                   "ModelConnection pk for this turn.")
          parser.add_argument("--timeout", dest="timeout", type=int,
                              default=DEFAULT_TIMEOUT_SECONDS,
                              help="Seconds to wait before giving up on the job.")

      def handle(self, *args, **options) -> None:
          text = (options["message"] or "").strip()
          if not text:
              raise CommandError("Say something: the message cannot be blank.")

          agent = Agent.objects.filter(slug__iexact=options["agent"], enabled=True).first()
          if agent is None:
              raise CommandError(
                  f"There is no enabled agent called {options['agent']!r}. "
                  f"Run `manage.py sync_agents`, or list what exists with "
                  f"`manage.py shell -c \"from agents.models import Agent; "
                  f"print([a.slug for a in Agent.objects.all()])\"`."
              )

          resolved = self._preflight(agent, options["connection"])
          conversation = self._conversation(agent, options["conversation"], text)
          user_turn, placeholder = self._write_turns(conversation, text)

          payload = {
              "conversation": str(conversation.id),
              "turn": placeholder.pk,
              "agent": agent.slug,
              "text": text,
              # A pk as a STRING, matching both existing precedents
              # (`tools/rag/jobs.py:9-10`, `tools/vision/jobs.py:11-24`).
              "connection": str(options["connection"]) if options["connection"] else None,
              # One legal value in P2; P3 adds "flow" without a payload
              # migration.
              "mode": "chat",
          }
          try:
              job_id = enqueue("agent.turn", payload)
          except QueueUnavailable as exc:
              raise CommandError(
                  f"The execution queue is unavailable, so nothing was queued ({exc}). "
                  f"Run `manage.py migrate` and check the worker is up."
              ) from exc

          self.stdout.write(f"queued job {job_id} for conversation {conversation.id}")
          self._wait(job_id, options["timeout"])
          self._report(placeholder.pk)

      def _preflight(self, agent, connection):
          """Refuse BEFORE the enqueue, with three honest states.

          An unbound chat role and a model that cannot call tools are both
          setup problems an operator can fix; queuing a job that will fail
          on either would replace one clear message with a failed job
          somebody has to open.
          """
          from models.contracts.bindings import resolve
          from models.registry.bindings import resolve_connection_named

          try:
              if connection:
                  resolved, _name = resolve_connection_named(int(connection), "chat")
              else:
                  resolved = resolve(agent.llm_role)
          except (ValueError, TypeError) as exc:
              raise CommandError(
                  f"No chat model is assigned to {agent.llm_role!r} "
                  f"({exc}). Assign one at /inference/ and try again."
              ) from exc

          principal = Principal(
              kind="resident_agent" if agent.resident else "user_agent", key=agent.slug,
          )
          if granted_tools(principal, agent.tool_keys) and (
              _supports_tool_calling(resolved) is False
          ):
              # `False` means the engine REPORTED it. `None` -- it does not
              # report the fact at all -- runs the turn
              # (`models/contracts/engines/base.py:450-458`).
              raise CommandError(
                  f"The model assigned to {agent.llm_role!r} reports that it cannot call "
                  f"tools, and {agent.slug!r} is granted tools. Assign a tool-capable "
                  f"model to that role, or use an agent that needs none."
              )
          return resolved

      def _conversation(self, agent, conversation_id, text):
          if conversation_id:
              conversation = Conversation.objects.filter(id=conversation_id).first()
              if conversation is None:
                  raise CommandError(f"No conversation with id {conversation_id!r}.")
              return conversation
          # Title from the first user turn, truncated. NEVER generated by a
          # model -- that would be a second, invisible model call per
          # conversation.
          return Conversation.objects.create(agent=agent, title=text[:_TITLE_MAX])

      def _write_turns(self, conversation, text):
          """The USER turn and the placeholder ASSISTANT turn, both BEFORE
          the enqueue.

          The placeholder is the durable side-effect that predates the job
          -- the row `on_turn_terminal` exists to fix up. Writing it after
          the enqueue would open a window in which a cancel finds nothing
          to flip.
          """
          user_turn = Turn.objects.create(
              conversation=conversation, index=Turn.next_index(conversation),
              role=Turn.Role.USER, text=text, state=Turn.State.DONE,
          )
          placeholder = Turn.objects.create(
              conversation=conversation, index=Turn.next_index(conversation),
              role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
          )
          return user_turn, placeholder

      def _wait(self, job_id, timeout):
          deadline = time.monotonic() + max(0, timeout)
          while True:
              status = get_job(job_id)
              if status is None:
                  raise CommandError(f"Job {job_id} vanished from the queue.")
              if status.state in ("done", "failed", "cancelled"):
                  if status.state == "failed":
                      raise CommandError(f"The turn failed: {status.error}")
                  if status.state == "cancelled":
                      raise CommandError("The turn was cancelled before it ran.")
                  return status
              if time.monotonic() >= deadline:
                  # Deliberately does NOT cancel: the turn may still finish,
                  # and throwing away work because a CLI stopped watching
                  # would be the command's opinion overriding the queue's.
                  raise CommandError(
                      f"Job {job_id} is still {status.state} after {timeout}s. It has not "
                      f"been cancelled -- watch it at /queue/ or re-run with a longer "
                      f"--timeout."
                  )
              time.sleep(POLL_INTERVAL_SECONDS)

      def _report(self, turn_pk):
          """Print what actually happened: the assistant text, then each
          tool call with its recorded outcome and artifacts. Read from the
          ROWS, not from the job result, so what is printed is what was
          persisted."""
          turn = Turn.objects.select_related("conversation").get(pk=turn_pk)
          conversation = turn.conversation
          self.stdout.write("")
          for tool_turn in conversation.turns.filter(role=Turn.Role.TOOL, index__lt=turn.index):
              call = tool_turn.tool_call or {}
              outcome = tool_turn.invocation.outcome if tool_turn.invocation else "unknown"
              self.stdout.write(f"  [tool] {call.get('tool')} -> {outcome}")
              if call.get("discarded"):
                  self.stdout.write(
                      "         not run: "
                      + ", ".join(d["tool"] for d in call["discarded"])
                  )
              for reference in tool_turn.artifacts or []:
                  self.stdout.write(f"         artifact: {reference}")
          self.stdout.write("")
          self.stdout.write(self.style.SUCCESS(turn.text or "(no answer)"))
          self.stdout.write("")
          self.stdout.write(f"conversation {conversation.id}")
  ```

- [ ] Run green, then drive it for real against the preview stack. **Restart the worker first** — `agent.turn` is job-kind code and `watcher`/`worker` have no reload machinery (`docs/DEV.md:279-290`):

  ```bash
  docker compose restart watcher worker
  .venv/bin/python manage.py sync_agents
  .venv/bin/python manage.py agent_turn library "what does the library say about attention?"
  ```

- [ ] Full matrix, then commit:

  ```bash
  git add agents/management agents/tests/test_agent_turn_command.py agents/tests/_helpers.py
  git commit -m "$(cat <<'EOF'
  feat(agents): manage.py agent_turn -- the P2 proof surface, with a preflight

  No /chat/ page until P3, so this is how a human drives a turn end to end
  and how spec section 12.3's gate is met.

  It PREFLIGHTS BEFORE IT ENQUEUES. Section 10.1's "chat role unbound" and
  "bound model cannot call tools" rows have no view to be raised from in
  this phase; raising them here keeps one clear message in front of the
  person who typed the command instead of producing a queued job somebody
  has to open and cancel. Three honest states, mirroring
  tools.vision.services.preflight. A no-tool agent is deliberately NOT
  refused by the tool-calling gate -- it does not need a tool-capable
  model, and refusing it would be a lie about what it needs.

  It writes BOTH rows before the enqueue: the USER turn and the QUEUED
  placeholder ASSISTANT turn. The placeholder is the durable side-effect
  that predates the job -- the row on_turn_terminal exists to fix up -- so
  writing it after the enqueue would open a window where a cancel finds
  nothing to flip.

  It polls get_job, and that is legal: the never-block rule governs a TOOL
  RUNNER, which executes inside a job and holds the machine's one slot in
  sequential mode. This command holds no slot. Task 14's guard excludes
  agents/management/ deliberately and says why.

  On timeout it does NOT cancel the job -- the turn may still finish, and
  throwing away work because a CLI stopped watching would be the command's
  opinion overriding the queue's.

  It reports from the ROWS, not the job result, so what it prints is what
  was persisted.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 13: retire `ChatSession` / `ChatMessage`, and the `answer_question` seam change

Spec §7.6, and spec §2.3's named "scope addition" — this is the one part of P2 that changes a **public seam**, so it gets its own task and its own ADR amendment.

#### The evidence they are dead, re-verified against the post-P1 tree

| Fact | Where | Verified |
|---|---|---|
| The only production write is `answer_question`'s `session_id` branch | `tools/rag/retrieval.py:649-652`, import at `:116` | yes |
| **There are no reads anywhere** — nothing feeds a prior turn back into a prompt | `retrieval.py`'s own docstring says so; `grep -rn 'ChatMessage.objects\|ChatSession.objects'` finds only the write and the tests | yes |
| The `session_id` that reaches it comes from `tools/rag/jobs.py:246` ← `tools/rag/views.py:1087,1100`, and the Ask template never sends the key | `tools/rag/templates/rag/ask.html` | **re-verify with a grep before deleting** |
| The only shipped caller that can populate it is `manage.py ask --session` | `tools/rag/management/commands/ask.py:31-35,39,55` | yes |
| P1 already refused to depend on it — `rag.ask`'s runner deliberately does not pass it | `tools/rag/tools.py:225-227` | yes |

**A drop, not a data migration.** A live box may hold rows from a `manage.py ask --session` run. Nothing reads them, so nothing loses a feature; the recovery path is the backup every deploy already takes (`foundation/ops/backup.py`), and the drop is recorded in `docs/OPERATIONS.md` (Task 14). Inventing a data migration into `agents.Turn` would fabricate conversations that never had an agent, a tool call, or a depth.

**Files**
- Modify: `tools/rag/models.py` — delete both models **and the now-unused `import uuid` at `:10`** (`grep -n uuid tools/rag/models.py` shows line 290 as its only other use — re-check after deleting)
- Modify: `tools/rag/retrieval.py` — the `session_id` parameter (`:541`), the branch (`:649-652`), the import (`:116`), and the docstrings at `:7-8`, `:547`, `:602-603`
- Modify: `tools/rag/jobs.py` — the payload docstring (`:9`) and the call (`:246`)
- Modify: `tools/rag/views.py` — the `AskView` docstring (`:904`), the read (`:1087`), the payload key (`:1100`)
- Modify: `tools/rag/management/commands/ask.py` — the `--session` flag and its usage line (`:5`, `:30-35`, `:39`, `:55`)
- Modify: `tools/rag/tools.py` — the `run_ask` docstring at `:225-227` (the retirement it predicted has happened)
- Modify: `tools/rag/README.md:105-108`
- Create: `tools/rag/migrations/0013_retire_chat_tables.py`
- Modify: `tools/rag/tests/test_retrieval.py` — delete `:560-589` and the import at `:26`
- Modify: `tools/rag/tests/test_models.py` — delete `:392-416`, the imports at `:15-16`, and the docstring at `:3-4`
- Modify: `docs/adr/0010-model-management-framework.md:277-280` — the amendment note
- Modify: `docs/ROADMAP.md:245-250`

**Interfaces**
- Consumes: nothing new.
- Produces: `tools.rag.retrieval.answer_question(question: str, *, category: str | None = None, answer_resolved: ResolvedModel, embed_resolved: ResolvedModel) -> dict` — **`session_id` is gone, and `question` becomes the only positional parameter.** Every caller updated in this task: `tools/rag/jobs.py:244`, `tools/rag/management/commands/ask.py:54`, `tools/rag/tools.py:249` (already passes no `session_id`), and `tools/rag/views.py`'s synchronous path if one remains — **grep for every call site rather than trusting this list.**

**Steps**

- [ ] **Re-verify the evidence before deleting anything.** A retirement justified by "nothing reads it" must prove that on today's tree, not on the tree the spec was written against:

  ```bash
  grep -rn "ChatSession\|ChatMessage" --include=*.py --include=*.html --include=*.md . \
    | grep -v "\.venv\|docs/superpowers\|llama_index"
  grep -rn "session_id" --include=*.py --include=*.html . | grep -v "\.venv\|docs/superpowers"
  grep -rn "answer_question" --include=*.py . | grep -v "\.venv"
  ```

  Expect the file:line set in the table above and nothing else. **Any hit that is a READ of either model stops this task** — the retirement's whole justification is that there are none. Note that `tools/rag/extract.py:20,57,93` imports a *llama-index* `ChatMessage`, which is an unrelated name; do not touch it.

- [ ] Failing test first — delete the two test classes that pin the models, and add the two that pin their absence. In `tools/rag/tests/test_models.py`:

  ```python
  def test_the_retired_chat_tables_are_gone():
      """Spec section 7.6. Conversation memory lives in `agents.Turn` now,
      which is a richer table (tool_call, data, artifacts, depth) and
      belongs to the agent column, not the RAG one. These names must not
      quietly come back."""
      from django.apps import apps

      names = {model.__name__ for model in apps.get_app_config("rag").get_models()}
      assert "ChatSession" not in names
      assert "ChatMessage" not in names
  ```

  and in `tools/rag/tests/test_retrieval.py`:

  ```python
  def test_answer_question_takes_no_session_id():
      """The retrieval SEAM survives; the parameter does not
      (ADR 0010:277-280, amended). A caller that still passes one is a
      caller that was never updated, and it should fail loudly here."""
      import inspect

      from tools.rag.retrieval import answer_question

      params = inspect.signature(answer_question).parameters
      assert "session_id" not in params
      assert [p for p in params.values()
              if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD] == [params["question"]]
  ```

- [ ] Run them and read the failures:

  ```bash
  .venv/bin/pytest -q tools/rag/tests/test_models.py tools/rag/tests/test_retrieval.py
  ```

- [ ] Delete the two models from `tools/rag/models.py:287-309`, and the `import uuid` at `:10` **only after** confirming line 290 was its only other use.

- [ ] Delete `session_id` from `answer_question`: the parameter (`retrieval.py:541`), the persistence branch (`:649-652`), the `ChatMessage, ChatSession` names from the import at `:116`, and each docstring mention (`:7-8`, `:547`, `:602-603`). The module docstring's replacement sentence should say where conversation memory went, not just that it left:

  ```python
  # tools/rag/retrieval.py, module docstring
  #
  # Conversation memory is NOT this module's business. It was, briefly and
  # write-only, through a `session_id` parameter that wrote ChatSession/
  # ChatMessage rows nothing ever read; both tables were dropped in P2
  # (agents plan, spec section 7.6). Multi-turn memory now lives in
  # `agents.models.Turn`, which records the tool call, its data, its
  # artifacts, and its delegation depth -- none of which a two-column
  # message table could hold. The RETRIEVAL SEAM is unchanged and is still
  # the one ADR 0010:277-280 names: an agent invoking RAG as a tool calls
  # `answer_question`, it does not grow a parallel one.
  ```

- [ ] Delete the payload key at `tools/rag/jobs.py:246` and its docstring mention at `:9`; delete `tools/rag/views.py:1087` and the `"session_id"` entry at `:1100`, and the `AskView` docstring's mention at `:904`. Delete the `--session` argument, its `usage` line, and the local variable in `tools/rag/management/commands/ask.py`.

- [ ] Update `tools/rag/tools.py`'s `run_ask` docstring (`:225-227`) — it currently says the parameter "is retired in P2"; it now says it **was**:

  ```python
      `session_id` is gone. It was retired in P2 along with ChatSession/
      ChatMessage (spec section 7.6); this runner never passed one, which
      is why nothing here changed when it went.
  ```

- [ ] Create `tools/rag/migrations/0013_retire_chat_tables.py`:

  ```python
  """Drop the two write-only chat tables (agents spec section 7.6).

  A DROP, not a data migration. Nothing ever READ these rows -- the only
  production writer was `answer_question`'s `session_id` branch, and the
  only shipped way to reach it was `manage.py ask --session` -- so nothing
  loses a feature. A live box may hold rows from such a run; the recovery
  path is the backup every deploy already takes
  (`foundation/ops/backup.py`), and this drop is recorded in
  docs/OPERATIONS.md.

  Inventing a data migration into `agents.Turn` was considered and
  rejected: it would fabricate conversations that never had an agent, a
  tool call, or a depth, and a fabricated history is worse than none.

  ChatMessage first: it holds the FK.
  """
  from django.db import migrations


  class Migration(migrations.Migration):

      dependencies = [("rag", "0012_ragsettings_hybrid_search")]

      operations = [
          migrations.DeleteModel(name="ChatMessage"),
          migrations.DeleteModel(name="ChatSession"),
      ]
  ```

  Generate it with `makemigrations` and then **compare against the above** rather than hand-writing it; confirm the dependency really is `0012_ragsettings_hybrid_search` (`ls tools/rag/migrations/`).

- [ ] Amend `docs/adr/0010-model-management-framework.md:277-280`. The bullet currently reads "`modules/rag/retrieval.py::answer_question` — with its `session_id`-keyed grounding — is the retrieval path". **Two things are wrong with it now**: the path is pre-P0 (`modules/rag/`), and the parameter is gone. Replace the bullet and append a dated note:

  ```markdown
  - **RAG-as-tool reuses the existing retrieval seam.**
    `tools/rag/retrieval.py::answer_question` is the retrieval path; an
    agent invoking RAG as a tool calls into that seam, it does not grow a
    parallel one.

  > **Amendment (2026-08-27, agents P2).** This bullet originally named
  > `modules/rag/retrieval.py::answer_question` "with its `session_id`-keyed
  > grounding". Two corrections. The path moved in the P0 regroup:
  > `modules/rag/` is now `tools/rag/`. And the `session_id` parameter was
  > deleted along with the `ChatSession`/`ChatMessage` tables it wrote to,
  > which nothing ever read. **The seam survives; the parameter does not.**
  > Conversation memory now lives in `agents.models.Turn`, which records the
  > tool call, its arguments, its data, its artifacts, and its delegation
  > depth — none of which a two-column message table could hold — and which
  > belongs to the agent column rather than the RAG one. `rag.search` and
  > `rag.ask` (P1) call `retrieve_nodes` and `answer_question` respectively,
  > exactly as this bullet requires.
  ```

- [ ] Rewrite `docs/ROADMAP.md:245-250`'s "v1 — grounded conversation" bullet, which plans to reuse the retired tables. Replace the `ChatSession`/`ChatMessage` sentence with what actually shipped, and keep the bullet honest about what has and has not landed. Update `tools/rag/README.md:105-108`'s `answer_question` signature line in the same pass.

- [ ] Run green, then prove the migration **applies and reverses** — a table drop that cannot be reversed is one nobody can back out of. `DeleteModel` reverses to `CreateModel`, so this really does round-trip:

  ```bash
  .venv/bin/pytest -q tools/rag
  .venv/bin/python manage.py migrate rag
  .venv/bin/python manage.py migrate rag 0012
  .venv/bin/python manage.py migrate rag
  .venv/bin/python manage.py makemigrations --check --dry-run
  ```

- [ ] Prove the CLI still works without its flag, and that the flag is gone rather than silently ignored:

  ```bash
  .venv/bin/python manage.py ask "what does the library say about attention?" || true
  .venv/bin/python manage.py ask "x" --session abc    # must FAIL with "unrecognized arguments"
  ```

- [ ] Full matrix, then commit:

  ```bash
  git add tools/rag docs/adr/0010-model-management-framework.md docs/ROADMAP.md
  git commit -m "$(cat <<'EOF'
  refactor(rag)!: retire ChatSession/ChatMessage and the session_id seam

  Spec sections 2.3 and 7.6. These two tables were WRITE-ONLY: the single
  production writer was answer_question's session_id branch, the only
  shipped way to reach it was `manage.py ask --session`, and nothing in
  the codebase ever read a row back. Re-verified by grep against today's
  tree before anything was deleted.

  A DROP, not a data migration. A live box may hold rows from such a run;
  nothing loses a feature, the recovery path is the backup every deploy
  already takes, and the drop is recorded in docs/OPERATIONS.md.
  Migrating them into agents.Turn was considered and rejected: it would
  fabricate conversations that never had an agent, a tool call, or a
  depth, and a fabricated history is worse than none.

  BREAKING (internal): answer_question loses its session_id parameter, so
  `question` is now its only positional one. Every caller updated. The
  RETRIEVAL SEAM itself is unchanged and is still the one ADR 0010:277-280
  names -- that bullet is amended for both the parameter and the pre-P0
  path it still cited.

  Conversation memory now lives in agents.models.Turn, which records the
  tool call, its arguments, its data, its artifacts, and its delegation
  depth -- none of which a two-column message table could hold -- and
  belongs to the agent column rather than the RAG one. ROADMAP's "v1 --
  grounded conversation" bullet, which planned to reuse these tables, is
  rewritten.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 14: structural guards, docs, the gate matrix, and the ladder

Four permanent guards, the doc sweep, and the rungs. **Every guard carries an anti-vacuous pin**, so a stale module list or a broken probe fails loudly instead of passing by looking at nothing — the shape P1 Task 10 established.

**Files**
- Modify: `foundation/ops/tests/test_import_law.py` — rule 3 for `agents/`
- Modify: `foundation/ops/tests/test_column_boundaries.py` — sweep `agents/runtime/`, widen the module-scope allow-list
- Modify: `models/registry/tests/test_registry_paths.py` — raise the anti-vacuous floor
- Modify: `agents/README.md`, `agents/contracts/README.md`
- Create: `agents/runtime/README.md`
- Modify: `docs/DEV.md`, `docs/ROADMAP.md`, `docs/OPERATIONS.md`

**Interfaces**
- Consumes: `agents.contracts.tools.all_tools`; `models.contracts.jobkinds.all_job_kinds`/`resolve_dotted_path`; `models.contracts.roles.all_roles`; `ast`, `subprocess`.
- Produces: no importable API.

**Steps**

- [ ] **Guard 1 — import-law rule 3: `agents/` never imports `tools/`.** Add to `foundation/ops/tests/test_import_law.py`, which already sweeps the `agents` column for rule 2 (`test_import_law.py:66`, `_SCANNED_COLUMNS`):

  ```python
  def test_no_agents_module_imports_a_tools_package():
      """Import-law rule 3's third clause. `agents/runtime` reaches a
      tool's implementation ONLY through a dotted-path STRING resolved at
      call time by `models.contracts.jobkinds.resolve_dotted_path` -- the
      same mechanism an AppConfig.ready() already uses to register a job
      kind without importing its handler.

      This is what makes the dependency direction one-way and acyclic:
      `tools/*` imports `agents.contracts` (a rule-1 pure leaf), and
      `agents/*` imports nothing of `tools/` at all. NOT just at module
      scope -- a lazy in-body `from tools.rag import retrieval` would
      still be a cross-column dependency, just a later one, so this walks
      EVERY import node in the file rather than only `tree.body`.
      """
      out = subprocess.run(["git", "ls-files", "--", "agents"], cwd=REPO_ROOT,
                           capture_output=True, text=True, check=True)
      offenders = {}
      for relative in out.stdout.splitlines():
          if not relative.endswith(".py") or _is_test_file(relative):
              continue
          tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
          for node in ast.walk(tree):
              names = (
                  [a.name for a in node.names] if isinstance(node, ast.Import)
                  else [node.module] if isinstance(node, ast.ImportFrom) and node.module
                  else []
              )
              for name in names:
                  if name == "tools" or name.startswith("tools."):
                      offenders.setdefault(relative, []).append(name)
      assert offenders == {}, offenders


  def test_the_tools_gate_would_actually_catch_a_lazy_import():
      """Anti-vacuous pin: an in-BODY import must be caught too, or the
      guard only enforces a style rule instead of a dependency rule."""
      lazy = "def f():\n    from tools.rag import retrieval\n    return retrieval\n"
      found = [
          node.module for node in ast.walk(ast.parse(lazy))
          if isinstance(node, ast.ImportFrom) and node.module
          and node.module.startswith("tools.")
      ]
      assert found == ["tools.rag"]


  def test_agents_reaches_models_registry_through_bindings_and_nothing_else():
      """Rule 2's single sanctioned exception, pinned as a CLOSED set. The
      existing gate forbids `models.registry.models`/`.views` to everyone;
      this one says that for `agents/`, `bindings` is the ONLY submodule
      of that package it may touch at all."""
      out = subprocess.run(["git", "ls-files", "--", "agents"], cwd=REPO_ROOT,
                           capture_output=True, text=True, check=True)
      offenders = {}
      for relative in out.stdout.splitlines():
          if not relative.endswith(".py") or _is_test_file(relative):
              continue
          tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
          for node in ast.walk(tree):
              module = (
                  node.module if isinstance(node, ast.ImportFrom) and node.module
                  else node.names[0].name if isinstance(node, ast.Import) else None
              )
              if module and module.startswith("models.registry.") \
                      and module != "models.registry.bindings":
                  offenders.setdefault(relative, []).append(module)
      assert offenders == {}, offenders
  ```

- [ ] **Guard 2 — no runtime module blocks on a queue job.** Extend `foundation/ops/tests/test_column_boundaries.py`. `TOOL_MODULES` (`:184-188`) already lists the three `tools.py` modules; add the runtime package **as a second, separately-named list**, so the exclusion below is visible rather than implied:

  ```python
  # The turn runtime. Swept for the same `get_job` rule as TOOL_MODULES:
  # a runner and a loop both execute INSIDE a job and hold the machine's
  # one execution slot in sequential mode.
  RUNTIME_MODULES = (
      "agents/runtime/prompt.py",
      "agents/runtime/invoke.py",
      "agents/runtime/loop.py",
      "agents/runtime/jobs.py",
      "agents/runtime/delegate.py",
  )

  # DELIBERATELY NOT SWEPT: `agents/management/commands/agent_turn.py`
  # polls `get_job`, and that is legal. This rule governs code that runs
  # INSIDE a job and holds the machine's one slot; a management command
  # holds no slot -- it stands OUTSIDE the queue waiting for it, exactly as
  # a browser poller would. Naming the exclusion here is the point: an
  # absent path would read as an oversight.
  ```

  and widen the sweep and the anti-vacuous pin:

  ```python
  def test_no_runtime_module_blocks_on_a_queue_job():
      offenders = {}
      for relative in RUNTIME_MODULES:
          text = _code_only_text(relative)
          hits = [needle for needle in _FORBIDDEN_IN_A_RUNNER if needle in text]
          if hits:
              offenders[relative] = hits
      assert offenders == {}, offenders


  def test_the_management_command_really_does_poll_and_is_really_excluded():
      """Anti-vacuous pin on the exclusion above: if `agent_turn.py` ever
      stops polling, this exclusion is dead weight and should be deleted
      rather than left as a standing carve-out nobody re-examines."""
      text = (REPO_ROOT / "agents/management/commands/agent_turn.py").read_text()
      assert "get_job" in text
      assert "agents/management/commands/agent_turn.py" not in RUNTIME_MODULES
  ```

  **`test_every_registered_runner_lives_in_a_swept_module` (`:281`) must now pass too** — the `agent.<slug>` specs run `agents.runtime.delegate.run_agent_tool`, so that walk's `swept` set has to be built from `TOOL_MODULES + RUNTIME_MODULES`. Update it, and confirm it goes red first by temporarily leaving `delegate.py` out.

- [ ] **Guard 3 — the module-scope import guard is left exactly as P1 wrote it.** `_is_allowed_registration_import` (`foundation/ops/tests/test_column_boundaries.py:290-314`) and `test_no_tool_module_imports_its_service_layer_at_module_scope` (`:315`) iterate **`TOOL_MODULES` only**, and that is correct: the property they protect is that `AppConfig.ready()` — which imports each `tools.py` to register its specs — pulls in no service layer. `agents/apps.py::ready()` imports `agents.contracts.tools` and `agents.resident` and nothing else; it never imports `agents/runtime/`, and a subprocess check in Task 11 already pins that. So `RUNTIME_MODULES` is swept for the `get_job` rule and **not** for the module-scope rule, the allow-list needs no widening, and `agents/runtime/delegate.py` is free to import `agents.models` and `agents.runtime.loop` at module scope — which is what m12/m13 asked for and what makes that file readable.

  Add one line to `test_the_stdlib_carve_out_does_not_hide_a_real_violation` (`:339`) so the untouched allow-list is still pinned as a closed set:

  ```python
      assert not _is_allowed_registration_import("agents.models")
      assert not _is_allowed_registration_import("agents.runtime.loop")
  ```

- [ ] **Guard 4 — every dotted path still resolves.** `models/registry/tests/test_registry_paths.py` walks every `JobKind`'s `planner`/`handler`/`summarizer`/`on_terminal`, every `RoleSpec.rematerialize`, and every `ToolSpec.runner` through `resolve_dotted_path`. P2 adds four (`agent.turn`'s) plus two (`agent.library`, `agent.illustrator`). **Raise the anti-vacuous floor from P1's `>= 20` and state what makes up the number in a comment**:

  ```python
  # P1 measured 20: 14 pre-existing registrations plus the six v1 tool
  # runners. P2 adds agent.turn's four (planner, handler, summarizer,
  # on_terminal) and the two agent-as-tool runners. The floor is a FLOOR,
  # not an equality -- a phase that adds a registration should not have to
  # edit this line -- but it must move up when a phase adds six, or it
  # stops proving the walk saw anything new.
  assert len(paths) >= 26, paths
  ```

  **Measure the real count first** (`manage.py shell` printing the walk's length) rather than trusting the arithmetic; if it differs from 26, use the measurement and say why in the comment.

- [ ] **Docs.** Five files, each with one job:

  1. **`agents/README.md`** — rewrite the table: `contracts/` (P1, unchanged), `models.py` + `runtime/` + `resident.py` + `limits.py` (**P2 — shipped**), `chat/` (P3). Add a short "How a turn runs" section — enqueue → plan → loop → tool → turn rows — and a "What is audited" line naming `ToolInvocation`. Extend the existing "two structural rules" section to **four**: no `tools/` import; no queue block; `ready()` touches no database; a resident row is a projection of code.
  2. **`agents/contracts/README.md`** — the "Principals and grants" section from Task 4, plus a new "The invocation log" section: every call writes a `ToolInvocation` keyed on a **principal**, a `Turn` merely references one, and that is what lets an external MCP `tools/call` reuse the row (addendum consequence 3). Add `mcp_tool_dict` to the `toolschema.py` bullet with the one-builder rule.
  3. **`agents/runtime/README.md`** (new) — the four modules, the loop's five endings, the recovery table from Task 7, and the post-deadline-latency note. This is where a reader who has to debug a turn should start.
  4. **`docs/DEV.md`** — no `testpaths` change (it already reads `tools models foundation agents scripts`), but four edits: add `manage.py sync_agents` to the deploy/after-migrate steps; add `agent.turn` to the restart-rules paragraph's list of job-kind code (`DEV.md:279-290`); add a short "Driving an agent turn from the CLI" block showing `agent_turn` with `--conversation`; and **fix the stale module path at `DEV.md:181-186`**, which still tells a reader that every test mocks `core.inference.gateway.get_llm`/`get_llm_for`/`get_embed_model`/`get_embed_model_for`. `core/` has not existed since P0 — the seam is `models.contracts.gateway`. That paragraph is the one place a newcomer is told how the suite stays offline, and this phase leans on it harder than any before it (`patch_llm` patches exactly that seam), so a wrong path there is a wrong instruction, not a typo.
  5. **`docs/ROADMAP.md`** — Phase 1.6's bullets updated to what shipped, and **three new phase lines in order**, straight from the 2026-08-27 addendum:

     ```markdown
     - [ ] **Identity & Auth** — principals and per-principal grants. `granted_tools`
       already takes a `Principal`; this is where grants stop living on the agent row and
       start living in their own table, and where `/inference/`'s unauthenticated mutation
       endpoints close (ADR 0010's standing gap).
     - [ ] **MCP edge** — the same in-process tool registry, exposed to EXTERNAL agents over
       HTTP on the existing web app: `tools/list` and `tools/call`, gated behind Identity &
       Auth. Not a separate server, and not a protocol in the middle of an internal call —
       an internal tool call stays a Python function call. `mcp_tool_dict` (P2) is already
       the wire adapter. The import direction is also in scope: this platform as an MCP
       *client* of other servers, with a per-server allowlist of which of their tools may be
       registered here.
     - [ ] **Tenancy** — visibility scopes on documents and categories, applied at the one
       retrieval filter point (`tools/rag/retrieval.py::retrieve_nodes`) rather than in each
       tool runner.
     ```
  6. **`docs/OPERATIONS.md`** — two additions: `manage.py sync_agents` in the deploy sequence after `migrate`, and a note that P2 **dropped** the `rag_chatsession` / `rag_chatmessage` tables (write-only, never read; recovery is the backup this document already describes).

- [ ] **The full gate matrix.** Every one of these, in order, all green:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'

  # Gate 1 -- both flag states.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q

  # Gate 2 -- both collection orders. Any DIFFERENCE in collected count
  # between them is registration leakage (a module-global registry
  # surviving between tests) and must be chased, not accepted.
  .venv/bin/pytest -q                                          # configured
  .venv/bin/pytest -q scripts agents foundation models tools    # reversed

  # Gate 3 -- the collected count is UP from Task 1's baseline by exactly
  # the sum of what each task's own per-file run reported. Never a
  # hand-computed total.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q --collect-only | tail -1

  # Gate 4 -- exactly two migrations this phase, and no third pending.
  .venv/bin/python manage.py makemigrations --check --dry-run
  .venv/bin/python manage.py migrate

  # Gate 5 -- Django is happy and nothing shadows the stdlib.
  .venv/bin/python manage.py check
  .venv/bin/python manage.py collectstatic --noinput --dry-run

  # Gate 6 -- the pure leaf is still pure, with Principal in it.
  .venv/bin/pytest -q agents/contracts/tests/test_purity.py

  # Gate 7 -- no conftest.py has appeared.
  find . -name conftest.py -not -path './.venv/*'      # must print nothing

  # Gate 8 -- the retired names are really gone from production code.
  grep -rn "ChatSession\|ChatMessage" --include=*.py . \
    | grep -v "\.venv\|llama_index\|tools/rag/extract.py\|docs/superpowers"
  ```

- [ ] **Restart the worker.** `docs/DEV.md:279-290`: `watcher` and `worker` run single management commands with no reload machinery, and this phase adds a job kind, a role, two tool specs, and four models. Forgetting this is the single most common way a change appears not to work when it does:

  ```bash
  docker compose restart watcher worker
  .venv/bin/python manage.py migrate
  .venv/bin/python manage.py sync_agents
  ```

- [ ] **The Smoke Checklist**, at Rung 2 (this branch's preview stack) and again at Rung 3 (live). Each step names what it proves:

  1. **`/setup/`** renders every registered engine's install guide and its live reachability check. *(Proves the new `chat.converse` role and the `agents` app broke no startup path, and that `foundation.setup`'s read-only surface over the registries still works.)*
  2. **`/inference/`** renders the model console **and now lists `chat.converse` among the roles**, with a picker. *(This is ROADMAP:236-241's "zero framework changes" claim, checked with eyes: nothing in the console was edited this phase.)* Assign a tool-capable chat model to it.
  3. **`/rag/`** loads, an ask returns an answer with citations, and an upload goes PENDING → READY. *(Proves the `answer_question` signature change and the table drop broke neither the page nor the queued path.)*
  4. **`/vision/`** renders its operation chooser and one generation completes. *(Proves nothing in P2 disturbed the VISION-OWNED tools it now calls through the registry.)*
  5. **`/queue/`** lists the jobs from steps 3–4 **plus the `agent.turn` jobs from step 7**, each with its one-line summary. *(Proves `summarize_turn` renders without a query and without raising.)*
  6. **`manage.py sync_agents`** prints `3 created` on a fresh box and `0 created, 3 updated` on a second run. *(Idempotence, on a real database.)*
  7. **The live proof rung** — one command, and the whole phase either works or does not:

     ```bash
     .venv/bin/python manage.py agent_turn general \
       "What does the library say about attention, and make me a small watercolor of a lighthouse"
     ```

     **Expected:** at least one `[tool] rag.*` line and one `[tool] vision.generate` line, each `-> ok`; an `artifact: output:<id>` line under the vision call; then an answer that refers to both. Open the artifact at `/vision/` and confirm the image exists. *(This is §12.3's gate and the owner's own worked example: a single turn that reaches two different columns' tools through one registry.)*
  8. **Cancel a turn from `/queue/`** while it is queued, then look at the conversation: the assistant turn is `cancelled`, not stuck at `queued`. *(Proves `on_turn_terminal` fires on the cancel path — §12.3's second gate.)*
  9. **`manage.py agent_turn library "..."`** with the `chat.converse` role **unbound**: refused before enqueue, with a message naming the role, and **no rows written**. Rebind afterwards. *(Proves the preflight, and §10.1's first row.)*

- [ ] **Rung 4 — fresh pixels on the owner's live system.** Only after Rung 4 may any of this be described as done. Until then: no "works", no "complete", no "fixed".

- [ ] Commit:

  ```bash
  git add foundation/ops/tests models/registry/tests/test_registry_paths.py \
          agents/README.md agents/contracts/README.md agents/runtime/README.md \
          docs/DEV.md docs/ROADMAP.md docs/OPERATIONS.md
  git commit -m "$(cat <<'EOF'
  test(agents): permanent structural guards for the turn runtime, plus docs

  Four guards, each with an anti-vacuous pin and each watched to bite:

  - agents/ imports no tools/ package, at module scope OR lazily in a
    body. Rule 3's third clause is a DEPENDENCY rule, not a style rule, so
    the walk covers every import node rather than only tree.body. This is
    what keeps the direction one-way: tools/* imports agents.contracts (a
    pure leaf); agents/* imports nothing of tools/ at all.
  - agents/ reaches models.registry through `bindings` and nothing else,
    pinned as a closed set rather than inferred from the existing
    forbid-list.
  - No runtime module blocks on a queue job. agents/management/ is
    DELIBERATELY excluded and the exclusion is named with its reason and
    its own pin: the rule governs code that runs inside a job and holds
    the machine's one slot, and a command holds none. An absent path would
    have read as an oversight.
  - Every registered dotted path still resolves; the anti-vacuous floor
    moved from 20 to the measured post-P2 count, with a comment saying
    what makes it up.

  The module-scope allow-list gains agents.limits and agents.resident --
  both pure, both Django-free by their own tests -- because delegate.py is
  now swept and legitimately imports them. agents.models and
  agents.runtime.loop are still refused, and a test says so.

  Docs: agents/README.md now describes how a turn runs and what is
  audited; agents/contracts/README.md gains Principals-and-grants and The
  invocation log; a new agents/runtime/README.md is where somebody
  debugging a turn should start. DEV.md gains sync_agents and agent.turn's
  restart rule; OPERATIONS.md records the dropped chat tables; ROADMAP
  gains the three addendum phases in order -- Identity & Auth, MCP edge,
  tenancy.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

## Coverage against the spec

| Spec item | Task |
|---|---|
| §6.1 `agent.turn` job kind registered in `ready()`, `default_priority=100`, `on_terminal` required | 9 |
| §6.1 payload shape (JSON-safe, connection pk as a string, `mode`) | 9 (documented), 12 (produced) |
| §6.1 `plan_turn` — chat role + union of tool roles, three tolerant drops, `exclusive=True`, no footprints | 9 |
| §6.1 delegation closure walked by the planner, deduped, depth-bounded | 9, 11 |
| §6.2 step 1 — `StepBudget` from `Agent.max_steps` + `TURN_DEADLINE_SECONDS` | 2 (limits), 8 |
| §6.2 step 2 — history → messages; a TOOL turn as exactly two messages; wire name in `tool_name`; `tool_call_id` reserved | 6 |
| §6.2 step 2 — only `depth=0` turns replay; a delegate's turns are audited but not replayed | 6, 11 |
| §6.2 step 2 — `HISTORY_TURNS` a fixed cap, not a token budget | 2, 6 |
| §6.2 step 3 — `resolve_connection_named` returns a tuple; never `get_llm()`-by-default | 8 |
| §6.2 step 4 — tool schemas built from the SAME filter the planner applied; `_TOOLS.get`, never `get_tool`, for an absent key | 4, 8, 9 |
| §6.2 step 5 — progress each iteration; take `calls[0]`, record discards; tool turn written; budget spent | 8 |
| §6.2 step 6 — honest final on exhaustion | 8 |
| §6.2 step 7 — assistant turn with accumulated artifacts | 8 |
| §6.2 step 8 — `run_turn` writes its own FAILED and re-raises; `on_terminal` proven not to fire there | 8, 9 |
| §6.3 tools run inline; a runner may enqueue but never wait | 8 (docstring), 14 (guard) |
| §6.4 agent-as-tool, `MAX_AGENT_DEPTH`, shared budget, depth by omission, recursion guard | 11 |
| §6.5 flows | **P3 — explicitly out of scope** (stated in Global Constraints) |
| §7.1 `Agent` + ruling R1's asymmetric `tool_keys` validation | 3 |
| §7.2 `Conversation` (UUID pk, PROTECT, title never model-generated) | 3, 12 |
| §7.3 `Turn`, the five `tool_call` keys, `queue_job_id` not an FK | 3, 8 |
| §7.4 `Flow` | **P3 — out of scope** |
| §7.5 resident agents, `sync_resident_agents`, `manage.py sync_agents`, tolerant unregistered keys | 10 |
| §7.6 `ChatSession`/`ChatMessage` retirement, the migration, the `answer_question` signature, ADR 0010 amendment, ROADMAP rewrite | 13 |
| §8.5 `chat.converse` role registered, capability `chat`, `rematerialize=None` | 2 |
| §8.1–§8.4 the `/chat/` surface | **P3 — out of scope** |
| §10.1 blank message → refused | 12 |
| §10.1 chat role unbound → refused before enqueue | 12 |
| §10.1 bound model cannot call tools → refused before enqueue; honest turn if reached anyway | 12, 8 |
| §10.1 `QueueUnavailable` → operator copy, nothing queued | 12 |
| §10.1 `ParamError` / raise / refusal → tool card, retry policy | 7, 8 |
| §10.1 budget or deadline exhausted → honest final | 8 |
| §10.1 `run_turn` raises → own writeback, `on_terminal` does not fire | 8, 9 |
| §10.1 cancel / double-orphan → `on_turn_terminal` | 9 |
| §10.2 the one-recovery rule, and every failed call spending a step | 7 (table), 8 |
| §10.3 `on_turn_terminal` as one conditional UPDATE; unknown tool name is a tool error | 9, 7 |
| §10.4 progress with a named unit; `checkpoint()` deliberately unused | 8 |
| §11.1 no `conftest.py`; per-package `_helpers.py`; helpers duplicated per app (the four-copy ruling) | Global Constraints; 1, 4, 6 |
| §11.1 the vision-flag rule | Global Constraints; 10, 12 |
| §11.2 `FakeToolLLM` at the gateway seam; `StubTool` via a snapshot/restore fixture; `make_agent`/`make_conversation`/`make_turn` | 6, 3 |
| §11.3 loop tests (no-tool, one-tool, three-calls-one-runs, replay, exhaustion, deadline, retry, unknown name, depth, cycle) | 8, 11 |
| §11.3 planner tests (union, drop, exclusive) | 9 |
| §11.3 job-wiring tests (self-writeback, `handler_started`, `on_turn_terminal` states, summarizer never queries) | 8, 9 |
| §11.3 resident-sync tests (idempotent, tolerant, typo still fails) | 10 |
| §11.3 structural guards (every dotted path resolves; no runner blocks on a job) | 14 |
| §11.4 both flag states, both collection orders, private DB on 5433 | Global Constraints; 14 |
| §12.3 content — models, migrations, `apps.py`, `jobs.py`, `loop.py`, `resident.py`, `sync_agents`, agent-as-tool, the retirement | 1–14 |
| §12.3 gate — `agent.turn` runs end to end from a shell | 12, 14 |
| §12.3 gate — `on_turn_terminal` proven against a cancel and a double-orphan | 9 (unit), 14 (browser cancel) |
| §12.3 gate — the retirement migration applies and reverses cleanly | 13 |
| §12.3 gate — suite green both ways; worker restarted before any smoke test | 14 |
| Corrections §1 — `testpaths` moves with the directory it names | Global Constraints (no change needed this phase) |
| Corrections §4 — registration stays lazy; the AST guard over module-scope imports | 14 |
| **Addendum 1** — grants attach to a principal; `granted_tools`; `ToolContext.principal` | 4 |
| **Addendum 2** — retrieval keeps ONE filter point; runners pass the principal through | 4 (principal threaded), 13 (seam preserved); **no runner change needed** |
| **Addendum 3** — `ToolInvocation` is its own row, referenced by `Turn` | 3, 7 |
| **Addendum 4** — `mutates: bool` kept, `scopes` named as its generalization | Addendum text; no code change in P2 (stated in Task 4) |
| **Addendum 5** — `agents/contracts` stays transport-agnostic; adapters beside it | 5 |
| **Addendum 6** — roadmap phases: Identity & Auth → MCP edge → tenancy | 14 |
| **Addendum P2 deliverable** — `mcp_tool_dict` + drift pin | 5 |
| P1 deferred — four copies of the snapshot/restore helpers stay four | Global Constraints (ruling), 1 |
| P1 deferred — vision double preflight, `run_generate` spec rebuild | **VISION-OWNED, noted not planned** |
| P1 deferred — the tolerant `tool_keys` path | 3, 4, 10 |
| P1 deferred — post-deadline latency documented | 8 |
| P1 deferred — the except-ordering rule, `agents/contracts/README.md` as authority | 7 |
| Round-1 review — one `resolve_chat` for planner and loop | 8, 9 |
| Round-1 review — `docs/DEV.md:181-186`'s stale `core.inference.gateway` path | 14 |

## Self-review

**Placeholder scan.** No step in this plan says "similar to Task N", "as above", "and so on", "TODO", or "etc." in place of code. Every module that ships is written out in full; every test class names the behaviour it pins and why that behaviour is the right one. The three places that deliberately do **not** contain code are marked as such and are all out of scope: flows (P3), the `/chat/` surface (P3), and the two VISION-OWNED items.

**Name and signature consistency, checked across tasks.**

| Name | Declared | Consumed by |
|---|---|---|
| `Principal(kind, key)` | 4 | 8, 9, 11, 12 |
| `granted_tools(principal, tool_keys)` | 4 | 8 (`_available_tools`), 9 (`_tool_roles`), 12 (preflight) |
| `ToolContext(conversation_id, principal, depth, budget, job, tool_key="")` | 4 | 7 (sets `tool_key`), 8, 11 |
| `mcp_tool_dict(spec)` / `_input_schema(spec)` | 5 | 5 only (the edge is a later phase) |
| `build_messages(agent, conversation, *, user_text="", before_index=None)` | 6 | 8 |
| `history_messages(...)` — filters `state="done"` **and `depth=0`** | 6 | 6, 8, 11 (the isolation test) |
| `resolve_chat(agent, connection)` | 8 (`agents/runtime/bindings.py`) | 8 (`run_turn`), 9 (`plan_turn`) — **one rule, one implementation** |
| `bind_chat_role(role_key, ...)` + the `bound_chat_role`/`bound_embed_role` fixtures | 6 (`agents/tests/_helpers.py`) | 8, 9, 11 — re-exported through `agents/runtime/tests/_helpers.py` (same app) |
| `tool_turn_messages(turn)` | 6 | 6 (`history_messages`), 8 (live append) — **the same function both times, which is the point** |
| `ToolOutcome(outcome, text, args, result, invocation_id)` | 7 | 8 |
| `invoke_tool(spec, args, tool_ctx)` / `invoke_unknown_tool(wire, args, tool_ctx)` | 7 | 8 |
| `run_turn(payload, models, ctx)` | 8 | 9 (as the `handler` dotted-path string, never imported) |
| `run_loop(...) -> LoopResult` | 11 (extracted) | 8 (`_run_turn`), 11 (`run_agent_tool`) |
| `_available_tools(principal, agent)` | 8 | 8, 11 |
| `_supports_tool_calling(resolved)` | 8 | 8, 12; patched by `patch_llm` because the real one makes a live HTTP call |
| `_finished_job` + `fake_queue` / `fake_queue_with_tool` / `fake_failed_queue` / `fake_running_queue` | 12 | 12 |
| `plan_turn` / `summarize_turn` / `on_turn_terminal` | 9 | `apps.py` (as strings) |
| `AGENT_TOOL_PREFIX` / `agent_tool_specs()` | 11 | 9 (planner walk), `apps.py` |
| `sync_resident_agents(AgentModel)` | 10 | `manage.py sync_agents` only (D5) |
| `Agent.save(*args, _from_resident_sync=False, **kwargs)` | 3 | 10 only |
| `Turn.next_index(conversation)` | 3 | 8, 12 |

**Three things a reviewer should push on, named rather than hidden.**

1. **`ToolContext.tool_key` is a contract addition forced by deviation D4.** If the executor prefers spec §6.4's single `agent.delegate` tool with an `agent` param, the field becomes unnecessary and Task 11 shrinks. It is kept because per-agent tool names are what the model actually reads, and a tool named `agent.library` with a one-line description is a better prompt than a generic delegate with a free-text agent argument the model has to guess at.
2. **The assistant turn is re-indexed at the end of a turn**, leaving a gap. The alternative — not pre-creating the placeholder — would remove `on_turn_terminal`'s reason to exist, which spec §6.1 calls out as required rather than optional.
3. **`degraded` is a defined-and-tested outcome class that no shipped runner produces yet.** Two natural producers exist (`rag.ingest` when the queue is down, `vision.generate` when a job is still running at the deadline) and both are outside this plan's file scope. That is stated in Task 7 rather than left for someone to discover as a dead column.


---

## Plan review

### Round 1 — AMEND (7 MAJOR / 18 minor). Author applied all findings.

**MAJOR**

| # | Finding | Applied in |
|---|---|---|
| M1 | Task 8's `_setup` and Task 11's `run_agent_tool` tests would have failed on the first run: `resolve_chat` and every delegate call reach `models.contracts.bindings.resolve`, which RAISES when no binding exists, and `patch_llm` only replaced the gateway. | `bind_chat_role` + the `bound_chat_role`/`bound_embed_role` fixtures moved into `agents/tests/_helpers.py` (Task 6) and are re-exported through the runtime helpers, so Tasks 8, 9, and 11 share one real `ModelConnection`+`RoleBinding` builder. `patch_llm` still patches only `get_llm_for` (plus `_supports_tool_calling`, see m9). `resolve` is explicitly **not** patched: it is code under test. |
| M2 | A fixture defined in `_helpers.py` is invisible to pytest — there is no `conftest.py`, so nothing collects it. Every fixture this plan names would have failed with `fixture 'x' not found`. | Stated once in Global Constraints as its own rule ("the import IS the registration"), and every test module that uses one now carries an explicit `from ... import <fixture>  # noqa: F401`. |
| M3 | Task 12's four queue fixtures were described in one sentence, and the description would have produced doubles that flip a job state without writing rows — so `_report` would print "(no answer)" and every output assertion would have passed for the wrong reason. | Written out in full, including `_write_assistant` and `_write_tool_turn`: the doubles simulate the handler's own writeback (assistant text, a TOOL turn, a `ToolInvocation`, and the index move) synchronously inside `enqueue`, with a note saying why that is the right seam. |
| M4 | Three `caplog` assertions passed vacuously: `caplog` captures at WARNING by default and both log calls are INFO, so the assertions would have held even with the logging deleted. | Every one now wraps `caplog.at_level(logging.INFO, logger=<the module's own logger>)`, with the reason at the call site, and `import logging` added to both test modules. |
| M5 | Task 10's tolerant-key test overrode `FARABUNKER_FEATURES` to prove an unregistered tool is tolerated — a process-wide URL-resolution hazard used to test something that is not about the install at all. | The test now pops `vision.generate` from `_TOOLS` under the autouse snapshot fixture and asserts the key comes back in `unregistered_keys`, with an anti-vacuous guard. **No test in this phase overrides the flag**, and the vision-flag-rule constraint says so. |
| M6 | `history_messages` filtered only on state, so a delegate's turns — written into the same conversation at depth ≥ 1 — would have replayed into the PARENT's next prompt, complete with tool-call blocks naming tools the parent may not hold. | `history_messages` now filters `depth=0` as well; `prompt.py`'s module and function docstrings say why; and Task 11 gains `test_a_delegates_turns_never_replay_into_the_parents_prompt`, which asserts both that the turns were written and that they do not replay. |
| M7 | `plan_turn._resolve_chat` and `loop._resolve_llm` were the same rule twice, and the docstring justifying the split cited `tools/rag/jobs.py`'s two-pass shape — a citation that does not hold, because that module's second pass adds a health check and P2's does not. | One `agents/runtime/bindings.py::resolve_chat`, imported by both. The false citation is deleted and replaced with the real precedent (`tools/rag/jobs.py:109-130`'s `_resolve_answer`, a single helper both passes call) plus an explicit note on why the two-pass shape does not apply here. |

**minor (all 18 applied)** — m1 the `_is_allowed_registration_import` widening is deleted; the module-scope guard stays on `TOOL_MODULES` (its subject is what `ready()` imports, and `ready()` never imports `agents/runtime/`), `RUNTIME_MODULES` is swept for `get_job` only, and delegate.py's module-scope imports are legal as a result. m2 `answered_by` added to `run_turn`'s Produces with what it means on each path. m3 one sentence in both `models.py` and `loop.py`: `tool_call["args"]` is the validated dict on ok/refused/error/degraded and the RAW dict on `param_error`, because validation is exactly what did not happen there. m4/m6 Task 1 no longer creates `agents/tests/_helpers.py` at all; Task 3 creates it with exactly what its own tests call, and later tasks add to it when they need to. m5 `agents/runtime/tests/_helpers.py` imports `make_job_ctx`/`make_budget`/`make_principal`/`snapshot_tools`/`restore_tools`/`bind_chat_role` from `agents/tests/_helpers.py` — same app, no sixth copy — and defines only `FakeToolLLM`, `patch_llm`, `make_tool_ctx`, and the stub runners. m7 `base.py:249` → `:250` in both places. m8 Task 5 re-measures and corrects `toolschema.py:4-5`'s stale `0.14.23`. m9 the `_supports_tool_calling` patch is **kept** and justified: with M1's real binding the unpatched call reaches Ollama's `/api/show` over HTTP, and `docs/DEV.md` promises the suite runs with Ollama down. m10 `assert result["steps_used"] == 2` added to the one-tool-path test. m11 both `plan_turn` and `run_turn` derive the agent from `turn.conversation.agent`; the payload's `agent` slug is documented as the summarizer's only, with the reason (a planner that trusted the payload could plan for a different agent than the loop runs). m12/m13 `loop.py` and `delegate.py` hoist their imports to module scope, with a comment saying the lazy-import discipline exists for `ready()` and buys nothing here. m14 `_finished_job` is imported in Task 12's test module. m15 `collections.abc.Sequence`, not `typing.Sequence`. m16 the redundant `agent.library` re-registration is deleted from the cycle test. m17 the dropped `"flow"` payload key is recorded as deviation D7. m18 Task 14 fixes `docs/DEV.md:181-186`'s `core.inference.gateway` path, which has not existed since P0 and is the one place a newcomer is told how the suite stays offline.

**Deviations promoted or added this round.** **D6** — `Agent.save()`'s keyword-only `_from_resident_sync` bypass. §7.5 says the sync "needs no bypass of §7.1's `save()` validation, and must not have one", and P2 gives it none: the `tool_keys` rule is unbypassable. The bypass is for the *resident-edit guard*, which §7.5 also mandates and which makes its own suggested `update_or_create` impossible (that call reaches `save()` with no way to pass a keyword). Keyword-only, greppable, one caller, and a test pins that an ordinary `save()` on a resident row still refuses. **D7** — the payload carries no `"flow"` key (a key whose only possible value is `None` is not forward compatibility). **D8** — `general` does not grant `flow.run`, which P3 registers; the tolerant-key path is proven against a real registered-then-removed tool instead.

### Orchestrator ruling

**Accepted all.** D1–D5 and spec inconsistencies 1–10 stand as authored. The executor re-verifies every `file:line` citation against the tree before running each task, and Task 1's baseline is measured on the merged tree rather than predicted.

### Round 2 — AMEND (4 MAJOR / 2 minor, all regressions introduced by the round-1 fixes). Author applied all.

| # | Finding | Applied |
|---|---|---|
| N1 | `bind_chat_role` (added for M1) passed `capability=` — `ModelConnection`'s real field is **`capabilities`, a JSONField LIST** (`models/registry/models.py:63`). Every M1 test would have died on a `TypeError` at construction. | `capabilities=[capability]`, plus an `embed_dim` passthrough for the embeddings fixture; the guard note now names the plural field and cites `models/registry/tests/_helpers.py:18-51` as the precedent instead of guessing at `context_window`. |
| N2 | M1 bound the chat role in exactly one Task 11 test; the other seven reach `resolve_chat` unbound and raise. | A module-level `@pytest.fixture(autouse=True) def _bind(db)` in `test_delegate.py`; the inline call is deleted. Its docstring says why autouse: a per-test line is one seven tests would forget, and the resulting `ValueError` reads like a bug in the code under test. |
| N3 | m11 made `plan_turn` read `turn.conversation.agent`, but `TestPlanTurn` still built agent and turn independently — so `make_turn()` created a **second** agent at the default slug (`uniq_agent_slug_ci`, or a plan for an agent the test never configured). | New `_turn_for(agent)` helper; every case uses it; `_payload` loses its `agent` parameter and its docstring records that the payload's slug is the summarizer's only. |
| N4 | m12/m13's hoist to `from models.contracts.gateway import get_llm_for` binds the function object at import time, so `patch_llm`'s attribute patch would never be seen — every loop and delegate test would drive a real engine. | `from models.contracts import gateway` + `gateway.get_llm_for(...)` in both `loop.py` and `delegate.py`, with the reason at each call site and a paragraph at `patch_llm`'s definition explaining that the module-attribute lookup is what makes the patch bite. |

**minor** — n1 `agents/tests/_helpers.py` gains `import pytest` in Task 3, since Tasks 6 and 12 add `@pytest.fixture`s to it. n2 `delegate.py` resolves through `resolve_chat(agent, None)` rather than a bare `resolve(agent.llm_role)`, so a delegate cannot resolve its own role by a different rule than the planner and the loop; the direct `resolve` import is dropped.

**Orchestrator ruling.** Accepted all. No further review round.

### Round 3 — CLEAN (one minor residual)
- Residual: `agent_turn._preflight` hand-rolls the picker/role rule. **Orchestrator ruling (binding implementer edit):** use `resolved, _ = resolve_chat(agent, connection)` inside the existing `try` — `resolve_chat` is the single spelling of that rule.
- Execution note: the plan is executable as of this round; every `file:line` is re-verified by the implementer before each task (tree moves under R2/Q-track merges).
