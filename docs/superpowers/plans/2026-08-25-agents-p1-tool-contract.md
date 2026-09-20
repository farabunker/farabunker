# Agents P1 — The Tool Contract and the v1 Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the platform a single tool contract — `ToolSpec`, `ToolResult`, `ToolContext`, `StepBudget`, a module-level registry, `describe_tool`, and `openai_tool_dict` — living in a pure, Django-free `agents/contracts/` package, and register the v1 tools against it from the `tools/rag`, `tools/vision`, and `models/registry` apps. Every tool runner calls an existing service function; not one grows a parallel seam. No agent, no runtime, no `/chat/`, no UI: P1 ends with a registry that is complete, described, schema-rendered, and provably wired to the real seams, and nothing that drives it yet.

**Architecture:** Ten tasks. Task 1 widens `validate_params`'s annotation to a one-member structural protocol so `ToolSpec` shares the platform's one validation floor rather than growing a second. Tasks 2–5 build `agents/contracts/` bottom-up: the dataclasses, the LLM tool-schema renderer, the registry plus `describe_tool`, and the artifact-reference vocabulary — each a pure module with no Django import, each independently testable. Task 6 adds the one optional engine method (`supports_tool_calling`) the chat preflight will need in P2, implemented against a live HTTP fact rather than a library's hardcoded declaration. Tasks 7–9 register the v1 tools: RAG's three, the `models/` column's one, and vision's two (**VISION-OWNED**). Task 10 installs the permanent structural guards, sweeps the docs, and runs the gates.

**Tech Stack:** Python 3.12/3.13, Django 5 (only for the `AppConfig.ready()` registration points — `agents/contracts/` imports no Django at all), `httpx` against Ollama's `/api/show`, pytest + pytest-django, PostgreSQL on the branch preview port 5433. No new Python dependencies: `requirements.txt:14-16` pins only floors and the installed LLM integration already supports everything §4.6 needs.

**Spec:** docs/superpowers/specs/2026-08-25-agents-and-tools-design.md

## Global Constraints

- **Depends on P0 landing first.** `docs/superpowers/plans/2026-08-25-agents-p0-regroup.md` must be merged before Task 1. Every path in this plan is written against the **post-regroup** tree: `models/contracts/`, `models/registry/`, `models/queue/`, `tools/rag/`, `tools/vision/`, `foundation/`, `agents/`. Re-verify every `file:line` citation against the tree before executing any task — P0 moves files without changing their contents, so line numbers survive, but confirm rather than assume.
- **Orchestrator gates — the full matrix, every task, no exceptions.** Two `FARABUNKER_FEATURES` states × two collection orders:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q                                  # configured collection order
  .venv/bin/pytest -q scripts agents foundation models tools   # reversed
  ```

  The reversed-order command gains `agents` in **Task 2**, which is the task that first creates `agents/contracts/tests/`. Before Task 2 it is `.venv/bin/pytest -q scripts foundation models tools` — pytest errors on a `testpaths`/argument path that does not exist, so `agents` cannot be named earlier. `pytest -q core/tests` does not exist any more: P0 moved those 29 tests to `foundation/tests` and brought them inside `testpaths`.
- **Private test DB per role. Never `test_farabunker`.** Implementers use `farabunker_impl` on the branch preview Postgres at **5433** (`docs/DEV.md:213-232`). Two sessions on the same test database race each other's create/drop lifecycle. Never a bare `test_farabunker`, never `5432`.
- **`agents/contracts/` imports NO Django.** Not `django.conf`, not `django.db`, not `django.apps`, not `django.utils`. It is a rule-1 pure leaf, universally importable in any direction, and Task 10 pins that with a test that imports every module in the package in a subprocess with no `DJANGO_SETTINGS_MODULE` set at all.

**This bites immediately, and the plan is written around it.** `models/contracts/jobkinds.py:34` does `from django.utils.module_loading import import_string` at module scope — so a plain `from models.contracts.jobkinds import JobContext` in `agents/contracts/tools.py` would pull Django into the pure leaf and fail the purity test on the first run. `ToolContext.job` is an **annotation-only** field and `from __future__ import annotations` is present, so the import goes under `if TYPE_CHECKING:` and never executes at runtime. Its runtime imports are therefore exactly: the standard library, and `models.contracts.operations` (`Param`, `validate_params`, and `_describe_param` in Task 4) — a module whose own imports are stdlib plus `models.contracts.roles`, both pure.
- **No `conftest.py`, anywhere.** `find . -name conftest.py` returns nothing and must keep returning nothing. New per-package helper module this phase: **`agents/contracts/tests/_helpers.py`** (Task 2). Autouse fixtures stay *defined* per test module and delegate their bodies to `_helpers`. Helpers are duplicated per app, never imported across apps — `make_job_ctx(**overrides)` exists in both `tools/rag/tests/_helpers.py:76` and `tools/vision/tests/_helpers.py:56` on purpose, and `agents/contracts/tests/_helpers.py` gets its own copy.
- **THE VISION-FLAG RULE** (`tools/rag/tests/_helpers.py:9-38`, enforced by `tools/rag/tests/test_flag_hygiene.py`): any test that overrides `settings.FARABUNKER_FEATURES` (directly, via the `settings` fixture, or via `override_settings`) **AND** performs an actual HTTP request or URL resolution in that same test (Django's test `Client`, `reverse()`) **MUST** keep `"vision"` in the overridden set — even when the test's own point is about `"media"` or an empty set. `config/urls.py` builds its `vision/` mount conditionally, once, at import time; Django resolves the URLconf lazily on the first request/`reverse()` in the process and never re-evaluates it, so one unlucky test poisons every later one. **Task 5's `artifact_url_name` tests call `reverse()`**, and **Task 9's vision-registration tests override the flag**; both are directly subject to this rule. A test that overrides the flag without ever touching `Client`/`reverse()` is not at risk. `tools/rag/tests/test_flag_hygiene.py` sweeps `tools`, `models`, `foundation`, and `agents` (P0 Task 11 made `agents` a listed, existence-guarded tree), so an agents test that breaks the rule fails loudly.
- **Never-500.** Nothing in P1 is reachable from a view yet, but the rule shapes the runners: a tool runner degrades to a `ToolResult` or raises a *named* exception with an operator-readable message, never a traceback and never a bare `Exception`. `foundation/ops`-style blanket `except Exception` with a log line and an honest message is the pattern.
- **No model names or versions.** Not in code, not in a `ToolSpec.description`, not in a comment, not in a test name, not in this plan. The repository is going public and ADR 0010's third amendment (`docs/adr/0010-model-management-framework.md:290-380`) forbids the platform from naming a model for the operator. `models.status` reports whatever `role_primary` returns, including a `"<model_id> (environment override)"` string the *operator's* environment produced — that is reporting the operator's own binding, not the platform naming a model, and it is exactly what `/inference/` already shows.
- **ADR import law, post-regroup (spec §3.3):**
  - **Rule 1 — pure leaves are universally importable.** `foundation/format.py`, `foundation/files.py`, everything under `models/contracts/`, and everything under `agents/contracts/`. Any column, any direction. This is what lets `tools/rag/tools.py` and `models/registry/tools.py` both import `agents.contracts.tools`.
  - **Rule 2 — Django apps are column-private.** `tools/rag`, `tools/vision`, `models/registry`, `models/queue`, `agents/runtime`, `agents/chat`, `foundation/ops`, `foundation/setup` are not importable across a column boundary, with exactly one exception: a `tools/*` **or** `agents/*` app MAY import `models.registry.bindings`, and only that module. `models.registry.models` and `models.registry.views` stay off-limits to every column, with no exception.
  - **Rule 3 — cross-column *work* goes through a seam, never an import.** The queue (`models.contracts.queue`), the gateway (`models.contracts.gateway`), and — new in this phase — the tool registry (`agents.contracts.tools`), whose `ToolSpec.runner` is a dotted-path string resolved at call time by `models.contracts.jobkinds.resolve_dotted_path` (`jobkinds.py:273`). Rule 3's third clause is what keeps `agents/` from importing `tools/` at module scope. There is no cycle: `tools/*` imports `agents.contracts` (pure, rule 1); `agents/runtime` reaches `tools/*` only through a string.
  - **`models/registry/tools.py` importing `models.registry.bindings` is an IN-COLUMN import** and crosses no boundary — that is one of the two reasons ruling R2 keeps `models.status` at all.
- **Never `import models`.** Always `from models.<sub> import ...`. Stated in `models/README.md` and `models/__init__.py` by P0.
- **Registration imports no implementation module.** `AppConfig.ready()` may import a module that *declares* `ToolSpec` constants, but that module must not, at import time, pull in the service layer it calls. Every runner does its heavy imports **lazily, inside the function body** — the same shape `tools/rag/categories.py:40` already uses (`from tools.rag.models import Category  # lazy: keep module import light`). Task 10 pins this with a subprocess test. This is not stylistic: `ToolSpec.runner` is a dotted-path *string* for exactly the reason `JobKind.handler` is one (`models/contracts/jobkinds.py:175-184`, and the code comment at `tools/vision/apps.py:51-55` which says so outright), and a `tools.py` that eagerly imported `retrieval`/`ingest`/`services` would reintroduce the startup cost that design exists to avoid.
- **Ruling R3 — `describer` is an INERT reserved field.** It is declared on `ToolSpec`, documented, and defaulted to `""`. **Nothing calls it.** `openai_tool_dict` does not consult it and does not filter params by live `supported`; the tool schema handed to a model is built from static `ToolSpec.params` alone, on every turn. `describe_tool` emits **no** live-facts keys (`supported`, `unsupported_reason`, `options`) in P1–P4. **No describer is implemented in P1, by anyone, including the VISION-OWNED session.** Writing one is R2/Phase-1.55 work.
- **Ruling R2 — `models.status` is DB-only.** It makes no `is_healthy` HTTP probe and no engine call of any kind. `/setup/` is the surface that answers "is the engine up", on demand, for a human, outside the queue.
- **A tool runner must never block on a queue job.** It may enqueue one and return its id; it must never call `get_job` in a loop, and it must never call `enqueue` for work whose result it needs. On a default install `JobSettings.memory_budget_bytes` is `null` (`models/queue/models.py:184-192`), which `models/queue/scheduler.py:374-380` reads as sequential mode — at most one job on the whole machine — so a job that waits on a job it enqueued deadlocks with certainty, not probability. `rag.ingest` enqueues and returns the id without waiting; that is legal and is the only enqueue in P1. Task 10 pins the rule with a source-text guard.
- **Tests and docs ship with every task** (ADR 0008, `docs/adr/0008-engineering-standards.md:15-30`). TDD: failing test first, run it, minimal implementation, run it, docs, commit.
- **Doubles are HTTP-layer for engines.** `FakeComfyUI` (`tools/vision/tests/_helpers.py:118`) patches `httpx.get`/`.post` so the adapter's real URL building and parsing run; an engine's own methods are never mocked away. Task 6's new `fake_ollama_show(...)` follows that shape and extends the existing `fake_ollama_get` (`models/registry/tests/_helpers.py:104-128`) rather than replacing it.
- **Baseline: measure it, do not predict it.** Task 1 starts by recording `pytest -q --collect-only | tail -1` on the merged post-P0 tree; that number is this phase's floor. Every later task's expectation is a **delta**: the whole suite green, and the collected count up by exactly what that task's own per-file run reported. Never a hand-computed total — `@pytest.mark.parametrize` expands, so an arithmetic prediction is wrong more often than the code is, and a plan that stops the implementer on a correct run is worse than no number at all. A count that goes *down*, or that differs between the configured and reversed collection orders, is a real defect and must be chased.
- **Migrations: expect ZERO.** P1 adds no model and no field. If a step appears to need a migration, STOP and report.
- **Branch-only.** No commits to `main`, no merges to `main`, no deploy from Tasks 1-9. Task 10 owns the ladder.
- **Commit trailers.** Every commit ends with:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```
- **Verification doctrine.** No "done"/"works"/"fixed"/"complete" language about the tool contract until Task 10's ladder finishes. P1 ships no UI, so its Rung 3 is "the suite, plus `/setup/`, `/inference/`, `/rag/`, `/queue/`, and `/vision/` still render" — but that is still a rung, and it is still driven in a browser.

### `pytest.ini` `testpaths`

P0 left it at `testpaths = tools models foundation scripts`. **Task 2 appends `agents`** — the phase that first creates `agents/contracts/tests/`, and therefore the first phase in which those tests would otherwise not be collected at all:

```
testpaths = tools models foundation agents scripts
```

### VISION-OWNED work in this phase

| Task | Owner | Deliverable |
|---|---|---|
| 9 | **VISION-OWNED** — executed by a different session | `tools/vision/tools.py` (`vision.operations`, `vision.generate`), their registration in `VisionConfig.ready()`, and the `ToolResult` shaping of `services.job_json` output |
| all others | this plan's session | everything else |

Task 9 states its exact interfaces — every signature it consumes, every signature it produces, the exact `ToolSpec` code, the exact `ToolResult` shape, and the exact tests — so the vision session implements against a contract rather than a description. **The vision `describer` is struck from VISION-OWNED entirely** (ruling R3): the inert field declaration in Task 2 is the whole of P1's describer work.

Task 9 depends on Tasks 1–5 and is independent of Tasks 6–8. It can run in parallel with 6–8 once 5 has landed. It must not touch `agents/contracts/`, `models/`, or `tools/rag/`.

---

### Task 1: `HasParams` — one validation floor, the existing one

`validate_params` touches **exactly one** thing on its first argument: `operation.params`, read twice (`models/contracts/operations.py:276,282`). It never calls `file_params()` — the `"file"` kind is handled inline in the coercion loop (`operations.py:326-328`), by kind, not by consulting a key set. So `ToolSpec` needs no `file_param_keys()` method and this phase adds none. Widening the annotation to a one-member structural protocol is a two-line change with no behaviour change, and it is what keeps `agents/contracts` from growing the second validator ADR 0012:676-679 rules out ("`core.inference.operations.validate_params` is the schema *floor* every caller shares, including a future chatbot tool that builds no Django form at all").

**Files**
- Modify: `models/contracts/operations.py` — add `HasParams` (after `Param`, before `Operation`); change `validate_params`'s first-argument annotation
- Test: `tools/vision/tests/test_operations.py` (the existing home for `models/contracts/operations.py`'s tests — it rode along from `modules/vision/tests/` in P0)

**Interfaces**
- Consumes: `models.contracts.operations.Param` (`operations.py:41-75`).
- Produces: `models.contracts.operations.HasParams` — a `typing.Protocol` with exactly one member, `params: tuple[Param, ...]`. `Operation` satisfies it structurally; `agents.contracts.tools.ToolSpec` (Task 2) will too. `validate_params(operation: HasParams, raw: dict) -> dict` — same body, same behaviour, same `ParamError`.

**Steps**

- [ ] Record the baseline on the merged post-P0 tree, before touching anything:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q --collect-only | tail -1
  cat pytest.ini
  # EXPECTED: a collected count -- write it down, it is this phase's floor -- and
  #   `testpaths = tools models foundation scripts` (P0's final form).
  ```

- [ ] Write the failing test. Append to `tools/vision/tests/test_operations.py`:

  ```python
  class TestValidateParamsAcceptsAnythingWithAParamSchema:
      """`validate_params` is the schema FLOOR every caller shares -- the
      page's form and a tool call both land here (ADR 0012:676-679). Its
      annotation says `Operation`, but its BODY only ever reads
      `.params` (operations.py:276,282), so a second validator in
      `agents/contracts` would be exactly the parallel seam that ADR rules
      out. This pins the structural contract instead."""

      def test_hasparams_is_a_protocol_with_exactly_one_member(self):
          from models.contracts.operations import HasParams

          assert issubclass(type(HasParams), type(Protocol))
          # `params` and nothing else. A second member would be a claim
          # `validate_params`'s body does not make.
          members = {
              name for name in HasParams.__annotations__
              if not name.startswith("_")
          }
          assert members == {"params"}

      def test_operation_satisfies_hasparams(self):
          from models.contracts.operations import HasParams, TXT2IMG

          def takes(schema: HasParams) -> tuple:
              return schema.params

          assert takes(TXT2IMG) == TXT2IMG.params

      def test_a_bare_object_with_params_validates(self):
          """The whole point: a non-`Operation` carrying a `params` tuple
          validates through the SAME floor, with the same coercions and
          the same `ParamError`."""
          from models.contracts.operations import Param, ParamError, validate_params

          @dataclass(frozen=True)
          class _Schema:
              params: tuple[Param, ...]

          schema = _Schema(params=(
              Param("query", "text", "Query", required=True),
              Param("top_k", "int", "Results", default=None, min=1, max=50),
          ))

          assert validate_params(schema, {"query": "x", "top_k": "7"}) == {
              "query": "x", "top_k": 7,
          }

          with pytest.raises(ParamError) as excinfo:
              validate_params(schema, {"query": "", "top_k": "999"})
          assert set(excinfo.value.errors) == {"query", "top_k"}

      def test_an_unknown_key_is_still_rejected_for_a_non_operation_schema(self):
          from models.contracts.operations import Param, ParamError, validate_params

          @dataclass(frozen=True)
          class _Schema:
              params: tuple[Param, ...]

          with pytest.raises(ParamError) as excinfo:
              validate_params(_Schema(params=(Param("a", "text", "A"),)), {"b": "x"})
          assert excinfo.value.errors == {"b": "Unknown parameter for this operation."}
  ```

  Add `from dataclasses import dataclass` and `from typing import Protocol` to the module's imports if they are not already there.

- [ ] Run and expect **red** on the import:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q tools/vision/tests/test_operations.py -k HasParams
  # EXPECTED: ImportError: cannot import name 'HasParams' from 'models.contracts.operations'
  ```

- [ ] Implement. In `models/contracts/operations.py`, add `Protocol` to the `typing` import and insert immediately after `Param`'s definition (before `Operation`):

  ```python
  class HasParams(Protocol):
      """Anything carrying a parameter schema `validate_params` can validate
      against.

      ONE member, deliberately. `validate_params` touches exactly one thing
      on its first argument -- `operation.params`, read twice (below at the
      `known = {...}` set and the coercion loop). It never calls
      `file_params()`; the `"file"` kind is handled inline in that loop, BY
      KIND, not by consulting a key set. A second member here would be a
      claim the body does not make.

      `Operation` satisfies this structurally, and so does
      `agents.contracts.tools.ToolSpec` -- which is the point: ADR
      0012:676-679 makes this function the schema FLOOR every caller
      shares, "including a future chatbot tool that builds no Django form
      at all". A second validator in `agents/contracts` would be exactly
      the parallel seam that rules out.

      Deliberately NOT `@runtime_checkable`: a runtime-checkable Protocol
      with a non-method member raises `TypeError` on `isinstance`, and
      nothing in this codebase needs an isinstance check against it. It is
      a type annotation, and only that.
      """

      params: tuple[Param, ...]
  ```

  Then change the signature at `operations.py:250`:

  ```python
  def validate_params(operation: HasParams, raw: dict) -> dict:
  ```

  Change nothing else in the function — not one line of its body.

- [ ] Run and expect **green**, then run the whole operations module to prove no behaviour changed:

  ```bash
  .venv/bin/pytest -q tools/vision/tests/test_operations.py
  # EXPECTED: all passed, including the four new tests.
  ```

- [ ] Update the docstring at `operations.py:251-256` so it says what is now true — replace "Coerce and range-check `raw` against `operation`'s schema" with "Coerce and range-check `raw` against `operation`'s schema. `operation` is anything with a `params` tuple (`HasParams`): an `Operation`, or a `ToolSpec`." Leave the rest of the docstring untouched.

- [ ] Run the full matrix and commit:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts foundation models tools
  # EXPECTED: whole suite green; collected count = P0's final count + the
  #   4 tests this task added. Record the real number as this phase's floor.

  git add models/contracts/operations.py tools/vision/tests/test_operations.py
  git commit -m "$(cat <<'EOF'
  refactor(contracts): widen validate_params to a HasParams protocol

  `validate_params` reads exactly one thing off its first argument --
  `.params`, twice -- and handles the "file" kind inline BY KIND, never via
  a key set. So the annotation, not the body, was the only thing tying it
  to `Operation`.

  New one-member `HasParams` protocol; `Operation` satisfies it today and
  `agents.contracts.tools.ToolSpec` will in the next task. That is what
  keeps the tool contract on the platform's ONE validation floor instead of
  growing the second validator ADR 0012:676-679 rules out. Zero behaviour
  change: the body is untouched.

  Not @runtime_checkable -- a runtime-checkable Protocol with a non-method
  member raises TypeError on isinstance, and nothing needs that check.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 2: `agents/contracts/tools.py` — the dataclasses

`ToolSpec`, `ToolResult`, `StepBudget`, `ToolContext`, `ToolRefused`, and `validate_tool_args`. Deliberately **not** a class hierarchy: every sibling registry in this codebase (`RoleSpec` `models/contracts/roles.py:58`, `Operation` `operations.py:79`, `JobKind` `jobkinds.py:175`) is a frozen dataclass with dotted-path strings for its callables, specifically so registration never imports the implementing module. A base class would force that import at `ready()` time and break the exact property those docstrings exist to protect.

**Files**
- Create: `agents/contracts/__init__.py`, `agents/contracts/tools.py`
- Create: `agents/contracts/tests/__init__.py`, `agents/contracts/tests/_helpers.py`, `agents/contracts/tests/test_tools.py`
- Modify: `pytest.ini:4` — `testpaths = tools models foundation agents scripts`
- Test: `agents/contracts/tests/test_tools.py`

**Interfaces**
- Consumes, **at runtime**: `models.contracts.operations.Param` (`operations.py:41-75`) and `models.contracts.operations.validate_params` (`operations.py:250`). `ParamError` is raised *through* `validate_params` and is never imported here; `HasParams` (Task 1) is satisfied structurally by `ToolSpec` and is likewise never imported. Consumed **under `TYPE_CHECKING` only**: `models.contracts.jobkinds.JobContext`, for one annotation — see M5 above.
- Produces:
  - `TOOL_PARAM_KINDS = frozenset({"text", "int", "float", "choice", "seed", "asset"})`
  - `ToolSpec(key, label, description, params=(), roles=(), runner="", mutates=False, describer="")` — frozen dataclass
  - `ToolResult(text="", data={}, artifacts=())` — frozen dataclass
  - `StepBudget(*, steps, deadline_monotonic, recoveries=1)` — mutable; `.spend(n=1)`, `.exhausted`, `.expired`, `.steps_left`, `.recoveries_left`, `.deadline_monotonic`
  - `ToolContext(conversation_id, agent_key, depth, budget, job)` — frozen dataclass
  - `ToolRefused(ValueError)`
  - `validate_tool_args(spec: ToolSpec, raw: dict) -> dict`

**Steps**

- [ ] Create the package skeleton and take `testpaths` to its P1 form. This must happen in this task, not later — `agents/contracts/tests/` is created here and its tests are otherwise not collected at all:

  ```bash
  mkdir -p agents/contracts/tests
  touch agents/contracts/tests/__init__.py
  sed -i '' 's|^testpaths = tools models foundation scripts$|testpaths = tools models foundation agents scripts|' pytest.ini
  cat pytest.ini
  # EXPECTED line 4: testpaths = tools models foundation agents scripts
  ```

  `agents/contracts/__init__.py`:

  ```python
  """The TOOL CONTRACT -- pure, Django-free, universally importable (rule 1).

  Three modules, no more:

  - `tools.py`      -- ToolSpec, ToolResult, ToolContext, StepBudget,
                       ToolRefused, `validate_tool_args`, the module-level
                       registry, and `describe_tool`.
  - `toolschema.py` -- ToolSpec -> the tool-calling JSON an LLM reads, and
                       the wire-name round trip back.
  - `artifacts.py`  -- the artifact-reference vocabulary.

  NOTHING HERE IMPORTS DJANGO -- not transitively either. At runtime it
  imports the standard library and `models.contracts.operations`, and
  nothing else. `models.contracts.jobkinds` is reached ONLY under
  `if TYPE_CHECKING:` for one annotation, because that module does
  `from django.utils.module_loading import import_string` at module scope
  (jobkinds.py:34) and a real import would drag Django in.
  `agents/contracts/tests/test_purity.py` pins this in a subprocess with no
  DJANGO_SETTINGS_MODULE set at all -- because "pure" that is only asserted
  in a docstring is a hope, not a property.
  """
  ```

- [ ] Write `agents/contracts/tests/_helpers.py` — the per-package helper module (no `conftest.py`, ever):

  ```python
  """Shared test helpers for agents/contracts/tests.

  Plain importable module -- **not** a `conftest.py` (the repo forbids
  `conftest.py` files anywhere). Each test module imports what it needs from
  here explicitly; autouse fixtures stay *defined* in each test module but
  delegate their bodies to the functions below, per repo convention (see
  `models/registry/tests/_helpers.py`, `tools/rag/tests/_helpers.py`,
  `tools/vision/tests/_helpers.py` -- all three say the same thing in their
  own docstrings).

  `make_job_ctx` is duplicated here rather than imported from another app's
  `_helpers`, exactly as it is duplicated between `tools/rag/tests/
  _helpers.py:76` and `tools/vision/tests/_helpers.py:56`. Helpers are
  per-app by convention; a cross-app import would make one package's test
  scaffolding load-bearing for another's.
  """
  from __future__ import annotations

  import time

  from agents.contracts.tools import StepBudget, ToolContext, ToolResult, ToolSpec
  from models.contracts.jobkinds import JobContext
  from models.contracts.operations import Param


  def make_job_ctx(**overrides) -> JobContext:
      """A `models.contracts.jobkinds.JobContext` for calling a runner
      directly -- no real worker or DB behind it, just inert
      `_report`/`_checkpoint` writers."""
      fields = dict(
          job_id=1,
          attempt=0,
          checkpoint_state=None,
          _report=lambda progress: None,
          _checkpoint=lambda state: None,
      )
      fields.update(overrides)
      return JobContext(**fields)


  def make_budget(**overrides) -> StepBudget:
      """A `StepBudget` with plenty of room and a deadline far enough out
      that a test never trips it by accident."""
      fields = dict(steps=8, deadline_monotonic=time.monotonic() + 900.0, recoveries=1)
      fields.update(overrides)
      return StepBudget(**fields)


  def make_tool_ctx(**overrides) -> ToolContext:
      """A `ToolContext` for calling a runner directly."""
      fields = dict(
          conversation_id="00000000-0000-0000-0000-000000000000",
          agent_key="test-agent",
          depth=0,
          budget=make_budget(),
          job=make_job_ctx(),
      )
      fields.update(overrides)
      return ToolContext(**fields)


  def make_spec(**overrides) -> ToolSpec:
      """A minimal valid `ToolSpec`. Pass keyword overrides for a test whose
      point IS a specific field."""
      fields = dict(
          key="stub.tool",
          label="Stub tool",
          description="A tool that exists so a test has one.",
          params=(Param("text", "text", "Text"),),
          roles=(),
          runner="agents.contracts.tests._helpers.stub_runner",
          mutates=False,
      )
      fields.update(overrides)
      return ToolSpec(**fields)


  # Module-level so `make_spec`'s default `runner` is a dotted path that
  # really resolves -- the structural guard in Task 10 walks every
  # registered runner and would otherwise have to special-case test specs.
  CALLS: list[tuple[dict, object]] = []


  def stub_runner(args: dict, ctx) -> ToolResult:
      """Records its call and returns a fixed result."""
      CALLS.append((args, ctx))
      return ToolResult(text="stub ran", data={"args": args})


  def snapshot_tools() -> dict:
      """Body of the `_snapshot_tools` autouse fixture every test module
      that registers a tool uses: copy `_TOOLS`, hand it back, and let the
      fixture restore it afterwards.

      The same shape as `tools/vision/tests/_helpers.py:25::clear_bindings`
      and `:99::reset_engine_caches`. A module-global registry surviving
      between tests is exactly the state that makes a suite pass in one
      collection order and fail in the other -- which is why the repo runs
      both orders.
      """
      from agents.contracts import tools as tools_module

      return dict(tools_module._TOOLS)


  def restore_tools(saved: dict) -> None:
      from agents.contracts import tools as tools_module

      tools_module._TOOLS.clear()
      tools_module._TOOLS.update(saved)
      CALLS.clear()
  ```

- [ ] Write the failing tests. `agents/contracts/tests/test_tools.py`:

  ```python
  """The tool contract's dataclasses (spec sections 4.1, 4.2, 4.4)."""
  from __future__ import annotations

  import time

  import pytest

  from agents.contracts.tests._helpers import make_budget, make_spec, make_tool_ctx
  from agents.contracts.tools import (
      TOOL_PARAM_KINDS, StepBudget, ToolContext, ToolRefused, ToolResult, ToolSpec,
      validate_tool_args,
  )
  from models.contracts.operations import Param, ParamError


  class TestToolParamKinds:
      def test_file_is_excluded_because_a_tool_call_is_json(self):
          """ADR 0012:140 -- a tool call is JSON, so it cannot carry an
          upload object. An image input is an artifact REFERENCE
          (agents/contracts/artifacts.py), never a file."""
          assert "file" not in TOOL_PARAM_KINDS

      def test_every_other_operation_param_kind_is_usable(self):
          from models.contracts.operations import PARAM_KINDS

          assert TOOL_PARAM_KINDS == frozenset(PARAM_KINDS) - {"file"}


  class TestToolSpecValidation:
      def test_a_file_param_is_rejected_at_definition_time(self):
          with pytest.raises(ValueError) as excinfo:
              make_spec(params=(Param("image", "file", "Image"),))
          assert "image" in str(excinfo.value)
          assert "file" in str(excinfo.value)

      def test_a_blank_runner_is_rejected(self):
          with pytest.raises(ValueError, match="declares no runner"):
              make_spec(runner="")

      def test_a_spaced_key_is_rejected(self):
          with pytest.raises(ValueError, match="space-free"):
              make_spec(key="rag search")

      def test_a_blank_key_is_rejected(self):
          with pytest.raises(ValueError, match="non-blank"):
              make_spec(key="")

      def test_a_key_containing_a_double_underscore_is_rejected(self):
          """`wire_name` maps "." -> "__" and `key_from_wire_name` maps
          back. A key that already contains "__" makes that round trip
          ambiguous, so it is a definition-time error, not a runtime
          surprise on the one tool call that happens to hit it."""
          with pytest.raises(ValueError, match="wire-name collision"):
              make_spec(key="rag__search")

      def test_a_valid_spec_is_frozen(self):
          spec = make_spec()
          with pytest.raises(Exception):
              spec.key = "other"

      def test_describer_defaults_to_blank_and_is_inert(self):
          """Ruling R3: the field exists so R2/Phase-1.55 is a fill-in
          rather than a contract change. NOTHING in P1-P4 calls it."""
          assert make_spec().describer == ""


  class TestToolResult:
      def test_defaults_are_empty_and_do_not_share_state(self):
          a, b = ToolResult(), ToolResult()
          a.data["x"] = 1
          assert b.data == {}
          assert a.artifacts == ()
          assert a.text == ""

      def test_artifacts_is_a_tuple_of_reference_strings(self):
          result = ToolResult(artifacts=("document:7", "output:3"))
          assert result.artifacts == ("document:7", "output:3")


  class TestStepBudget:
      def test_spend_decrements_and_exhausts(self):
          budget = make_budget(steps=2)
          assert not budget.exhausted
          budget.spend()
          assert budget.steps_left == 1
          budget.spend()
          assert budget.steps_left == 0
          assert budget.exhausted

      def test_spend_takes_a_count(self):
          budget = make_budget(steps=5)
          budget.spend(3)
          assert budget.steps_left == 2

      def test_a_past_deadline_is_expired(self):
          assert StepBudget(steps=8, deadline_monotonic=time.monotonic() - 1.0).expired

      def test_a_future_deadline_is_not_expired(self):
          assert not make_budget().expired

      def test_recoveries_default_to_one(self):
          assert make_budget().recoveries_left == 1

      def test_it_is_mutable_by_design(self):
          """The ONE mutable object in this module: a turn's remaining
          allowance, SHARED by every tool call and every delegated
          sub-agent in that turn. `ToolContext` stays frozen and holds a
          reference -- the same shape `JobContext` uses for its injected
          closures."""
          budget = make_budget(steps=4)
          ctx = make_tool_ctx(budget=budget)
          ctx.budget.spend()
          assert budget.steps_left == 3


  class TestToolContext:
      def test_it_is_frozen_and_carries_the_shared_budget(self):
          ctx = make_tool_ctx()
          with pytest.raises(Exception):
              ctx.depth = 9
          assert ctx.depth == 0
          assert isinstance(ctx.budget, StepBudget)

      def test_job_is_the_runners_only_progress_path(self):
          reported = []
          ctx = make_tool_ctx()
          object.__setattr__(ctx.job, "_report", reported.append)
          ctx.job.report_progress(1, total=8, unit="items", label="thinking")
          assert reported == [{"done": 1, "total": 8, "unit": "items", "label": "thinking"}]


  class TestValidateToolArgs:
      def test_it_delegates_to_the_platforms_one_floor(self):
          spec = make_spec(params=(
              Param("query", "text", "Query", required=True),
              Param("top_k", "int", "Results", default=None, min=1, max=50),
          ))
          assert validate_tool_args(spec, {"query": "x", "top_k": "7"}) == {
              "query": "x", "top_k": 7,
          }

      def test_it_raises_paramerror_with_per_arg_reasons(self):
          """`.errors` maps a param key to a human-readable reason
          (operations.py:201-209) -- which is what makes a bad tool call
          the ONE failure worth handing back to the model for a retry."""
          spec = make_spec(params=(Param("query", "text", "Query", required=True),))
          with pytest.raises(ParamError) as excinfo:
              validate_tool_args(spec, {"query": ""})
          assert excinfo.value.errors == {"query": "Query is required."}

      def test_an_unknown_arg_is_rejected_not_swallowed(self):
          spec = make_spec(params=(Param("query", "text", "Query"),))
          with pytest.raises(ParamError) as excinfo:
              validate_tool_args(spec, {"quary": "x"})
          assert "quary" in excinfo.value.errors

      def test_a_blank_seed_resolves_to_an_integer(self):
          spec = make_spec(params=(Param("seed", "seed", "Seed"),))
          clean = validate_tool_args(spec, {})
          assert isinstance(clean["seed"], int)


  class TestToolRefused:
      def test_it_is_a_distinct_exception_from_paramerror(self):
          """Section 10.1 splits them: a refusal gets NO retry, a
          ParamError gets exactly one. Nothing catches ToolRefused until
          P2's `invoke_tool`, but a runner author -- including the
          VISION-OWNED session -- needs to know which exception means
          which, and this module is the only place that can say so."""
          assert issubclass(ToolRefused, ValueError)
          assert not issubclass(ToolRefused, ParamError)
          assert not issubclass(ParamError, ToolRefused)
  ```

- [ ] Run and expect **red** on the import:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q agents/contracts/tests/test_tools.py
  # EXPECTED: ModuleNotFoundError: No module named 'agents.contracts.tools'
  ```

- [ ] Implement `agents/contracts/tools.py`:

  ```python
  """The tool contract: what a tool IS, what a runner is called with, and
  what it hands back.

  Deliberately NOT a class hierarchy. Every sibling registry in this
  codebase -- `RoleSpec` (models/contracts/roles.py:58), `Operation`
  (operations.py:79), `JobKind` (jobkinds.py:175) -- is a frozen dataclass
  with dotted-path STRINGS for its callables, specifically so registration
  never imports the implementing module. `tools/vision/apps.py:51-55` says
  so in a code comment. A base class would force that import at `ready()`
  time and break the exact property those docstrings exist to protect.

  Pure: no Django, no database, no I/O. Rule 1 of the import law -- any
  column may import this, in any direction.
  """
  from __future__ import annotations

  import time
  from dataclasses import dataclass, field
  from typing import TYPE_CHECKING

  from models.contracts.operations import Param, validate_params

  if TYPE_CHECKING:  # pragma: no cover -- annotation only, never imported at runtime
      # `models/contracts/jobkinds.py:34` imports `django.utils.module_loading`
      # at module scope. Importing `JobContext` for real would therefore drag
      # Django into this rule-1 pure leaf and break its universal
      # importability -- `agents/contracts/tests/test_purity.py` fails on
      # exactly that. `ToolContext.job` is an annotation-only field and
      # `from __future__ import annotations` is in force above, so the name
      # is never evaluated at runtime and this costs nothing.
      from models.contracts.jobkinds import JobContext

  # Param kinds a tool may declare. "file" is excluded: a tool call is JSON
  # (ADR 0012:140 -- "future chatbot tool call is JSON, so neither can carry
  # an upload object"), so an image input is an artifact REFERENCE (see
  # agents/contracts/artifacts.py), never an upload object.
  TOOL_PARAM_KINDS = frozenset({"text", "int", "float", "choice", "seed", "asset"})


  class ToolRefused(ValueError):
      """A tool call this platform will not run at all: the tool is not
      granted to this agent, the delegation depth cap is reached, or the
      tool mutates existing state and Identity & Auth has not landed
      (ADR 0010:266-276).

      Distinct from `ParamError` because the recovery policy differs:
      section 10.1 gives a `ParamError` exactly ONE retry (the model was
      told precisely what was wrong and can fix it) and a refusal NONE
      (nothing the model can say will change the answer).

      Nothing catches this until P2's `invoke_tool`. It is declared here,
      now, because a runner author needs to know which exception means
      which, and this module is the only place that can say so.
      """


  @dataclass(frozen=True)
  class ToolSpec:
      """One tool an agent may call.

      `runner` is a dotted-path string to `callable(args: dict, ctx:
      ToolContext) -> ToolResult`, resolved lazily by
      `models.contracts.jobkinds.resolve_dotted_path` -- never a live
      callable, so a `ToolSpec` stays plain data and registration imports
      nothing, exactly like `JobKind.handler`.

      `roles` is the set of `RoleSpec.key`s this tool's runner may consume.
      The agent-turn planner unions these across an agent's granted tools
      and declares the result to the queue at enqueue time; a tool that
      consumes no model declares `()`.

      `mutates` is True when the tool changes state that ALREADY EXISTS --
      configuration, a role binding, a platform setting, or
      previously-ingested content. Creating new work product (a generated
      image, a queued job, a conversation turn) is not `mutates`. A
      `mutates=True` tool is registered but NOT grantable until Identity &
      Auth lands (ADR 0010:266-276).

      `describer` is an INERT RESERVED FIELD (ruling R3). It is a dotted
      path to `callable(spec, resolved) -> dict` returning
      `describe_tool(spec)` with per-param live facts merged in, for the
      one constant vision input screen designed in the spec's section 4.8.
      NOTHING in P1-P4 calls it: `openai_tool_dict` does not consult it and
      `describe_tool` emits no live-facts keys. Two reasons -- a per-prompt
      describer call means an engine round trip inside the turn loop, which
      is the unbounded-latency cost ruling R2 rejects; and a tool schema
      that silently changes shape between turns is a debugging nightmare
      for no v1 benefit. The field exists now only so that work is a
      fill-in rather than a contract change. A string, not a callable, for
      the same reason `runner` is one.
      """

      key: str                          # "rag.search" -- namespace is the owning column
      label: str                        # operator-facing
      description: str                  # what the LLM reads; the whole prompt surface of a tool
      params: tuple[Param, ...] = ()
      roles: tuple[str, ...] = ()
      runner: str = ""
      mutates: bool = False
      describer: str = ""

      def __post_init__(self) -> None:
          if not self.key or " " in self.key:
              raise ValueError(
                  f"Tool key must be a non-blank, space-free string, got {self.key!r}"
              )
          if "__" in self.key:
              # `wire_name` maps "." -> "__" and `key_from_wire_name` maps
              # back (toolschema.py). A key that already contains "__"
              # makes that round trip ambiguous, so it is a definition-time
              # error, not a runtime surprise on the one tool call that
              # happens to hit it.
              raise ValueError(
                  f"Tool key must not contain '__' (wire-name collision): {self.key!r}"
              )
          if not self.runner:
              raise ValueError(f"Tool {self.key!r} declares no runner")
          for param in self.params:
              if param.kind not in TOOL_PARAM_KINDS:
                  raise ValueError(
                      f"Tool {self.key!r} param {param.key!r}: kind {param.kind!r} is not "
                      f"usable in a JSON tool call; must be one of {sorted(TOOL_PARAM_KINDS)}"
                  )


  @dataclass(frozen=True)
  class ToolResult:
      """What a runner returns.

      `text` is what goes back to the LLM as the tool message -- the only
      part the model ever sees. `data` is JSON-safe structured payload for
      the UI (RAG citations land in `data["citations"]`, in the exact dict
      shape `tools/rag/retrieval.py:325-339` already produces).
      `artifacts` are JSON-safe reference strings (see
      `agents/contracts/artifacts.py`) -- never filesystem paths, never
      bytes.
      """

      text: str = ""
      data: dict = field(default_factory=dict)
      artifacts: tuple[str, ...] = ()


  class StepBudget:
      """The one mutable object in this module: a turn's remaining
      allowance, SHARED by every tool call and every delegated sub-agent in
      that turn.

      Mutable by design -- steps are spent. `ToolContext` stays frozen and
      holds a reference, the same shape `JobContext` uses for its injected
      closures (models/contracts/jobkinds.py:118-121).
      """

      def __init__(
          self, *, steps: int, deadline_monotonic: float, recoveries: int = 1
      ) -> None:
          self.steps_left = steps
          self.deadline_monotonic = deadline_monotonic
          self.recoveries_left = recoveries

      def spend(self, n: int = 1) -> None:
          """Spend `n` steps. Never clamps at zero: a caller that overspends
          should see how far past the line it went, not a tidied-up zero."""
          self.steps_left -= n

      @property
      def exhausted(self) -> bool:
          return self.steps_left <= 0

      @property
      def expired(self) -> bool:
          """Wall-clock, monotonic. Checked at the top of every loop
          iteration and before every tool call; expiry ends a turn the same
          way exhaustion does."""
          return time.monotonic() >= self.deadline_monotonic


  @dataclass(frozen=True)
  class ToolContext:
      """The second argument every tool runner is called with.

      `job` is this turn's `models.contracts.jobkinds.JobContext` -- the
      runner's only progress-reporting path, and the same object the vision
      job handler already threads into `services.wait_for`'s `on_poll`
      (tools/vision/jobs.py:308-336). Annotated, never imported at runtime
      (see the `TYPE_CHECKING` block above): `jobkinds` imports Django, and
      this module must not.
      """

      conversation_id: str          # UUID as a string
      agent_key: str                # the agent whose turn is calling
      depth: int                    # 0 at the top level; +1 per agent.delegate hop
      budget: StepBudget
      job: JobContext


  def validate_tool_args(spec: ToolSpec, raw: dict) -> dict:
      """Coerce and range-check `raw` against `spec.params`.

      Raises `models.contracts.operations.ParamError`, whose `.errors` maps
      a param key to a human-readable reason (operations.py:201-209) --
      which is what makes a bad tool call the ONE failure worth handing
      back to the model for a retry (section 10.2).

      One line, deliberately: `validate_params` is the schema FLOOR every
      caller shares (ADR 0012:676-679), and `ToolSpec` satisfies its
      `HasParams` protocol. A second validator here would be exactly the
      parallel seam that ADR rules out.
      """
      return validate_params(spec, raw)
  ```

- [ ] Run and expect **green**:

  ```bash
  .venv/bin/pytest -q agents/contracts/tests/test_tools.py
  # EXPECTED: green. Record the count -- parametrized cases expand.
  ```

- [ ] Run the full matrix — including the reversed order, which now names `agents` for the first time — and commit:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  # EXPECTED: whole suite green in all three; collected count up by exactly
  #   the number of tests test_tools.py contributes (parametrized cases
  #   expand, so count from the per-file run above, never by hand).

  git add agents pytest.ini
  git commit -m "$(cat <<'EOF'
  feat(agents): the tool contract's dataclasses

  agents/contracts/tools.py: ToolSpec, ToolResult, StepBudget,
  ToolContext, ToolRefused, validate_tool_args. Frozen dataclasses with a
  dotted-path `runner` STRING, matching RoleSpec/Operation/JobKind exactly
  -- so registration never imports the implementing module, which is the
  whole reason those three are dataclasses and not a class hierarchy.

  `__post_init__` rejects a "file" param (a tool call is JSON, ADR
  0012:140), a blank runner, a spaced or blank key, and a key containing
  "__" (which would make the wire-name round trip ambiguous).

  StepBudget is the one mutable object: a turn's allowance, shared by every
  tool call and delegated sub-agent. ToolContext stays frozen and holds a
  reference -- the shape JobContext already uses for injected closures.

  `describer` is declared and INERT per ruling R3; nothing calls it in
  P1-P4. ToolRefused is declared but caught by nothing until P2 -- a runner
  author needs to know which exception gets no retry, and this is the only
  place that can say so.

  pytest.ini gains `agents`: this is the phase that first creates
  agents/contracts/tests/, so those tests are otherwise not collected.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 3: `agents/contracts/toolschema.py` — rendering a tool to the LLM

**The decision, verified against the installed packages** (`llama-index-core 0.14.23`, `llama-index-llms-ollama 0.10.1`, `ollama 0.6.2`, `pydantic 2.13.4`; `requirements.txt:14-16` pins only floors, so nothing needs bumping): **emit the OpenAI-compatible tool dict ourselves and call `llm.chat(messages, tools=[...])`. Do not build `FunctionTool` objects and do not use `predict_and_call`.** Three reasons: `FunctionTool.from_defaults` (`site-packages/llama_index/core/tools/function_tool.py:172-184`) requires a live Python callable and synthesizes a pydantic schema from it, which would force an eager import of every tool module every time a prompt is built — the exact property the dotted-path design exists to avoid; it would install pydantic as a second validation floor beside `validate_params`, which ADR 0012:676-679 rules out; and `predict_and_call` runs the tool itself, hiding the step budget, the depth guard, and the recovery policy this design must own. All four of the installed integration's chat paths pop `tools` from kwargs and forward it to the SDK (`base.py:403,412`, `:453,463`, `:621,630`, `:537,547`), and the SDK's `tools` parameter accepts raw mappings (`ollama/_client.py:314,390-394`).

**Files**
- Create: `agents/contracts/toolschema.py`
- Create: `agents/contracts/tests/test_toolschema.py`
- Test: `agents/contracts/tests/test_toolschema.py`

**Interfaces**
- Consumes: `agents.contracts.tools.ToolSpec`; `models.contracts.operations.Param`.
- Produces:
  - `wire_name(key: str) -> str` — `"rag.search"` → `"rag__search"`
  - `key_from_wire_name(name: str) -> str` — the inverse
  - `openai_tool_dict(spec: ToolSpec) -> dict`

**Steps**

- [ ] Write the failing tests. `agents/contracts/tests/test_toolschema.py`:

  ```python
  """ToolSpec -> the tool-calling JSON an LLM reads (spec section 4.6)."""
  from __future__ import annotations

  import json

  import pytest

  from agents.contracts.tests._helpers import make_spec
  from agents.contracts.toolschema import key_from_wire_name, openai_tool_dict, wire_name
  from models.contracts.operations import Param


  class TestWireName:
      def test_a_dot_becomes_a_double_underscore(self):
          """The OpenAI function-name grammar is `^[a-zA-Z0-9_-]{1,64}$` and
          models are trained against it; a dotted name is a needless
          correctness risk even where a server tolerates it."""
          assert wire_name("rag.search") == "rag__search"

      def test_it_round_trips(self):
          for key in ("rag.search", "rag.ask", "vision.generate", "models.status"):
              assert key_from_wire_name(wire_name(key)) == key

      def test_the_emitted_name_fits_the_openai_grammar(self):
          import re

          for key in ("rag.search", "vision.operations", "models.status"):
              assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", wire_name(key))


  class TestOpenAIToolDict:
      def test_the_envelope_shape(self):
          spec = make_spec(key="rag.search", description="Search the library.")
          rendered = openai_tool_dict(spec)
          assert rendered["type"] == "function"
          assert rendered["function"]["name"] == "rag__search"
          assert rendered["function"]["description"] == "Search the library."
          assert rendered["function"]["parameters"]["type"] == "object"

      def test_it_is_json_serialisable(self):
          """It is posted as JSON to an HTTP API. A tuple or a dataclass
          leaking in here fails at request time, in a turn, on a worker."""
          rendered = openai_tool_dict(make_spec(params=(
              Param("mode", "choice", "Mode", choices=("a", "b")),
              Param("n", "int", "N", min=1, max=9),
          )))
          assert json.loads(json.dumps(rendered)) == rendered

      def test_each_param_kind_maps_to_its_json_type(self):
          spec = make_spec(params=(
              Param("t", "text", "T"), Param("s", "seed", "S"),
              Param("a", "asset", "A"), Param("c", "choice", "C"),
              Param("i", "int", "I"), Param("f", "float", "F"),
          ))
          props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
          assert props["t"]["type"] == "string"
          assert props["s"]["type"] == "string"
          assert props["a"]["type"] == "string"
          assert props["c"]["type"] == "string"
          assert props["i"]["type"] == "integer"
          assert props["f"]["type"] == "number"

      def test_description_falls_back_to_label(self):
          """`description` is the entire prompt surface a model gets for an
          argument (ruling R2 half one -- the field must never be removed,
          renamed, or made optional-by-omission)."""
          spec = make_spec(params=(
              Param("a", "text", "Alpha", description="What alpha means."),
              Param("b", "text", "Bravo"),
          ))
          props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
          assert props["a"]["description"] == "What alpha means."
          assert props["b"]["description"] == "Bravo"

      def test_a_fixed_choice_param_emits_an_enum(self):
          spec = make_spec(params=(Param("mode", "choice", "Mode", choices=("a", "b")),))
          props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
          assert props["mode"]["enum"] == ["a", "b"]

      def test_an_engine_owned_choice_param_emits_no_enum(self):
          """A `"choice"` param whose options the ENGINE owns (empty
          `choices`) emits no `enum` -- an honest "the schema does not
          know", exactly as `operations.describe` does
          (operations.py:161-163). Never a guessed list."""
          spec = make_spec(params=(Param("sampler", "choice", "Sampler"),))
          props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
          assert "enum" not in props["sampler"]

      def test_numeric_bounds_are_emitted_and_none_is_omitted(self):
          spec = make_spec(params=(
              Param("bounded", "int", "Bounded", min=1, max=50),
              Param("open", "float", "Open"),
          ))
          props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
          assert props["bounded"]["minimum"] == 1
          assert props["bounded"]["maximum"] == 50
          assert "minimum" not in props["open"]
          assert "maximum" not in props["open"]

      def test_a_multiple_param_becomes_an_array(self):
          """`validate_params` answers a `multiple` param with a LIST
          (operations.py:335-341). The schema must say so, or the model
          sends a bare string and the tool silently runs with one adapter
          where several were meant."""
          spec = make_spec(params=(
              Param("loras", "asset", "LoRAs", asset_kind="lora", multiple=True,
                    description="Adapters to apply."),
          ))
          prop = openai_tool_dict(spec)["function"]["parameters"]["properties"]["loras"]
          assert prop["type"] == "array"
          assert prop["items"]["type"] == "string"
          assert prop["description"] == "Adapters to apply."

      def test_required_lists_exactly_the_required_params(self):
          spec = make_spec(params=(
              Param("q", "text", "Q", required=True),
              Param("k", "int", "K"),
          ))
          assert openai_tool_dict(spec)["function"]["parameters"]["required"] == ["q"]

      def test_a_no_param_tool_renders_an_empty_object_schema(self):
          rendered = openai_tool_dict(make_spec(params=()))["function"]["parameters"]
          assert rendered == {"type": "object", "properties": {}, "required": []}

      def test_it_never_consults_describer(self):
          """Ruling R3: the schema handed to the model is built from the
          STATIC `ToolSpec.params` alone, on every turn. A describer whose
          dotted path does not even resolve must make no difference."""
          plain = openai_tool_dict(make_spec(params=(Param("a", "text", "A"),)))
          with_describer = openai_tool_dict(make_spec(
              params=(Param("a", "text", "A"),),
              describer="nowhere.at.all.describe",
          ))
          assert plain == with_describer

      def test_it_emits_no_live_facts_keys(self):
          """`supported`/`unsupported_reason`/`options` are reserved for
          R2/Phase 1.55 and are unreachable now."""
          props = openai_tool_dict(make_spec(params=(Param("a", "text", "A"),)))
          serialised = json.dumps(props)
          for reserved in ("supported", "unsupported_reason", "options"):
              assert reserved not in serialised
  ```

- [ ] Run and expect **red**, then implement `agents/contracts/toolschema.py`:

  ```python
  """A `ToolSpec` as the tool-calling JSON both Ollama's HTTP API and every
  OpenAI-compatible API accept -- and the wire-name round trip back.

  Verified against the installed packages (llama-index-core 0.14.23,
  llama-index-llms-ollama 0.10.1, ollama 0.6.2): all four chat paths pop
  `tools` from kwargs and forward it to the SDK, and the SDK's `tools`
  parameter accepts raw mappings. So this design emits the dict itself and
  calls `llm.chat(messages, tools=[...])`.

  It deliberately does NOT build `FunctionTool` objects and does NOT use
  `predict_and_call`:

  1. `FunctionTool.from_defaults` requires a LIVE Python callable and
     synthesizes a pydantic schema from it -- which would force an eager
     import of every tool module every time a prompt is built, the exact
     property `ToolSpec.runner`'s dotted-path design exists to avoid.
  2. It would install pydantic as a second validation floor beside
     `validate_params`, which ADR 0012:676-679 rules out.
  3. `predict_and_call` runs the tool itself, which would hide the step
     budget, the depth guard, and the recovery policy this design owns.

  Pure: no Django, no I/O, no engine.
  """
  from __future__ import annotations

  from agents.contracts.tools import ToolSpec

  _JSON_TYPES = {
      "text": "string", "seed": "string", "asset": "string",
      "choice": "string", "int": "integer", "float": "number",
  }


  def wire_name(key: str) -> str:
      """`"rag.search"` -> `"rag__search"`.

      The OpenAI function-name grammar is `^[a-zA-Z0-9_-]{1,64}$` and models
      are trained against it; a dotted name is a needless correctness risk
      even where a server tolerates it. `ToolSpec.__post_init__` rejects a
      key containing `"__"`, which is what makes this round trip
      unambiguous.
      """
      return key.replace(".", "__")


  def key_from_wire_name(name: str) -> str:
      """The inverse. A name that round-trips through neither direction is
      an unknown tool -- handled as a tool error (section 10.3), never
      guessed at, fuzzy-matched, or silently ignored."""
      return name.replace("__", ".")


  def openai_tool_dict(spec: ToolSpec) -> dict:
      """`spec` as the tool-calling JSON an LLM reads.

      Built from the STATIC `spec.params` alone, on every turn. It does not
      consult `spec.describer` and does not filter params by any live
      `supported` fact (ruling R3): a per-prompt describer call means an
      engine round trip inside the turn loop, and a tool schema that
      silently changes shape between turns is a debugging nightmare for no
      v1 benefit. A model that passes an argument the picked connection
      cannot honour gets the honest engine-side failure
      (`GenerationRejected` -> `ENGINE_REJECTED`,
      `tools/vision/services.py:654-655`), surfaced as a tool error with
      one recovery.

      A `"choice"` param whose options the ENGINE owns (empty
      `spec.params[i].choices`) emits no `enum` -- an honest "the schema
      does not know", exactly as `operations.describe` does
      (operations.py:161-163).
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
              # (operations.py:335-341), and the only such param today is
              # the asset one (LORA_PARAMS, operations.py:456-457). The
              # schema must say so, or the model sends a bare string and
              # the tool silently runs with one adapter where several were
              # meant.
              prop = {"type": "array", "items": prop, "description": prop["description"]}
          props[p.key] = prop
          if p.required:
              required.append(p.key)
      return {
          "type": "function",
          "function": {
              "name": wire_name(spec.key),
              "description": spec.description,
              "parameters": {"type": "object", "properties": props, "required": required},
          },
      }
  ```

- [ ] Run, expect green, run the matrix, and commit:

  ```bash
  .venv/bin/pytest -q agents/contracts/tests/test_toolschema.py
  # EXPECTED: green. Record the count -- parametrized cases expand.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  # EXPECTED: whole suite green; collected count up by exactly what
  #   test_toolschema.py contributed in the per-file run above.

  git add agents/contracts/toolschema.py agents/contracts/tests/test_toolschema.py
  git commit -m "$(cat <<'EOF'
  feat(agents): render a ToolSpec to the LLM tool-calling schema

  agents/contracts/toolschema.py: wire_name / key_from_wire_name /
  openai_tool_dict.

  We emit the OpenAI tool dict ourselves and call `llm.chat(messages,
  tools=[...])` rather than building FunctionTool objects or using
  predict_and_call. FunctionTool.from_defaults needs a LIVE callable and
  synthesizes a pydantic schema from it -- an eager import of every tool
  module on every prompt build, and a second validation floor beside
  validate_params (ADR 0012:676-679). predict_and_call would run the tool
  itself and hide the step budget, the depth guard, and the recovery
  policy. Verified against the installed integration: all four chat paths
  forward `tools` to the SDK, which accepts raw mappings.

  A `multiple` param renders as {"type":"array","items":{...}} because
  validate_params answers one with a LIST -- otherwise the model sends a
  bare string and the tool silently runs with one adapter where several
  were meant. An engine-owned `choice` emits no enum: honest "the schema
  does not know", never a guessed list.

  Pinned inert per ruling R3: `describer` is never consulted, and no
  supported/unsupported_reason/options key is ever emitted.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 4: the registry and `describe_tool`

Mirrors `models/contracts/roles.py:78-97` and `jobkinds.py:240-270` exactly, **including the raise-vs-None split**: `get_role` returns `None` because a role may legitimately be absent; `get_job_kind` raises because enqueuing an unregistered kind is a caller bug. A tool call for an unregistered tool is likewise a bug, so `get_tool` raises — and P2's prompt builder therefore uses a separate None-returning `_TOOLS.get(key)` lookup for the case where an absent key is *normal*.

**Files**
- Modify: `agents/contracts/tools.py` — append `_TOOLS`, `register_tool`, `all_tools`, `get_tool`, `grantable_tools`, `describe_tool`
- Create: `agents/contracts/tests/test_registry.py`
- Test: `agents/contracts/tests/test_registry.py`

**Interfaces**
- Consumes: `models.contracts.operations._describe_param` (`operations.py:178-206`).
- Produces:
  - `register_tool(spec: ToolSpec) -> None` — idempotent, registration order preserved
  - `all_tools() -> list[ToolSpec]`
  - `get_tool(key: str) -> ToolSpec` — **raises `ValueError` naming the key**
  - `grantable_tools() -> list[ToolSpec]` — every `mutates=False` spec
  - `describe_tool(spec: ToolSpec) -> dict` — keys exactly `{"key", "label", "description", "roles", "mutates", "params"}`

**Steps**

- [ ] Write the failing tests. `agents/contracts/tests/test_registry.py`:

  ```python
  """The tool registry and `describe_tool` (spec section 4.3)."""
  from __future__ import annotations

  import json

  import pytest

  from agents.contracts import tools as tools_module
  from agents.contracts.tests._helpers import make_spec, restore_tools, snapshot_tools
  from agents.contracts.tools import (
      ToolSpec, all_tools, describe_tool, get_tool, grantable_tools, register_tool,
  )
  from models.contracts.operations import Param


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      """Defined here, body delegated to `_helpers` -- the repo convention
      (no conftest.py anywhere). A module-global registry surviving between
      tests is exactly the state that makes a suite pass in one collection
      order and fail in the other."""
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  class TestRegisterTool:
      def test_it_registers_under_the_specs_key(self):
          register_tool(make_spec(key="x.one"))
          assert get_tool("x.one").key == "x.one"

      def test_it_is_idempotent_like_its_siblings(self):
          """Matches `register_role` and `register_job_kind`: registering
          the same key again overwrites, so re-importing a module that
          registers at import time is safe."""
          register_tool(make_spec(key="x.one", label="First"))
          register_tool(make_spec(key="x.one", label="Second"))
          assert get_tool("x.one").label == "Second"
          assert [t.key for t in all_tools()].count("x.one") == 1

      def test_all_tools_preserves_registration_order(self):
          before = len(all_tools())
          register_tool(make_spec(key="x.one"))
          register_tool(make_spec(key="x.two"))
          register_tool(make_spec(key="x.three"))
          assert [t.key for t in all_tools()[before:]] == ["x.one", "x.two", "x.three"]


  class TestGetTool:
      def test_it_raises_naming_the_key(self):
          """RAISES, not None -- matching `get_job_kind`, not `get_role`. A
          tool call for an unregistered tool is a caller bug, not a state
          to degrade gracefully from."""
          with pytest.raises(ValueError) as excinfo:
              get_tool("nope.missing")
          assert "nope.missing" in str(excinfo.value)

      def test_the_none_returning_lookup_exists_for_the_normal_absent_case(self):
          """P2's prompt builder drops an unregistered granted key rather
          than crashing, so it needs a lookup that does not raise."""
          assert tools_module._TOOLS.get("nope.missing") is None


  class TestGrantableTools:
      def test_it_excludes_every_mutating_spec(self):
          """ADR 0010:266-276 -- a settings-mutating tool is registered (so
          it is visible, documented, and testable) but must not be
          grantable before Identity & Auth."""
          register_tool(make_spec(key="x.read"))
          register_tool(make_spec(key="x.write", mutates=True))
          keys = {t.key for t in grantable_tools()}
          assert "x.read" in keys
          assert "x.write" not in keys

      def test_a_mutating_tool_is_still_registered_and_findable(self):
          register_tool(make_spec(key="x.write", mutates=True))
          assert get_tool("x.write").mutates is True
          assert "x.write" in {t.key for t in all_tools()}


  class TestDescribeTool:
      def test_it_emits_one_key_per_serialised_field_and_no_more(self):
          """Written out explicitly (one key per field) rather than via
          `dataclasses.asdict`, so a field added without a serialization
          decision fails THIS test instead of silently appearing in a
          public contract -- the exact rationale
          `models/contracts/operations.py:178-182` gives for
          `_describe_param`."""
          described = describe_tool(make_spec())
          assert set(described) == {
              "key", "label", "description", "roles", "mutates", "params",
          }

      def test_runner_and_describer_are_deliberately_absent(self):
          """Both are INTERNAL: one is a dotted path into this codebase,
          the other is reserved. Neither belongs in a contract handed to an
          LLM, an HTTP client, or a future MCP surface. This asserts their
          ABSENCE, not their presence."""
          described = describe_tool(make_spec(describer="some.path.describe"))
          assert "runner" not in described
          assert "describer" not in described

      def test_it_carries_no_live_facts_keys(self):
          """Ruling R3: `supported`/`unsupported_reason`/`options` are
          reserved for R2/Phase 1.55 and unreachable now -- nothing calls a
          describer, so nothing can produce them."""
          described = describe_tool(make_spec(params=(Param("a", "text", "A"),)))
          serialised = json.dumps(described)
          for reserved in ("supported", "unsupported_reason", "options"):
              assert reserved not in serialised

      def test_it_is_json_safe(self):
          described = describe_tool(make_spec(
              roles=("rag.embed",),
              params=(Param("mode", "choice", "Mode", choices=("a", "b")),),
          ))
          assert json.loads(json.dumps(described)) == described
          assert described["roles"] == ["rag.embed"]        # list, not tuple
          assert described["params"][0]["choices"] == ["a", "b"]

      def test_params_reuse_the_operations_describer(self):
          """Not a second copy of the param-serialisation rule."""
          from models.contracts.operations import _describe_param

          param = Param("a", "text", "A", description="Alpha.")
          assert describe_tool(make_spec(params=(param,)))["params"] == [
              _describe_param(param)
          ]

      def test_every_toolspec_field_is_either_serialised_or_named_internal(self):
          """The anti-drift pin: a NEW ToolSpec field must be added to
          `describe_tool` or added to `_INTERNAL_FIELDS` with a reason. It
          cannot be silently ignored."""
          import dataclasses

          declared = {f.name for f in dataclasses.fields(ToolSpec)}
          serialised = set(describe_tool(make_spec()))
          internal = {"runner", "describer"}
          assert declared == serialised | internal
  ```

- [ ] Run and expect **red**, then append to `agents/contracts/tools.py`:

  ```python
  _TOOLS: dict[str, ToolSpec] = {}


  def register_tool(spec: ToolSpec) -> None:
      """Register `spec` under its `.key`, replacing any existing entry.

      Idempotent, like `register_role` (roles.py:81) and
      `register_job_kind` (jobkinds.py:243): registering the same key again
      simply overwrites the prior entry, so re-importing a module that
      registers at import time is safe.
      """
      _TOOLS[spec.key] = spec


  def all_tools() -> list[ToolSpec]:
      """Every registered tool, in registration order."""
      return list(_TOOLS.values())


  def get_tool(key: str) -> ToolSpec:
      """The tool registered under `key`.

      RAISES `ValueError` naming it if absent -- matching `get_job_kind`
      (jobkinds.py:258), not `get_role` (roles.py:95). A role may
      legitimately be absent; a tool call for an unregistered tool is a
      caller bug, not a state to degrade gracefully from.

      A caller for whom an absent key is NORMAL -- P2's prompt builder,
      which drops an unregistered granted key rather than crashing -- uses
      `_TOOLS.get(key)` instead, deliberately.
      """
      try:
          return _TOOLS[key]
      except KeyError:
          raise ValueError(f"Unknown tool: {key!r}") from None


  def grantable_tools() -> list[ToolSpec]:
      """Every registered tool with `mutates=False` -- the set an agent row
      may name in its `tool_keys`.

      ADR 0010:266-276: a settings-mutating tool is registered (so it is
      visible, documented, and testable) but must not be grantable before
      Identity & Auth.
      """
      return [spec for spec in _TOOLS.values() if not spec.mutates]


  def describe_tool(spec: ToolSpec) -> dict:
      """`spec` as plain, JSON-safe data.

      Written out explicitly -- one key per field -- rather than via
      `dataclasses.asdict`, so a field added without a serialization
      decision fails a test instead of silently appearing in a public
      contract. That is the exact rationale
      `models/contracts/operations.py:178-182` gives for `_describe_param`,
      which this reuses for the `params` list rather than growing a second
      copy of the param-serialisation rule.

      `runner` and `describer` are INTERNAL and deliberately unserialized:
      one is a dotted path into this codebase, the other is reserved
      (section 4.8) -- both are implementation, and neither belongs in a
      contract handed to an LLM, an HTTP client, or a future MCP surface.
      The one-key-per-serialized-field test therefore asserts their
      ABSENCE, not their presence.

      In P1-P4 this output carries NO live-facts keys. Section 4.8's
      `supported` / `unsupported_reason` / `options` are reserved for
      R2/Phase 1.55 and are unreachable now: nothing calls a `describer`,
      so nothing can produce them (ruling R3).
      """
      return {
          "key": spec.key,
          "label": spec.label,
          "description": spec.description,
          "roles": list(spec.roles),
          "mutates": spec.mutates,
          "params": [_describe_param(p) for p in spec.params],
      }
  ```

  Add `_describe_param` to the module's import from `models.contracts.operations`, with a comment noting it is a deliberate reuse of a private name rather than a third copy of the serialisation rule.

- [ ] Run, expect green, run the matrix, and commit:

  ```bash
  .venv/bin/pytest -q agents/contracts/tests/test_registry.py
  # EXPECTED: green. Record the count.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  # EXPECTED: whole suite green; collected count up by exactly what
  #   test_registry.py contributed in the per-file run above.

  git add agents/contracts/tools.py agents/contracts/tests/test_registry.py
  git commit -m "$(cat <<'EOF'
  feat(agents): the tool registry and describe_tool

  register_tool / all_tools / get_tool / grantable_tools / describe_tool,
  mirroring roles.py:78-97 and jobkinds.py:240-270 -- including the
  raise-vs-None split. `get_tool` RAISES naming the key, like
  `get_job_kind`: a role may legitimately be absent, a tool call for an
  unregistered tool is a caller bug. P2's prompt builder, for which an
  absent key IS normal, uses the None-returning `_TOOLS.get` instead, and
  a test pins that both exist.

  `grantable_tools` excludes every mutates=True spec (ADR 0010:266-276): a
  settings-mutating tool is registered -- visible, documented, testable --
  but not grantable before Identity & Auth.

  `describe_tool` writes one key per field explicitly, so a NEW ToolSpec
  field must be serialised or named internal; it cannot be silently
  ignored. `runner` and `describer` are asserted ABSENT: implementation,
  not contract. No live-facts keys in P1-P4 (ruling R3).

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 5: `agents/contracts/artifacts.py` — the artifact-reference vocabulary

An artifact is a JSON-safe *reference string*, never a filesystem path and never bytes. `"output"` and `"input"` are vision's own — already parsed by `tools.vision.services.parse_input_reference` (`services.py:379-389`), already minted by `stage_upload` (`services.py:503-508`) and by `templates/vision/_output_actions.html`. `"document"` is this spec's one addition, for a RAG source file.

**Files**
- Create: `agents/contracts/artifacts.py`
- Create: `agents/contracts/tests/test_artifacts.py`
- Test: `agents/contracts/tests/test_artifacts.py`

**Interfaces**
- Consumes: nothing (pure; the URL *names* are string literals, not `reverse()` calls — this module never imports Django).
- Produces:
  - `ARTIFACT_KINDS = ("output", "input", "document")`
  - `parse_artifact(reference: str) -> tuple[str, int]` — raises `ValueError` naming the shape
  - `artifact_url_name(kind: str) -> str` — `"output"` → `"vision-output-file"` (`tools/vision/urls.py:23`), `"input"` → `"vision-input-file"` (`urls.py:24`), `"document"` → `"rag-document-file"` (`tools/rag/urls.py:34`); raises `ValueError` for anything else

**`tools.vision.services.parse_input_reference` stays the authority for the two kinds vision owns and is NOT widened.** When an artifact is fed back *into* a vision tool, `services.resolve_inputs(operation, references)` (`services.py:452`) is the caller and it must keep refusing anything but `output:`/`input:`. Task 5 adds a test that the two parsers agree on the two kinds they share, so a reference vision minted parses identically here.

**Steps**

- [ ] Write the failing tests. `agents/contracts/tests/test_artifacts.py`:

  ```python
  """The artifact-reference vocabulary (spec section 4.5).

  ONE DELIBERATE COLUMN CROSSING, and it is the point of the test that makes
  it: `test_it_agrees_with_visions_own_parser_on_the_two_kinds_they_share`
  imports `tools.vision.services`. A test importing across a column is not
  the import law's business -- rule 2 governs what PRODUCTION code may
  import, and `agents/contracts/artifacts.py` itself imports nothing but the
  standard library. The crossing exists precisely so the two parsers cannot
  drift: a reference vision MINTED must parse identically here, and the only
  way to assert that is to run both.

  This module also calls `reverse()`. It overrides `FARABUNKER_FEATURES`
  nowhere, so THE VISION-FLAG RULE is satisfied trivially -- both supported
  gate states carry "vision", and the two vision routes exist in both.
  """
  from __future__ import annotations

  import pytest
  from django.urls import reverse

  from agents.contracts.artifacts import ARTIFACT_KINDS, artifact_url_name, parse_artifact


  class TestParseArtifact:
      @pytest.mark.parametrize(
          "reference,expected",
          [("output:12", ("output", 12)), ("input:3", ("input", 3)),
           ("document:451", ("document", 451))],
      )
      def test_it_splits_a_valid_reference(self, reference, expected):
          assert parse_artifact(reference) == expected

      @pytest.mark.parametrize(
          "reference",
          ["", "output", "output:", ":12", "output:abc", "output:-1", "output:1.5",
           "nope:12", "/etc/passwd", "output:12:extra", None],
      )
      def test_it_refuses_anything_else_naming_the_shape(self, reference):
          """This value arrives from a queue payload, a link, or a tool
          call, so it is untrusted input and a clear refusal beats a
          confusing failure later -- the same rule
          `tools/vision/services.py:384-389` already states."""
          with pytest.raises(ValueError) as excinfo:
              parse_artifact(reference)
          assert "output:<id>" in str(excinfo.value)

      def test_it_agrees_with_visions_own_parser_on_the_two_kinds_they_share(self):
          """A reference that vision MINTED must parse identically here.
          `parse_input_reference` stays the authority for output/input and
          is deliberately NOT widened -- `resolve_inputs` must keep
          refusing a `document:` reference fed into a generation."""
          from tools.vision.services import parse_input_reference

          for reference in ("output:12", "input:3"):
              assert parse_artifact(reference) == parse_input_reference(reference)

          with pytest.raises(ValueError):
              parse_input_reference("document:451")


  class TestArtifactUrlName:
      @pytest.mark.parametrize(
          "kind,name",
          [("output", "vision-output-file"), ("input", "vision-input-file"),
           ("document", "rag-document-file")],
      )
      def test_each_kind_names_the_view_that_serves_its_bytes(self, kind, name):
          assert artifact_url_name(kind) == name

      def test_it_refuses_an_unknown_kind(self):
          with pytest.raises(ValueError, match="nope"):
              artifact_url_name("nope")

      def test_every_declared_kind_has_a_url_name(self):
          assert {artifact_url_name(k) for k in ARTIFACT_KINDS} == {
              "vision-output-file", "vision-input-file", "rag-document-file",
          }

      def test_every_url_name_actually_reverses(self):
          """The names are string literals in a pure module, so nothing
          else proves they are real routes. This does.

          No `FARABUNKER_FEATURES` override anywhere in this file, so THE
          VISION-FLAG RULE is satisfied trivially: both supported gate
          states ('vision,media' and 'vision') carry "vision", and the two
          vision routes exist in both.
          """
          assert reverse("vision-output-file", args=[1]).endswith("/outputs/1/file/")
          assert reverse("vision-input-file", args=[1]).endswith("/inputs/1/file/")
          assert reverse("rag-document-file", args=[1]).endswith("/documents/1/file/")
  ```

- [ ] Run and expect **red**, then implement `agents/contracts/artifacts.py`:

  ```python
  """The artifact-reference vocabulary.

  An artifact is a JSON-safe REFERENCE STRING -- never a filesystem path,
  never bytes. A tool that produces a file hands back `"output:12"` or
  `"document:451"`; the chat template reverses the matching URL name and
  serves it. No layer below the view ever learns a path on a disk the
  caller cannot see, which is the same rule `tools/vision/services.py:742`
  (`job_json`) already enforces for the vision page.

  `"output"`/`"input"` are vision's own, already parsed by
  `tools.vision.services.parse_input_reference` (services.py:379-389) and
  already minted by `stage_upload` (services.py:503-508) and by
  `templates/vision/_output_actions.html`. `"document"` is this spec's one
  addition, for a RAG source file.

  Pure: no Django. The URL NAMES below are string literals; reversing them
  is the caller's job (a template, a view), not this module's.
  """
  from __future__ import annotations

  ARTIFACT_KINDS = ("output", "input", "document")

  _SHAPE = "an artifact reference looks like output:<id>, input:<id>, or document:<id>"

  # Which view serves each kind's bytes. `tools/vision/urls.py:23-24` and
  # `tools/rag/urls.py:34`. URL NAMES, never dotted paths -- so a package
  # move never touches this map.
  _URL_NAMES = {
      "output": "vision-output-file",
      "input": "vision-input-file",
      "document": "rag-document-file",
  }


  def parse_artifact(reference: str) -> tuple[str, int]:
      """`(kind, pk)` for `reference`, or `ValueError` naming the shape.

      The same `partition(":")` + `isdigit()` shape as vision's own parser,
      deliberately -- a reference that vision minted must parse identically
      here. This value arrives from a queue payload, a link, or a tool
      call, so it is untrusted input and a clear refusal beats a confusing
      failure later.
      """
      kind, _, raw_id = str(reference or "").partition(":")
      if kind not in ARTIFACT_KINDS or not raw_id.isdigit():
          raise ValueError(f"{reference!r} is not an artifact reference — {_SHAPE}.")
      return kind, int(raw_id)


  def artifact_url_name(kind: str) -> str:
      """The URL name that serves this artifact kind's bytes.

      The chat template reverses this; no layer below the view ever learns
      a filesystem path.
      """
      try:
          return _URL_NAMES[kind]
      except KeyError:
          raise ValueError(
              f"{kind!r} is not an artifact kind; must be one of {list(ARTIFACT_KINDS)}"
          ) from None
  ```

- [ ] Run, expect green, run the matrix, and commit:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q agents/contracts/tests/test_artifacts.py
  # EXPECTED: green. Record the count -- parametrized cases expand.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  # EXPECTED: whole suite green; collected count up by exactly what
  #   test_artifacts.py contributed in the per-file run above.

  git add agents/contracts/artifacts.py agents/contracts/tests/test_artifacts.py
  git commit -m "$(cat <<'EOF'
  feat(agents): the artifact-reference vocabulary

  agents/contracts/artifacts.py: ARTIFACT_KINDS, parse_artifact,
  artifact_url_name. An artifact is a JSON-safe reference string --
  "output:12", "input:3", "document:451" -- never a path and never bytes,
  the same rule services.job_json already enforces for the vision page.

  Same partition(":") + isdigit() shape as vision's own parser,
  deliberately: a test asserts the two agree on the two kinds they share.
  `parse_input_reference` stays the authority for output/input and is NOT
  widened -- `resolve_inputs` must keep refusing a `document:` reference
  fed into a generation, and a test pins that too.

  The URL names are string literals in a pure module, so a test reverses
  all three to prove they are real routes.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 6: `InferenceEngine.supports_tool_calling` — a measurement, never a claim

`Ollama.metadata.is_function_calling_model` is **hardcoded `True`** (`site-packages/llama_index/llms/ollama/base.py:127-130`, `:189-198`) with an upstream `# TODO: Detect if selected model is a function calling model?` at `base.py:196`. It is a declaration, not a detection, and must not be used as a check. The real signal is one layer down: Ollama's `/api/show` response carries a `capabilities` list.

**Live probe, run 2026-08-25 against the host Ollama at `localhost:11434`** — this is the probe the spec §4.7 asked P1 to perform, and its result is recorded here rather than left as a question:

| Model kind | `POST /api/show` → `capabilities` | `supports_tool_calling` |
|---|---|---|
| a chat model | `["completion", "tools"]` | `True` |
| an embedding model | `["embedding"]` | `False` |
| a vision-capable chat model | `["completion", "vision"]` | `False` |

`POST /api/show` (not GET) with body `{"model": "<id>"}`. Response keys observed: `capabilities`, `details`, `license`, `model_info`, `modelfile`, `modified_at`, `parameters`, `template`, `tensors`.

**Second finding from the same probe, correcting spec §4.7 and §14 gap 12:** `/api/tags` rows on this server **do** carry `capabilities`, even though the SDK's typed `ListResponse.Model` does not declare the field (`ollama/_types.py:518-527`). So `models/contracts/engines/ollama.py:253`'s `model.get("capabilities", [])` is not dead code and `list_installed` really does report mapped capabilities. This does not change the design — `/api/show` stays the source for `supports_tool_calling`, because `/api/tags` is a discovery listing whose per-row shape is not contractual — but it does mean the spec's "which may always be absent" is not what the installed server does.

**Files**
- Modify: `models/contracts/engines/base.py` — declare `supports_tool_calling` alongside `loaded_footprint`/`unload` (`base.py:404-421`)
- Modify: `models/contracts/engines/ollama.py` — implement it
- Modify: `models/registry/tests/_helpers.py` — add `fake_ollama_show(...)`
- Modify: `models/registry/tests/test_engines.py` — new test class
- Test: `models/registry/tests/test_engines.py`

**Interfaces**
- Consumes: `httpx.post`; `DISCOVERY_TIMEOUT` (`ollama.py:45`).
- Produces: `InferenceEngine.supports_tool_calling(model_id: str, endpoint: str) -> bool | None` — **optional**, read via `getattr(engine, "supports_tool_calling", None)` so an adapter predating it degrades to `None`, never `AttributeError`. `OllamaEngine` implements it; `ComfyUIEngine` and `WhisperEngine` deliberately do not.

**`"tools"` is deliberately NOT added to `CAPABILITIES`** (`models/contracts/roles.py:19`). That set is the *role* vocabulary, validated by `RoleSpec.__post_init__` (`roles.py:71-75`) and, per ADR 0010 §3 (`0010:137-141`), it *is* the manifest vocabulary. "Can call tools" is a model trait, not a purpose a role can name; adding it would let someone register a nonsensical `RoleSpec(..., capability="tools")`. The implementation reads the **raw** `"tools"` string and does **not** touch `_map_capabilities` (`ollama.py:91-105`) or `_CAPABILITY_MAP` (`ollama.py:63-67`), so the existing test that pins `"tools"` being dropped from the platform vocabulary — `models/registry/tests/test_engines.py:254-262`, `test_unknown_engine_capability_is_dropped` — stays green, untouched.

**Steps**

- [ ] Add the HTTP-layer double to `models/registry/tests/_helpers.py`, beside the existing `fake_ollama_get` (`:104-128`):

  ```python
  def fake_ollama_show(capabilities=None, *, status_error=False, connect_error=False,
                       omit_capabilities=False):
      """Build an `httpx.post` side_effect for `/api/show`, for patching
      `models.contracts.engines.ollama.httpx.post` (repo convention: mock
      at the HTTP layer, never the engine's own methods away, so the
      adapter's real URL building and parsing are exercised -- the same
      rule `tools/vision/tests/_helpers.py:8-12` states for FakeComfyUI).

      Shapes observed live on 2026-08-25: a chat model reports
      `["completion", "tools"]`, an embedding model `["embedding"]`, a
      vision-capable chat model `["completion", "vision"]`.
      """
      import httpx

      def fake_post(url, json=None, timeout=None):
          assert url.endswith("/api/show"), f"unexpected url: {url}"
          assert json and "model" in json, "the adapter must name the model in the body"
          if connect_error:
              raise httpx.ConnectError("refused")
          response = MagicMock()
          if status_error:
              response.raise_for_status.side_effect = httpx.HTTPStatusError(
                  "404", request=MagicMock(), response=MagicMock(),
              )
              return response
          response.raise_for_status.return_value = None
          body = {} if omit_capabilities else {"capabilities": list(capabilities or [])}
          response.json.return_value = body
          return response

      return fake_post
  ```

- [ ] Write the failing tests. Append to `models/registry/tests/test_engines.py`:

  ```python
  class TestOllamaSupportsToolCalling:
      """`Ollama.metadata.is_function_calling_model` is hardcoded True
      upstream (llama_index/llms/ollama/base.py:127-130,189-198, with its
      own `# TODO: Detect...` at :196). It is a DECLARATION, not a
      detection, and this platform must not use it as a check. The real
      signal is `POST /api/show`'s `capabilities` list."""

      @patch("models.contracts.engines.ollama.httpx.post")
      def test_a_model_reporting_tools_is_true(self, mock_post):
          mock_post.side_effect = fake_ollama_show(["completion", "tools"])
          assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is True

      @patch("models.contracts.engines.ollama.httpx.post")
      def test_a_model_not_reporting_tools_is_false(self, mock_post):
          mock_post.side_effect = fake_ollama_show(["completion", "vision"])
          assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is False

      @patch("models.contracts.engines.ollama.httpx.post")
      def test_an_embedding_model_is_false(self, mock_post):
          mock_post.side_effect = fake_ollama_show(["embedding"])
          assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is False

      @patch("models.contracts.engines.ollama.httpx.post")
      def test_a_response_without_a_capabilities_key_is_none_not_false(self, mock_post):
          """`None` means "this engine does not report it", which is a
          different fact from "it reports that it cannot". The chat
          preflight refuses on False and RUNS on None (section 4.7)."""
          mock_post.side_effect = fake_ollama_show(omit_capabilities=True)
          assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is None

      @patch("models.contracts.engines.ollama.httpx.post")
      def test_an_unreachable_engine_is_none_never_an_exception(self, mock_post):
          mock_post.side_effect = fake_ollama_show(connect_error=True)
          assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is None

      @patch("models.contracts.engines.ollama.httpx.post")
      def test_a_non_2xx_response_is_none_never_an_exception(self, mock_post):
          mock_post.side_effect = fake_ollama_show(status_error=True)
          assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is None

      @patch("models.contracts.engines.ollama.httpx.post")
      def test_it_reads_the_raw_string_and_never_the_platform_vocabulary(self, mock_post):
          """It must not route through `_map_capabilities`, which DROPS
          "tools" because that string has no platform-capability
          equivalent. Routing through it would make this method always
          False."""
          from models.contracts.engines.ollama import _map_capabilities

          assert _map_capabilities(["completion", "tools"]) == ("chat",)
          mock_post.side_effect = fake_ollama_show(["completion", "tools"])
          assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is True

      def test_tools_is_not_in_the_platform_capability_vocabulary(self):
          """`CAPABILITIES` is the ROLE vocabulary and, per ADR
          0010:137-141, the manifest vocabulary. "Can call tools" is a
          model trait, not a purpose a role can name -- adding it would let
          someone register a nonsensical RoleSpec(capability="tools")."""
          from models.contracts.roles import CAPABILITIES

          assert "tools" not in CAPABILITIES


  class TestSupportsToolCallingIsOptional:
      def test_an_adapter_without_it_degrades_to_none_not_attributeerror(self):
          """Callers read it defensively via `getattr(engine,
          "supports_tool_calling", None)`, the same shape
          `loaded_footprint`/`unload` already use (base.py:400-406, the block
          comment that states the rule), so a
          third-party or stub adapter that predates this method degrades to
          "no measurement" rather than crashing a preflight."""
          from models.contracts.engines import get_engine

          # `InferenceEngine` is a Protocol (base.py:274) and no adapter
          # subclasses it -- typing here is purely structural -- so an
          # adapter that has not implemented the method really does have no
          # such attribute, and `getattr(..., None)` is the whole guard.
          for name in ("comfyui", "whisper"):
              assert getattr(get_engine(name), "supports_tool_calling", None) is None

          assert callable(getattr(get_engine("ollama"), "supports_tool_calling", None))

      def test_the_protocol_declares_it_so_an_adapter_author_can_find_it(self):
          """Declaring it on the Protocol (rather than leaving it
          undocumented) is what lets a future adapter's author discover the
          seam at all -- the reason base.py:400-406 gives for declaring
          `loaded_footprint`/`unload` there."""
          from models.contracts.engines.base import InferenceEngine

          assert "supports_tool_calling" in InferenceEngine.__dict__
  ```

  Add `fake_ollama_show` to the module's import from `._helpers`.

- [ ] Run and expect **red**:

  ```bash
  .venv/bin/pytest -q models/registry/tests/test_engines.py -k ToolCalling
  # EXPECTED: AttributeError: 'OllamaEngine' object has no attribute 'supports_tool_calling'
  ```

- [ ] Declare it on the protocol. In `models/contracts/engines/base.py`, immediately after `unload` (which ends at `base.py:431`), inside the same "Optional: the execution queue's engine seam" block that opens at `base.py:400`:

  ```python
      def supports_tool_calling(self, model_id: str, endpoint: str) -> bool | None:
          """Whether `model_id` at `endpoint` can be driven with TOOL CALLS,
          or `None` if this engine does not report it -- the same
          opportunistic-fact shape as `loaded_footprint` above: a
          measurement, never a claim.

          OPTIONAL, like the two methods above. Called via
          `getattr(engine, "supports_tool_calling", None)`, so an adapter
          predating this method degrades to `None`, never
          `AttributeError`.

          Three-valued on purpose. `False` means the engine reported its
          capabilities and tool calling was not among them -- the chat
          preflight refuses BEFORE enqueue with an honest 503 naming the
          role. `None` means the engine does not report the fact at all --
          the turn runs, and if the model emits no tool call that is a
          final answer, honestly indistinguishable from a model that chose
          not to use one. Never parse a tool call out of prose, never
          inject a hand-rolled syntax into the system prompt, never retry
          with a "please respond in JSON" nudge.
          """
          ...
  ```

- [ ] Implement it on `OllamaEngine`, after `unload` in `models/contracts/engines/ollama.py`:

  ```python
      def supports_tool_calling(self, model_id: str, endpoint: str) -> bool | None:
          """Ask Ollama whether `model_id` reports the `"tools"` capability.

          `POST /api/show` with `{"model": model_id}` returns a
          `capabilities` list. Verified live on 2026-08-25: a chat model
          reports `["completion", "tools"]`, an embedding model
          `["embedding"]`, and a vision-capable chat model
          `["completion", "vision"]`.

          Reads the RAW `"tools"` string. It deliberately does NOT route
          through `_map_capabilities`/`_CAPABILITY_MAP` above, which DROP
          `"tools"` because it has no platform-capability equivalent --
          routing through them would make this method always `False`. That
          drop is correct and is pinned by
          `test_unknown_engine_capability_is_dropped`; `"tools"` is a model
          trait, not a role purpose, so it stays out of `CAPABILITIES`.

          Uses `DISCOVERY_TIMEOUT`: this is metadata, not a generation. It
          loads nothing -- `/api/show` reads the manifest, it does not
          bring the model into memory.

          Returns `None` rather than raising on any HTTP failure, and
          `None` rather than `False` when the response carries no
          `capabilities` key at all -- "the engine does not report it" is a
          different fact from "the engine reports it cannot", and the
          caller acts differently on each.
          """
          try:
              response = httpx.post(
                  f"{endpoint}/api/show",
                  json={"model": model_id},
                  timeout=DISCOVERY_TIMEOUT,
              )
              response.raise_for_status()
              reported = response.json().get("capabilities")
          except (httpx.HTTPError, ValueError):
              return None
          if reported is None:
              return None
          return "tools" in reported
  ```

- [ ] Run, expect green, and confirm the pre-existing capability-drop test is untouched:

  ```bash
  .venv/bin/pytest -q models/registry/tests/test_engines.py
  # EXPECTED: all passed, INCLUDING test_unknown_engine_capability_is_dropped
  #   with no edit to it whatsoever.
  git diff --stat models/registry/tests/test_engines.py
  # EXPECTED: additions only.
  ```

- [ ] **Confirm the live probe by hand** against the host Ollama, so the numbers in this task are re-verified rather than trusted. `/api/show` loads nothing — it reads the manifest — so this is safe under the never-load-outside-the-queue rule:

  ```bash
  curl -s --max-time 10 -X POST http://localhost:11434/api/show \
       -d '{"model":"<a chat model id from /api/tags>"}' \
    | .venv/bin/python -c "import sys,json; print(json.load(sys.stdin).get('capabilities'))"
  # EXPECTED: a list containing "tools" for a tool-calling chat model.
  ```

- [ ] Run the matrix and commit:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  # EXPECTED: whole suite green; collected count up by exactly what the new
  #   test_engines.py classes contributed. No pre-existing test changed.

  git add models/contracts/engines/base.py models/contracts/engines/ollama.py \
          models/registry/tests/_helpers.py models/registry/tests/test_engines.py
  git commit -m "$(cat <<'EOF'
  feat(engines): optional supports_tool_calling, measured not declared

  The installed integration hardcodes `is_function_calling_model = True`
  with its own "# TODO: Detect..." next to it. That is a declaration, not a
  detection, and this platform must not use it as a check.

  New OPTIONAL InferenceEngine.supports_tool_calling(model_id, endpoint) ->
  bool | None, declared beside loaded_footprint/unload and read the same
  defensive way (getattr), so an adapter predating it degrades to None
  rather than AttributeError. OllamaEngine implements it against POST
  /api/show's `capabilities` list.

  Three-valued on purpose: False = the engine reported and tool calling was
  not among them (the P2 preflight refuses before enqueue); None = the
  engine does not report it (the turn runs, and no tool call IS a final
  answer). We never parse a tool call out of prose.

  Reads the RAW "tools" string and never routes through
  _map_capabilities, which correctly DROPS it -- so
  test_unknown_engine_capability_is_dropped stays green, untouched.
  "tools" stays OUT of CAPABILITIES: that set is the ROLE vocabulary (ADR
  0010:137-141) and "can call tools" is a model trait, not a purpose a role
  can name.

  Live-probed 2026-08-25: a chat model reports ["completion","tools"], an
  embedding model ["embedding"], a vision chat model
  ["completion","vision"]. Same probe found that /api/tags rows DO carry
  `capabilities` on the installed server, so ollama.py's
  `model.get("capabilities", [])` is not dead code -- correcting the
  spec's "may always be absent".

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 7: `tools/rag/tools.py` — `rag.search`, `rag.ask`, `rag.ingest`

Each maps onto an existing service function. None grows a parallel seam.

| Tool key | `roles` | `mutates` | Runner → existing function |
|---|---|---|---|
| `rag.search` | `("rag.embed",)` | no | `retrieval.retrieve_nodes(...)` (`tools/rag/retrieval.py:344`) then `retrieval.apply_score_floor(nodes, score_floor, hybrid=hybrid)` (`retrieval.py:435`) — the no-LLM path `SearchView` uses |
| `rag.ask` | `("rag.answer", "rag.embed")` | no | `retrieval.answer_question(...)` (`retrieval.py:461`) — the exact seam ADR 0010:277-280 names |
| `rag.ingest` | `("rag.embed",)` | **yes** | `ingest.enqueue_reingest(doc)` (`tools/rag/ingest.py:1229`) |

**Files**
- Modify: `tools/rag/views.py`, `tools/rag/retrieval.py` — relocate `_search_result_for`, `_truncated_snippet`, `_document_url_for` (own commit, pure move)
- Create: `tools/rag/tools.py`
- Modify: `tools/rag/apps.py` — register the three tools at the end of `RagConfig.ready()`
- Create: `tools/rag/tests/test_tools.py`
- Modify: `tools/rag/tests/_helpers.py` — add `snapshot_tools`/`restore_tools` (its own copy; helpers are never imported across apps)
- Test: `tools/rag/tests/test_tools.py`

**Interfaces**
- Consumes:
  - `retrieval.retrieve_nodes(question, category, settings_row, *, embed_resolved) -> tuple[list, bool, index]` — returns a **triple**; `SearchView` unpacks it at `views.py:1506`
  - `retrieval.apply_score_floor(nodes, score_floor, *, hybrid) -> list`
  - `retrieval.answer_question(question, session_id=None, *, category=None, answer_resolved, embed_resolved) -> {"answer": str, "citations": [...]}`
  - `ingest.enqueue_reingest(doc) -> int | None` — raises `FileNotFoundError` if the store copy is missing
  - `models.RagSettings.get_solo()` (`tools/rag/models.py:444`); `retrieval_top_k` (`models.py:405`), `retrieval_score_floor`, `hybrid_search`
  - `models.contracts.bindings.resolve(role_key) -> ResolvedModel` (`bindings.py:130`)
  - `retrieval._search_result_for(node_with_score, *, hybrid) -> dict` — relocated from `tools/rag/views.py:1334` by this task's first step
- Produces: `RAG_SEARCH`, `RAG_ASK`, `RAG_INGEST` (`ToolSpec` constants) and `run_search`/`run_ask`/`run_ingest` (`(args: dict, ctx: ToolContext) -> ToolResult`).

**Two decisions this task makes, both recorded because the spec left them open:**

1. **`category` is a `"text"` param, not `"choice"`.** `validate_params` requires a non-blank value for **any** `choice` param, required or not (`operations.py:317-319`: a blank `choice` produces `"{label} must be chosen."`), and "search every category" is the normal case (`retrieval.py`: `None`/empty searches all categories, ADR 0009). Declaring it `"choice"` would make every category-less search a validation error. The LLM-facing schema is **identical either way** — `openai_tool_dict` maps both `text` and `choice` to `"string"` and emits no `enum` for an engine-free choice with empty `choices` — so nothing is lost. The runner resolves the value against `Category` rows and refuses an unknown name with a `ParamError`-shaped message naming the categories that exist.
2. **`_search_result_for` moves to `retrieval.py`, beside the sibling its own docstring names — it is not imported from `views.py`.** That function (`tools/rag/views.py:1334`) describes itself as "the search-page sibling of `modules.rag.retrieval._vector_citations`'s per-node dict, reusing that module's OWN `locator_for`/`locator_text_for` helpers rather than a third copy of the page/timestamp connector rule". It already lives one module away from where it belongs. A fourth copy inside `tools.py` would be exactly the anti-pattern it was extracted to end — but so would a tool module importing a private name out of a *view* module, which inverts the usual direction (`views.py` imports `retrieval`, never the reverse) and would make the view layer load-bearing for a background job. Moving it, with its two private helpers, puts it beside its declared sibling and lets `views.SearchView` and `tools.run_search` call it as peers. Done as a pure relocation in its own commit, first step below; a test then pins that the tool and the search page produce byte-identical dicts for the same node.

**Steps**

- [ ] **Move `_search_result_for` and its two private helpers from `views.py` to `retrieval.py`, in their own commit, before writing any tool code.** A pure relocation: `git`-visible as a move of `_truncated_snippet` (`views.py:1303`), `_document_url_for` (`views.py:1314`), and `_search_result_for` (`views.py:1334`) into `tools/rag/retrieval.py`, placed immediately after `_vector_citations` (`retrieval.py:283-341`) — the sibling `_search_result_for`'s own docstring names. Three edits follow from it:

  - `retrieval.py` gains `from django.urls import NoReverseMatch, reverse` (it already imports Django ORM models at `:115`, so this crosses no new line).
  - `views.py` drops the three definitions and adds `_search_result_for` to its existing `from tools.rag.retrieval import locator_for, locator_text_for` line (`views.py:59`). `SearchView`'s call site at `views.py:1533` becomes `retrieval._search_result_for(...)` or stays bare via the import — either, consistently.
  - `_search_result_for`'s docstring loses the phrase "reusing that module's OWN helpers" and gains "declared beside `_vector_citations`, the sibling it mirrors" — it no longer reaches across a module for them.

  Nothing else changes. Then:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q tools/rag/tests/test_views.py tools/rag/tests/test_retrieval.py
  # EXPECTED: green. Any `mock.patch("tools.rag.views._search_result_for")`
  #   target in the suite must be re-pointed at `tools.rag.retrieval.` in this
  #   same commit -- `mock.patch` raises on an unimportable target, so a missed
  #   one fails loudly here.
  grep -rn "_search_result_for\|_truncated_snippet\|_document_url_for" --include="*.py" tools/
  # EXPECTED: definitions in retrieval.py only; call sites in views.py and
  #   (after the next step) tools.py.
  ```

  ```bash
  git add tools/rag/views.py tools/rag/retrieval.py
  git commit -m "refactor(rag): move _search_result_for beside _vector_citations

  Pure relocation of _search_result_for and its two private helpers from
  views.py to retrieval.py. That function's own docstring already calls
  itself \"the search-page sibling of retrieval._vector_citations's per-node
  dict\" and reaches into retrieval for locator_for/locator_text_for; it
  belongs beside the sibling it mirrors.

  The immediate reason is rag.search: a tool runner needs the same dicts the
  search page renders, and a tool module importing a private name out of a
  VIEW module inverts the usual direction and makes the view layer
  load-bearing for a background job. Now both callers are peers.

  No behaviour change.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>"
  ```

- [ ] Add the tool-test helpers to `tools/rag/tests/_helpers.py` — **its own copy**. Helpers are duplicated per app, never imported across apps: `make_job_ctx` already exists in both this file (`:76`) and `tools/vision/tests/_helpers.py:56` on purpose, and `make_tool_ctx` follows it. A cross-app import would make one package's test scaffolding load-bearing for another's.

  ```python
  def make_tool_ctx(**overrides):
      """An `agents.contracts.tools.ToolContext` for calling a tool runner
      directly -- no agent, no turn, no queue behind it.

      Duplicated per app, exactly as `make_job_ctx` above is (see
      `models/registry/tests/_helpers.py`'s identical note). Reuses this
      module's own `make_job_ctx` for the `job` field.
      """
      import time

      from agents.contracts.tools import StepBudget, ToolContext

      fields = dict(
          conversation_id="00000000-0000-0000-0000-000000000000",
          agent_key="test-agent",
          depth=0,
          budget=StepBudget(steps=8, deadline_monotonic=time.monotonic() + 900.0),
          job=make_job_ctx(),
      )
      fields.update(overrides)
      return ToolContext(**fields)


  def snapshot_tools() -> dict:
      """Body of the `_snapshot_tools` autouse fixture used by every test in
      this package that registers or re-registers a tool. Copy `_TOOLS`,
      hand it back, restore it afterwards -- the same shape as
      `clear_bindings` above. A module-global registry surviving between
      tests is exactly the state that makes a suite pass in one collection
      order and fail in the other, which is why the repo runs both."""
      from agents.contracts import tools as tools_module

      return dict(tools_module._TOOLS)


  def restore_tools(saved: dict) -> None:
      from agents.contracts import tools as tools_module

      tools_module._TOOLS.clear()
      tools_module._TOOLS.update(saved)
  ```

- [ ] Write the failing tests. `tools/rag/tests/test_tools.py`:

  ```python
  """The RAG tools (spec section 5). Each runner calls the SAME service
  function the page calls -- these tests patch that function and assert the
  call, which is the gate the spec names for P1."""
  from __future__ import annotations

  from unittest.mock import MagicMock, patch

  import pytest

  from agents.contracts.tools import ToolResult, all_tools, get_tool, grantable_tools
  from models.contracts.jobkinds import resolve_dotted_path
  from models.contracts.operations import ParamError
  from tools.rag.tests._helpers import make_tool_ctx, restore_tools, snapshot_tools


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  class TestRegistration:
      def test_all_three_are_registered_by_ready(self):
          keys = {spec.key for spec in all_tools()}
          assert {"rag.search", "rag.ask", "rag.ingest"} <= keys

      def test_every_runner_resolves(self):
          """The dotted-path strings fail LAZILY -- a wrong one boots
          cleanly and throws on a worker."""
          for key in ("rag.search", "rag.ask", "rag.ingest"):
              assert callable(resolve_dotted_path(get_tool(key).runner))

      def test_declared_roles_match_what_each_runner_consumes(self):
          assert get_tool("rag.search").roles == ("rag.embed",)
          assert get_tool("rag.ask").roles == ("rag.answer", "rag.embed")
          assert get_tool("rag.ingest").roles == ("rag.embed",)

      def test_only_ingest_mutates_and_is_therefore_not_grantable(self):
          """`rag.ingest` REPLACES existing chunks, which is a change to
          state that already exists (ADR 0010:266-276). Registered,
          documented, and tested -- but not grantable until Identity & Auth."""
          assert get_tool("rag.ingest").mutates is True
          assert get_tool("rag.search").mutates is False
          assert get_tool("rag.ask").mutates is False
          grantable = {spec.key for spec in grantable_tools()}
          assert "rag.ingest" not in grantable
          assert {"rag.search", "rag.ask"} <= grantable

      def test_no_spec_declares_a_describer(self):
          """Ruling R3 -- no describer is implemented in P1, by anyone."""
          for key in ("rag.search", "rag.ask", "rag.ingest"):
              assert get_tool(key).describer == ""

      def test_category_is_a_text_param_not_a_choice(self):
          """`validate_params` requires a non-blank value for ANY choice
          param (operations.py:317-319), and "search every category" is the
          normal case. The LLM-facing schema is identical either way."""
          for key in ("rag.search", "rag.ask"):
              param = next(p for p in get_tool(key).params if p.key == "category")
              assert param.kind == "text"
              assert not param.required


  @pytest.mark.django_db
  class TestRagSearchRunner:
      def test_it_calls_retrieve_nodes_then_apply_score_floor(self):
          from tools.rag.tools import run_search

          node = MagicMock()
          with patch("tools.rag.retrieval.retrieve_nodes") as retrieve, \
               patch("tools.rag.retrieval.apply_score_floor") as floor, \
               patch("models.contracts.bindings.resolve") as resolve:
              retrieve.return_value = ([node], False, MagicMock())
              floor.return_value = []
              run_search({"query": "anything"}, make_tool_ctx())

          assert retrieve.call_count == 1
          assert floor.call_count == 1
          assert resolve.call_args[0][0] == "rag.embed"

      def test_the_score_floor_and_hybrid_flag_come_from_settings_never_from_args(self):
          """Operator policy (ADR 0014 §14). A tool param that let a model
          lower the floor would be a settings mutation wearing a search
          tool's clothes."""
          from tools.rag.models import RagSettings
          from tools.rag.tools import RAG_SEARCH, run_search

          assert {p.key for p in RAG_SEARCH.params} == {"query", "category", "top_k"}

          row = RagSettings.get_solo()
          row.retrieval_score_floor = 0.42
          row.save(update_fields=["retrieval_score_floor"])

          with patch("tools.rag.retrieval.retrieve_nodes") as retrieve, \
               patch("tools.rag.retrieval.apply_score_floor") as floor, \
               patch("models.contracts.bindings.resolve"):
              retrieve.return_value = ([], True, MagicMock())
              floor.return_value = []
              run_search({"query": "q"}, make_tool_ctx())

          assert floor.call_args[0][1] == 0.42
          assert floor.call_args[1]["hybrid"] is True

      def test_top_k_defaults_to_the_operators_configured_depth(self):
          from tools.rag.models import RagSettings
          from tools.rag.tools import run_search

          row = RagSettings.get_solo()
          row.retrieval_top_k = 9
          row.save(update_fields=["retrieval_top_k"])

          with patch("tools.rag.retrieval.retrieve_nodes") as retrieve, \
               patch("tools.rag.retrieval.apply_score_floor", return_value=[]), \
               patch("models.contracts.bindings.resolve"):
              retrieve.return_value = ([], False, MagicMock())
              run_search({"query": "q"}, make_tool_ctx())
              settings_row = retrieve.call_args[0][2]
              assert settings_row.retrieval_top_k == 9

              run_search({"query": "q", "top_k": 3}, make_tool_ctx())
              assert retrieve.call_args[0][2].retrieval_top_k == 3

      def test_results_are_the_search_pages_own_dict_shape(self):
          """Not a fourth copy of the page/timestamp connector rule -- the
          tool and the search page must never drift."""
          from tools.rag import retrieval
          from tools.rag.tools import run_search

          node = MagicMock()
          node.node.metadata = {"file_id": "7", "file_name": "a.pdf", "page": 3}
          node.node.node_id = "n1"
          node.node.get_content.return_value = "some text"
          node.score = 0.9

          with patch("tools.rag.retrieval.retrieve_nodes", return_value=([node], False, MagicMock())), \
               patch("tools.rag.retrieval.apply_score_floor", return_value=[node]), \
               patch("models.contracts.bindings.resolve"):
              result = run_search({"query": "q"}, make_tool_ctx())

          assert result.data["results"] == [retrieval._search_result_for(node, hybrid=False)]

      def test_artifacts_are_deduped_document_references(self):
          from tools.rag.tools import run_search

          def node_for(file_id):
              node = MagicMock()
              node.node.metadata = {"file_id": file_id, "file_name": "a.pdf"}
              node.node.node_id = f"n-{file_id}"
              node.node.get_content.return_value = "text"
              node.score = 0.5
              return node

          nodes = [node_for("7"), node_for("7"), node_for("9")]
          with patch("tools.rag.retrieval.retrieve_nodes", return_value=(nodes, False, MagicMock())), \
               patch("tools.rag.retrieval.apply_score_floor", return_value=nodes), \
               patch("models.contracts.bindings.resolve"):
              result = run_search({"query": "q"}, make_tool_ctx())

          assert result.artifacts == ("document:7", "document:9")

      def test_a_blank_query_raises_paramerror_with_a_per_arg_reason(self):
          from tools.rag.tools import run_search

          with pytest.raises(ParamError) as excinfo:
              run_search({"query": ""}, make_tool_ctx())
          assert "query" in excinfo.value.errors

      def test_an_unknown_category_is_refused_naming_what_exists(self):
          from tools.rag.models import Category
          from tools.rag.tools import run_search

          Category.objects.create(name="Medical")
          with pytest.raises(ParamError) as excinfo:
              run_search({"query": "q", "category": "nonesuch"}, make_tool_ctx())
          assert "category" in excinfo.value.errors
          assert "Medical" in excinfo.value.errors["category"]

      def test_a_blank_category_searches_every_category(self):
          from tools.rag.tools import run_search

          with patch("tools.rag.retrieval.retrieve_nodes") as retrieve, \
               patch("tools.rag.retrieval.apply_score_floor", return_value=[]), \
               patch("models.contracts.bindings.resolve"):
              retrieve.return_value = ([], False, MagicMock())
              run_search({"query": "q"}, make_tool_ctx())
          assert retrieve.call_args[0][1] is None


  @pytest.mark.django_db
  class TestRagAskRunner:
      def test_it_calls_answer_question_with_both_roles_resolved_fresh(self):
          """`run_ask` re-resolves at call time and documents why -- a
          queued job can sit for a while (ADR 0013:205-213)."""
          from tools.rag.tools import run_ask

          with patch("tools.rag.retrieval.answer_question") as answer, \
               patch("models.contracts.bindings.resolve") as resolve:
              answer.return_value = {"answer": "42", "citations": []}
              run_ask({"question": "why"}, make_tool_ctx())

          assert [c[0][0] for c in resolve.call_args_list] == ["rag.answer", "rag.embed"]
          assert answer.call_args[0][0] == "why"
          assert answer.call_args[1]["category"] is None

      def test_the_answer_is_the_text_and_citations_land_in_data(self):
          from tools.rag.tools import run_ask

          citations = [
              {"document_id": "7", "title": "a.pdf", "locator_text": ", p. 3", "score": 0.9},
              {"document_id": "9", "title": "b.md", "locator_text": "", "score": 0.7},
          ]
          with patch("tools.rag.retrieval.answer_question") as answer, \
               patch("models.contracts.bindings.resolve"):
              answer.return_value = {"answer": "Because.", "citations": citations}
              result = run_ask({"question": "why"}, make_tool_ctx())

          assert isinstance(result, ToolResult)
          assert result.text == "Because."
          assert result.data["citations"] == citations
          assert result.artifacts == ("document:7", "document:9")

      def test_it_passes_no_session_id(self):
          """The `session_id` parameter is retired in P2 (spec section 2.3).
          P1 must not start depending on it."""
          from tools.rag.tools import run_ask

          with patch("tools.rag.retrieval.answer_question") as answer, \
               patch("models.contracts.bindings.resolve"):
              answer.return_value = {"answer": "x", "citations": []}
              run_ask({"question": "why"}, make_tool_ctx())

          assert len(answer.call_args[0]) == 1          # question only, positionally
          assert "session_id" not in answer.call_args[1]


  @pytest.mark.django_db
  class TestRagIngestRunner:
      def test_it_takes_a_document_id_and_nothing_else(self):
          """Store-path only, in the strongest available sense: it accepts
          NO path at all, so the model has zero filesystem reach."""
          from tools.rag.tools import RAG_INGEST

          assert [p.key for p in RAG_INGEST.params] == ["document_id"]
          assert RAG_INGEST.params[0].kind == "int"
          assert RAG_INGEST.params[0].required is True

      def test_it_calls_enqueue_reingest_and_returns_the_queue_job_id(self):
          from tools.rag.models import Document
          from tools.rag.tools import run_ingest

          doc = Document.objects.create(title="a.pdf", source_path="/x/a.pdf")
          with patch("tools.rag.ingest.enqueue_reingest", return_value=99) as enqueue:
              result = run_ingest({"document_id": doc.pk}, make_tool_ctx())

          assert enqueue.call_args[0][0].pk == doc.pk
          assert result.data["queue_job_id"] == 99
          assert result.data["document_id"] == doc.pk

      def test_it_enqueues_and_never_waits(self):
          """A tool runner must never block on a queue job. In sequential
          mode the child sits queued until the turn finishes and then runs;
          no deadlock, because nothing blocks."""
          import inspect

          from tools.rag import tools as tools_module

          source = inspect.getsource(tools_module.run_ingest)
          assert "get_job" not in source
          assert "wait_for" not in source

      def test_a_missing_document_is_an_honest_tool_error(self):
          from tools.rag.tools import run_ingest

          with pytest.raises(ValueError) as excinfo:
              run_ingest({"document_id": 999999}, make_tool_ctx())
          assert "999999" in str(excinfo.value)

      def test_a_missing_store_copy_surfaces_the_real_cause(self):
          """`enqueue_reingest` raises FileNotFoundError for a data-integrity
          problem distinct from "the queue is down". Surfaced, not
          swallowed."""
          from tools.rag.models import Document
          from tools.rag.tools import run_ingest

          doc = Document.objects.create(title="a.pdf", source_path="/nope/a.pdf")
          with pytest.raises(ValueError) as excinfo:
              run_ingest({"document_id": doc.pk}, make_tool_ctx())
          assert "store" in str(excinfo.value).lower()

      def test_a_queue_that_is_down_returns_a_honest_result_not_a_crash(self):
          """`enqueue_reingest` returns None when the queue refuses. The
          tool says so; it does not pretend work was queued."""
          from tools.rag.models import Document
          from tools.rag.tools import run_ingest

          doc = Document.objects.create(title="a.pdf", source_path="/x/a.pdf")
          with patch("tools.rag.ingest.enqueue_reingest", return_value=None):
              result = run_ingest({"document_id": doc.pk}, make_tool_ctx())
          assert result.data["queue_job_id"] is None
          assert "not queued" in result.text.lower()
  ```

- [ ] Run and expect **red**, then implement `tools/rag/tools.py`:

  ```python
  """The RAG tools an agent may call (spec section 5).

  Each runner calls the SAME service function the page calls. Not one grows
  a parallel seam:

  - `rag.search` -> `retrieval.retrieve_nodes` + `retrieval.apply_score_floor`
    -- exactly what `views.SearchView` does (views.py:1506,1531).
  - `rag.ask`    -> `retrieval.answer_question` -- exactly what
    `jobs.run_ask` does (jobs.py:244), and the seam ADR 0010:277-280 names.
  - `rag.ingest` -> `ingest.enqueue_reingest` -- the retry button's own
    entry point (ingest.py:1229).

  MODULE-SCOPE IMPORTS STAY PURE, AND THAT IS LOAD BEARING. `RagConfig.
  ready()` imports this module to register the specs below, and `ready()`
  must not pull in the service layer (no DB, no heavy imports at startup --
  see that method's own docstring). So every runner does its imports
  LAZILY, inside the function body, the same way `tools/rag/categories.py:40`
  already does. `agents/contracts/tests/test_purity.py`'s sibling guard in
  Task 10 pins it.
  """
  from __future__ import annotations

  from agents.contracts.tools import ToolContext, ToolResult, ToolSpec, validate_tool_args
  from models.contracts.operations import Param, ParamError
  from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE

  # `category` is a "text" param, not a "choice", deliberately:
  # `validate_params` requires a non-blank value for ANY choice param,
  # required or not (operations.py:317-319), and "search every category" is
  # the normal case (ADR 0009: None/empty searches all categories). The
  # LLM-facing schema is IDENTICAL either way -- `openai_tool_dict` maps
  # both kinds to "string" and emits no `enum` for an engine-free choice --
  # so nothing is lost, and the runner validates the name against the DB.
  _CATEGORY = Param(
      "category", "text", "Category",
      description=(
          "Restrict to one library category by name. Leave it out to search "
          "every category."
      ),
  )


  RAG_SEARCH = ToolSpec(
      key="rag.search",
      label="Search the library",
      description=(
          "Search the operator's own ingested documents and return the "
          "best-matching passages with their source titles and locators. Use "
          "this when you need evidence from the library but do not need it "
          "summarised into an answer. It calls no language model."
      ),
      params=(
          Param("query", "text", "Query", required=True,
                description="What to look for in the library."),
          _CATEGORY,
          Param("top_k", "int", "Results", default=None, min=1, max=50,
                description=(
                    "How many passages to return. Leave it out to use the "
                    "operator's configured retrieval depth."
                )),
      ),
      roles=(RAG_EMBED_ROLE,),
      runner="tools.rag.tools.run_search",
  )

  RAG_ASK = ToolSpec(
      key="rag.ask",
      label="Ask the library",
      description=(
          "Ask a question of the operator's own ingested documents and get a "
          "synthesised answer with citations. Use this when a written answer "
          "is wanted rather than raw passages."
      ),
      params=(
          Param("question", "text", "Question", required=True,
                description="The question to answer from the library."),
          _CATEGORY,
      ),
      roles=(RAG_ANSWER_ROLE, RAG_EMBED_ROLE),
      runner="tools.rag.tools.run_ask",
  )

  RAG_INGEST = ToolSpec(
      key="rag.ingest",
      label="Re-ingest a document",
      description=(
          "Queue an already-stored document to be read and indexed again. "
          "Takes the document's id and nothing else -- it accepts no file "
          "path, so it can only ever act on what the operator already put in "
          "the library."
      ),
      params=(
          Param("document_id", "int", "Document", required=True, min=1,
                description="The id of a document already in the library."),
      ),
      roles=(RAG_EMBED_ROLE,),
      runner="tools.rag.tools.run_ingest",
      # A re-ingest REPLACES existing chunks -- a change to state that
      # already exists, which is exactly what `mutates` means. Registered,
      # documented, and tested, but never grantable until Identity & Auth
      # (ADR 0010:266-276).
      mutates=True,
  )


  def _resolved_category(raw: str | None):
      """The `Category` name `raw` denotes, or `None` for blank.

      Refuses an unknown name with a `ParamError` naming what exists, so a
      model that guessed a shelf gets the repairable failure section 10.2
      grants one retry for -- never a silent all-categories search that
      answers a question nobody asked.
      """
      from tools.rag.models import Category

      name = (raw or "").strip()
      if not name:
          return None
      match = Category.objects.filter(name__iexact=name).first()
      if match is None:
          existing = sorted(Category.objects.values_list("name", flat=True))
          raise ParamError({
              "category": (
                  f"No category named {name!r}. "
                  + (f"The library has: {', '.join(existing)}." if existing
                     else "The library has no categories yet.")
              )
          })
      return match.name


  def _document_artifacts(dicts) -> tuple[str, ...]:
      """`("document:<id>", ...)` for every entry carrying a document id,
      deduped, first-seen order preserved."""
      seen = {}
      for entry in dicts:
          doc_id = entry.get("document_id")
          if doc_id:
              seen.setdefault(f"document:{doc_id}", None)
      return tuple(seen)


  def run_search(args: dict, ctx: ToolContext) -> ToolResult:
      """Retrieval only -- the no-LLM path `views.SearchView` uses.

      Follows that view exactly: take `RagSettings.get_solo()`, call
      `retrieve_nodes` (which returns the TRIPLE `(nodes, hybrid, index)`,
      retrieval.py:344-350), then `apply_score_floor`.

      The score floor and the hybrid flag are read from `RagSettings`,
      NEVER from tool arguments: they are operator policy (ADR 0014 §14),
      and a tool param that let a model lower the floor would be a settings
      mutation wearing a search tool's clothes.
      """
      from models.contracts.bindings import resolve
      from tools.rag import retrieval
      from tools.rag.models import RagSettings

      clean = validate_tool_args(RAG_SEARCH, args)
      category = _resolved_category(clean.get("category"))

      settings_row = RagSettings.get_solo()
      if clean.get("top_k"):
          # In memory only -- never saved. The operator's configured depth
          # is policy; a per-call override is not a settings change.
          settings_row.retrieval_top_k = clean["top_k"]

      embed_resolved = resolve(RAG_EMBED_ROLE)
      nodes, hybrid, _index = retrieval.retrieve_nodes(
          clean["query"], category, settings_row, embed_resolved=embed_resolved
      )
      surviving = retrieval.apply_score_floor(
          nodes, settings_row.retrieval_score_floor, hybrid=hybrid
      )

      # `retrieval._search_result_for` -- the SAME function
      # `views.SearchView` calls, moved beside `_vector_citations` in the
      # commit above so both callers are peers rather than one reaching
      # into the other's view module. Not a fourth copy of the
      # page/timestamp connector rule: a tool that drifted from the search
      # page would be two different answers to one question.
      results = [retrieval._search_result_for(node, hybrid=hybrid) for node in surviving]

      lines = [
          f"{i}. {r['title']}{r['locator_text']} — {r['snippet']}"
          for i, r in enumerate(results, start=1)
      ]
      text = "\n".join(lines) if lines else "Nothing in the library matched that."
      return ToolResult(
          text=text,
          data={"results": results, "hybrid": hybrid},
          artifacts=_document_artifacts(results),
      )


  def run_ask(args: dict, ctx: ToolContext) -> ToolResult:
      """The synthesised-answer path -- the exact seam ADR 0010:277-280
      names.

      Both roles are re-resolved fresh at call time, for the reason
      `jobs.run_ask` already documents: a queued job can sit for a while,
      and the binding may have changed under it (ADR 0013:205-213).

      `session_id` is deliberately not passed. That parameter is retired in
      P2 along with `ChatSession`/`ChatMessage` (spec section 2.3), and P1
      must not start depending on it.
      """
      from models.contracts.bindings import resolve
      from tools.rag import retrieval

      clean = validate_tool_args(RAG_ASK, args)
      category = _resolved_category(clean.get("category"))

      answer_resolved = resolve(RAG_ANSWER_ROLE)
      embed_resolved = resolve(RAG_EMBED_ROLE)
      result = retrieval.answer_question(
          clean["question"],
          category=category,
          answer_resolved=answer_resolved,
          embed_resolved=embed_resolved,
      )
      citations = result["citations"]
      return ToolResult(
          text=result["answer"],
          data={"citations": citations},
          artifacts=_document_artifacts(citations),
      )


  def run_ingest(args: dict, ctx: ToolContext) -> ToolResult:
      """Queue an already-stored document for another ingest pass.

      ENQUEUES AND RETURNS. It never waits on the job it queued. On a
      default install `JobSettings.memory_budget_bytes` is null, which
      `models/queue/scheduler.py:374-380` reads as sequential mode -- at
      most one job on the whole machine -- so a turn that blocked on a job
      it enqueued would deadlock with certainty. In sequential mode the
      child simply sits queued until the turn finishes and then runs.
      """
      from tools.rag import ingest
      from tools.rag.models import Document

      clean = validate_tool_args(RAG_INGEST, args)
      doc_id = clean["document_id"]
      doc = Document.objects.filter(pk=doc_id).first()
      if doc is None:
          raise ValueError(f"There is no document with id {doc_id} in the library.")

      try:
          queue_job_id = ingest.enqueue_reingest(doc)
      except FileNotFoundError as exc:
          # A data-integrity problem distinct from "the queue is down": the
          # row exists but has nothing left to ingest FROM. Surfaced with
          # its real cause, never flattened into a generic failure.
          raise ValueError(
              f"{doc.title!r} cannot be re-ingested: its copy in the managed "
              f"store is missing ({exc})."
          ) from exc

      if queue_job_id is None:
          return ToolResult(
              text=f"{doc.title!r} was not queued — the execution queue is unavailable.",
              data={"document_id": doc.pk, "queue_job_id": None},
          )
      return ToolResult(
          text=f"{doc.title!r} is queued for re-ingest as job {queue_job_id}.",
          data={"document_id": doc.pk, "queue_job_id": queue_job_id},
      )
  ```

- [ ] Register them. At the end of `RagConfig.ready()` in `tools/rag/apps.py`, after the two `register_job_kind` calls:

  ```python
          # Tool registration (spec section 4.3). `tools/rag/tools.py`
          # imports nothing heavier than the pure contracts at module scope
          # -- every runner does its `retrieval`/`ingest`/`views` imports
          # lazily, inside the function body -- so importing it here keeps
          # this method's no-DB-no-heavy-imports promise (see the docstring
          # above). The runners themselves stay dotted-path STRINGS, never
          # live callables, exactly as the job kinds above do.
          from agents.contracts.tools import register_tool
          from tools.rag.tools import RAG_ASK, RAG_INGEST, RAG_SEARCH

          register_tool(RAG_SEARCH)
          register_tool(RAG_ASK)
          register_tool(RAG_INGEST)
  ```

  Registered **unconditionally**, not behind the `"media"` flag — the same reasoning `rag.ingest`'s job kind already carries at `apps.py:72-76`: plain prose/tabular document work exists for every deployment.

- [ ] Run, expect green, and run the matrix:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q tools/rag/tests/test_tools.py
  # EXPECTED: green. Record the count -- parametrized cases expand, so this
  #   number is read from the run, never predicted.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  # EXPECTED: whole suite green; collected count up by exactly what
  #   tools/rag/tests/test_tools.py contributed in the per-file run above.
  ```

- [ ] Update `tools/rag/README.md` with a "Tools" section naming the three keys, their roles, which is `mutates=True` and why it is not grantable, and the one-line rule that each runner calls the same service function the page calls. Then commit:

  ```bash
  git add tools/rag/tools.py tools/rag/apps.py tools/rag/README.md \
          tools/rag/tests/test_tools.py tools/rag/tests/_helpers.py
  git commit -m "$(cat <<'EOF'
  feat(rag): register rag.search, rag.ask, and rag.ingest as tools

  Three ToolSpecs on the existing seams -- retrieve_nodes +
  apply_score_floor (the no-LLM path SearchView uses), answer_question (the
  seam ADR 0010:277-280 names), and enqueue_reingest (the retry button's
  own entry point). Not one grows a parallel seam, and each is proven by
  patching that function and asserting the call.

  rag.search reads the score floor and the hybrid flag from RagSettings,
  never from tool arguments: they are operator policy (ADR 0014 §14), and
  a param that let a model lower the floor would be a settings mutation
  wearing a search tool's clothes. Its result dicts come from
  retrieval._search_result_for -- the same function the search page calls,
  relocated there from views.py in the commit before this one so both
  callers are peers. Not a fourth copy of the page/timestamp connector rule
  that function exists to end, and not a tool reaching into a view module.

  rag.ingest takes a document id and NOTHING else: no path at all, so the
  model has zero filesystem reach. mutates=True (it replaces existing
  chunks), so it is registered, documented, and tested but excluded from
  grantable_tools until Identity & Auth (ADR 0010:266-276). It ENQUEUES AND
  RETURNS -- never waits on the job it queued, which in sequential mode
  would deadlock with certainty.

  `category` is a "text" param, not "choice": validate_params requires a
  non-blank value for ANY choice param, and "search every category" is the
  normal case. The LLM-facing schema is identical either way; the runner
  validates the name against the DB and refuses an unknown one naming what
  exists.

  tools.py's module-scope imports stay pure so RagConfig.ready() keeps its
  no-DB-no-heavy-imports promise; every runner imports lazily inside its
  body, as tools/rag/categories.py:40 already does.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 8: `models/registry/tools.py` — `models.status`, DB-only

Kept, per orchestrator **ruling R2**, on two grounds: it is the `models/` column's own proof that it adopts the tool contract (without it, `models/` is the one column that registers no tool), and it traces directly to the owner's "it should have access to the tools within the system".

**But it is DB-only. It makes no `is_healthy` HTTP probe, and no engine call of any kind.** Reachability is a live network fact whose cost is unbounded and whose latency lands inside a turn that is already holding the machine's one execution slot. `/setup/` is the surface that answers "is the engine up", and it answers it on demand, for a human, outside the queue. If a role is bound but its engine is down, the agent finds out the honest way — the tool that needs it fails and surfaces the error.

**Files**
- Create: `models/registry/tools.py`
- Modify: `models/registry/apps.py` — register the tool at the end of `InferenceConfig.ready()`
- Create: `models/registry/tests/test_tools.py`
- Modify: `models/registry/tests/_helpers.py` — add its own `snapshot_tools`/`restore_tools`
- Test: `models/registry/tests/test_tools.py`

**Interfaces**
- Consumes: `models.contracts.roles.all_roles() -> list[RoleSpec]` (`roles.py:90`); `models.registry.bindings.role_primary(role_key) -> tuple[str, int | None]` (`bindings.py:192-221`). **In-column import — crosses no boundary**, which is the second reason ruling R2 keeps this tool.
- Produces: `MODELS_STATUS` (`ToolSpec`, `roles=()`, `mutates=False`, `params=()`) and `run_status(args, ctx) -> ToolResult`.

`role_primary` has exactly three honest states, and the tool reports all three without collapsing them: a bound connection → `(connection.name, connection.pk)`; no connection but an explicit environment override → `(f"{model_id} (environment override)", None)`; neither → `("", None)`, the role is genuinely unassigned. The environment-override string carries a model id the **operator's own environment** supplied — reporting the operator's binding back to them is what `/inference/` already does, and it is not the platform naming a model.

**Steps**

- [ ] Add `make_tool_ctx`, `snapshot_tools`, and `restore_tools` to `models/registry/tests/_helpers.py` — **its own copy**, byte-for-byte the three functions Task 7 added to `tools/rag/tests/_helpers.py`, with the same docstrings. Helpers are duplicated per app, never imported across apps; this file already carries its own `make_job_ctx` for exactly that reason, and `make_tool_ctx` reuses it.

- [ ] Write the failing tests. `models/registry/tests/test_tools.py`:

  ```python
  """`models.status` -- the models/ column's own tool (spec section 5,
  ruling R2)."""
  from __future__ import annotations

  import json
  from unittest.mock import patch

  import pytest

  from agents.contracts.tools import all_tools, get_tool, grantable_tools
  from models.contracts.jobkinds import resolve_dotted_path
  from models.contracts.operations import ParamError
  from models.registry.tests._helpers import (
      make_chat_connection, make_tool_ctx, restore_tools, snapshot_tools,
  )


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  class TestRegistration:
      def test_it_is_registered_so_the_models_column_adopts_the_contract(self):
          """Without it, `models/` is the one column that registers no tool
          -- which is half of ruling R2's reason for keeping it."""
          assert "models.status" in {spec.key for spec in all_tools()}

      def test_its_runner_resolves(self):
          assert callable(resolve_dotted_path(get_tool("models.status").runner))

      def test_it_declares_no_roles_and_no_params(self):
          spec = get_tool("models.status")
          assert spec.roles == ()
          assert spec.params == ()

      def test_it_does_not_mutate_and_is_grantable(self):
          assert get_tool("models.status").mutates is False
          assert "models.status" in {spec.key for spec in grantable_tools()}

      def test_it_declares_no_describer(self):
          assert get_tool("models.status").describer == ""


  class TestItIsDatabaseOnly:
      def test_the_source_makes_no_engine_call_of_any_kind(self):
          """Ruling R2. Reachability is a live network fact whose cost is
          unbounded and whose latency lands inside a turn already holding
          the machine's one execution slot. `/setup/` answers "is the
          engine up", on demand, for a human, outside the queue."""
          import inspect

          from models.registry import tools as tools_module

          source = inspect.getsource(tools_module)
          for forbidden in ("is_healthy", "httpx", "get_engine", "preflight",
                            "list_installed", "loaded_footprint"):
              assert forbidden not in source, forbidden

      @pytest.mark.django_db
      def test_it_makes_no_http_request_when_it_runs(self):
          """The source check above is necessary but not sufficient -- a
          transitive call could still reach the network. This proves it
          does not."""
          from models.registry.tools import run_status

          with patch("httpx.get", side_effect=AssertionError("no HTTP allowed")), \
               patch("httpx.post", side_effect=AssertionError("no HTTP allowed")):
              run_status({}, make_tool_ctx())


  @pytest.mark.django_db
  class TestRunStatus:
      def test_it_reports_a_bound_connection_with_its_name_and_pk(self):
          from models.registry.models import RoleBinding
          from models.registry.tools import run_status

          connection = make_chat_connection(name="the box's chat model")
          RoleBinding.objects.create(role_key="rag.answer", connection=connection)

          result = run_status({}, make_tool_ctx())
          row = next(r for r in result.data["roles"] if r["role"] == "rag.answer")
          assert row["model"] == "the box's chat model"
          assert row["connection_id"] == connection.pk
          assert row["assigned"] is True
          assert row["capability"] == "chat"

      def test_an_unassigned_role_is_reported_as_unassigned_not_omitted(self):
          """Three honest states, none collapsed: bound, environment
          override, genuinely unassigned (bindings.py:192-221)."""
          from models.registry.tools import run_status

          with patch("models.registry.bindings.role_primary", return_value=("", None)):
              result = run_status({}, make_tool_ctx())

          assert result.data["roles"]
          for row in result.data["roles"]:
              assert row["assigned"] is False
              assert row["connection_id"] is None
              assert "not assigned" in result.text

      def test_an_environment_override_is_reported_with_no_connection_id(self):
          from models.registry.tools import run_status

          with patch("models.registry.bindings.role_primary",
                     return_value=("something (environment override)", None)):
              result = run_status({}, make_tool_ctx())

          row = result.data["roles"][0]
          assert row["assigned"] is True
          assert row["connection_id"] is None
          assert "environment override" in row["model"]

      def test_it_covers_every_registered_role(self):
          from models.contracts.roles import all_roles
          from models.registry.tools import run_status

          result = run_status({}, make_tool_ctx())
          assert {r["role"] for r in result.data["roles"]} == {
              role.key for role in all_roles()
          }

      def test_the_text_is_one_readable_line_per_role(self):
          from models.contracts.roles import all_roles
          from models.registry.tools import run_status

          result = run_status({}, make_tool_ctx())
          assert len(result.text.splitlines()) == len(all_roles())

      def test_the_data_is_json_safe(self):
          from models.registry.tools import run_status

          data = run_status({}, make_tool_ctx()).data
          assert json.loads(json.dumps(data)) == data

      def test_it_produces_no_artifacts(self):
          from models.registry.tools import run_status

          assert run_status({}, make_tool_ctx()).artifacts == ()

      def test_an_unexpected_argument_is_rejected_not_ignored(self):
          """It declares no params, so ANY argument is a bug in the call --
          and `validate_tool_args` is the one floor that says so."""
          from models.registry.tools import run_status

          with pytest.raises(ParamError) as excinfo:
              run_status({"role": "rag.answer"}, make_tool_ctx())
          assert "role" in excinfo.value.errors
  ```

- [ ] Run and expect **red**, then implement `models/registry/tools.py`:

  ```python
  """The `models/` column's own tool: what is currently assigned to each
  model-consuming role (spec section 5, ruling R2).

  Kept on two grounds. It is this column's proof that it adopts the tool
  contract -- without it, `models/` is the one column that registers no
  tool at all -- and it traces directly to the owner's "it should have
  access to the tools within the system".

  DB-ONLY, and that is the ruling, not an optimisation. It runs no health
  probe and makes no engine call of any kind. Reachability is a live
  network fact whose cost is unbounded and whose latency would land inside
  a turn that is already holding the machine's one execution slot
  (sequential mode, models/queue/scheduler.py:374-380). `/setup/` is the
  surface that answers "is the engine up", and it answers it on demand, for
  a human, outside the queue. If a role is bound but its engine is down,
  the agent finds out the honest way: the tool that needs it fails and
  surfaces the error.

  `models.registry.bindings` is an IN-COLUMN import here and crosses no
  boundary -- which is the other half of why this tool lives in this column
  rather than being bolted onto `agents/`.

  NOTE ON WORDING: this module deliberately never spells the name of the
  engine-probe method it refuses to call. `test_the_source_makes_no_engine_
  call_of_any_kind` below scans this file's own TEXT for that name among
  others, so writing it here -- even inside a docstring explaining why we
  do not use it -- would turn the guard red on its own explanation.

  Module-scope imports stay pure so `InferenceConfig.ready()` keeps its
  no-DB-no-heavy-imports promise; `run_status` imports lazily inside its
  body.
  """
  from __future__ import annotations

  from agents.contracts.tools import ToolContext, ToolResult, ToolSpec, validate_tool_args

  MODELS_STATUS = ToolSpec(
      key="models.status",
      label="Model assignments",
      description=(
          "Report which model this box currently has assigned to each of its "
          "model-consuming roles, and which roles have nothing assigned. It "
          "reads the registry only: it does not contact any engine, so it "
          "cannot say whether an engine is actually running."
      ),
      params=(),
      roles=(),
      runner="models.registry.tools.run_status",
  )


  def run_status(args: dict, ctx: ToolContext) -> ToolResult:
      """Every registered role and whatever currently answers it.

      Reports all three of `role_primary`'s honest states without
      collapsing any of them (bindings.py:192-221): a bound connection
      (name plus pk), an explicit environment override (a name, no pk --
      there is no registry row to carry one), or genuinely unassigned.
      """
      from models.contracts.roles import all_roles
      from models.registry.bindings import role_primary

      validate_tool_args(MODELS_STATUS, args)   # declares no params: any arg is a bug

      rows = []
      lines = []
      for role in all_roles():
          name, connection_id = role_primary(role.key)
          rows.append({
              "role": role.key,
              "label": role.label,
              "capability": role.capability,
              "model": name,
              "connection_id": connection_id,
              "assigned": bool(name),
          })
          lines.append(
              f"{role.label} ({role.key}, {role.capability}): "
              + (name if name else "not assigned")
          )

      return ToolResult(text="\n".join(lines), data={"roles": rows})
  ```

- [ ] Register it at the end of `InferenceConfig.ready()` in `models/registry/apps.py`, after the `register_job_kind` call:

  ```python
          # Tool registration (spec section 4.3, ruling R2). Imports the
          # pure contracts plus this app's own `tools` module, which itself
          # imports nothing heavy at module scope -- so this method's
          # no-DB-no-heavy-imports promise (see the docstring above) holds.
          from agents.contracts.tools import register_tool
          from models.registry.tools import MODELS_STATUS

          register_tool(MODELS_STATUS)
  ```

- [ ] Run, expect green, run the matrix, update `models/registry/README.md` with a "Tools" section, and commit:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q models/registry/tests/test_tools.py
  # EXPECTED: green. Record the count.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  # EXPECTED: whole suite green; collected count up by exactly what
  #   models/registry/tests/test_tools.py contributed in the run above.

  git add models/registry/tools.py models/registry/apps.py models/registry/README.md \
          models/registry/tests/test_tools.py models/registry/tests/_helpers.py
  git commit -m "$(cat <<'EOF'
  feat(registry): register models.status as the models/ column's tool

  Per ruling R2: kept because it is this column's proof that it adopts the
  tool contract -- without it models/ registers no tool at all -- and
  because it traces to the owner's "access to the tools within the system".

  DB-ONLY, and that is the ruling. No is_healthy probe, no engine call of
  any kind: reachability is a live network fact with unbounded cost, and
  its latency would land inside a turn already holding the machine's one
  execution slot. Two tests enforce it -- a source scan for the forbidden
  names, and a run with httpx.get/post patched to raise. /setup/ is the
  surface that answers "is the engine up", on demand, for a human, outside
  the queue.

  Reports all three of role_primary's honest states without collapsing
  any: bound connection (name + pk), environment override (name, no pk),
  genuinely unassigned. Declares no params, so any argument is rejected by
  the one validation floor rather than ignored.

  `models.registry.bindings` is an in-column import here and crosses no
  boundary -- the other half of why this tool lives in this column.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 9: `tools/vision/tools.py` — `vision.operations`, `vision.generate` — **VISION-OWNED**

> **VISION-OWNED. This task is executed by a different session.** It depends on Tasks 1–5 having landed and is independent of Tasks 6–8; it may run in parallel with them once Task 5 is merged. **It must not touch `agents/contracts/`, `models/`, or `tools/rag/`.** Everything it consumes is listed below with an exact signature; everything it produces is specified below with an exact shape. If a needed interface is missing or differs from what is written here, STOP and report rather than widening a contract from inside this task.
>
> **No describer.** Ruling R3 struck the vision `describer` from VISION-OWNED entirely. The inert field declaration (Task 2) is the whole of P1's describer work. Do not implement `describe(resolved)`, do not compute `supported`/`unsupported_reason`, do not add an `options` key. That is R2/Phase-1.55 work and it is listed in the ROADMAP, not here.
>
> **THE VISION-FLAG RULE applies directly to this task.** Its registration tests override `FARABUNKER_FEATURES`; any test in this package that overrides the flag **and** performs an HTTP request or `reverse()` in the same test MUST keep `"vision"` in the overridden set. The flag-off registration test below deliberately calls neither, which is what makes an empty set legal there — the same escape the existing `tools/vision/tests/test_config.py` documents.

**Files**
- Create: `tools/vision/tools.py`
- Modify: `tools/vision/apps.py` — register both tools at the end of `VisionConfig.ready()`, **after** the existing `if FEATURE not in settings.FARABUNKER_FEATURES: return` early exit at `apps.py:34-35` and **after** the five `register_operation` calls at `:44-48`
- Create: `tools/vision/tests/test_tools.py`
- Modify: `tools/vision/tests/_helpers.py` — add its own `snapshot_tools`/`restore_tools` (helpers are duplicated per app, never imported across apps)
- Modify: `tools/vision/README.md` — a "Tools" section
- Test: `tools/vision/tests/test_tools.py`

**Interfaces — Consumes (exact, all existing, none to be changed):**

| Signature | Location |
|---|---|
| `services.operation_catalog(resolved: ResolvedModel \| None = None) -> list[dict]` | `tools/vision/services.py:304` |
| `services.submit_job(operation_key: str, raw_params: dict, files: dict \| None = None, resolved: ResolvedModel \| None = None) -> GenerationJob` | `services.py:560` |
| `services.wait_for(job, timeout: float, interval: float = 1.0, on_poll: Callable[[GenerationJob, float], None] \| None = None) -> GenerationJob` | `services.py:801` |
| `services.job_json(job: GenerationJob) -> dict` | `services.py:742` (shape fixed at `:759-798`; exposes URLs, never paths) |
| `services.resolve_inputs(operation, references: dict[str, str]) -> dict[str, store.StoredFile]` | `services.py:452` |
| `services.InputReferenceError(param_key, message)` | `services.py:436` |
| `services.VisionUnavailable(state, message)` | `services.py:99` |
| `services.preflight(resolved=None) -> PreflightResult` | `services.py:150` |
| `services.role_unbound_message() -> str` | `services.py:117` |
| `operations.all_operations() -> list[Operation]`, `operations.get_operation(key)` | `models/contracts/operations.py:135`, `:140` |
| `Operation.file_params() -> tuple[Param, ...]` (DECLARATION order) | `operations.py:103` |
| `Operation.param(key) -> Param \| None` | `operations.py:96` |
| `agents.contracts.artifacts.parse_artifact(reference) -> tuple[str, int]` | Task 5 |
| `agents.contracts.tools.{ToolSpec, ToolResult, ToolContext, register_tool, validate_tool_args}` | Tasks 2, 4 |
| `ctx.job.report_progress(done, total=None, *, unit, label="")` | `models/contracts/jobkinds.py:123` |

**Interfaces — Produces (exact):**

```python
VISION_OPERATIONS: ToolSpec        # key="vision.operations", roles=(), mutates=False, params=()
def build_generate_spec() -> ToolSpec   # key="vision.generate", roles=("vision.generate",), mutates=False
def run_operations(args: dict, ctx: ToolContext) -> ToolResult
def run_generate(args: dict, ctx: ToolContext) -> ToolResult
```

**Exception translation is mandatory, and it is a contract, not a detail.** `services.VisionUnavailable` is a **`RuntimeError`** (`tools/vision/services.py:99`), and `services.InputReferenceError` is a **`ValueError`** (`services.py:436`). §10.1 splits tool failures into two classes with different recovery policies: a `ToolRefused` gets **no** retry, everything else gets **one**. A bare `RuntimeError` escaping a runner falls into neither bucket cleanly and would reach P2's `invoke_tool` as an unclassified crash. So `run_generate` must catch and translate:

| Raised by the service layer | Translate to | Why |
|---|---|---|
| `services.VisionUnavailable` (a `RuntimeError`) for an unbound role or an unreachable engine | `agents.contracts.tools.ToolRefused` | Nothing the model can say will bind a role or start an engine; a retry is wasted budget. **The message must be preserved verbatim** — `services.role_unbound_message()` (`services.py:117`) is the platform's own operator-facing copy, and a tool must not invent a second sentence for the same state. |
| `services.InputReferenceError` (a `ValueError`) for a dead or malformed reference | `ValueError`, message preserved | Repairable: the model can name a different artifact. Gets the one retry. |
| `models.contracts.operations.ParamError` from `submit_job`'s own `validate_params` | let it propagate unchanged | Its `.errors` maps a param key to a reason — the single most repairable failure there is (§10.2). |

Never swallow, never re-word, never wrap a traceback into the message.

`vision.generate`'s params are **derived at registration time**, after the five `register_operation` calls, so `operation`'s `choices` are exactly the operation keys that are actually registered. That is why it is a builder function and not a module constant: a constant would be evaluated at import time, before `ready()` has registered anything, and would ship an empty `choices` tuple.

Exact param list for `vision.generate` — the honest intersection of what the registered operations declare:

```python
Param("operation", "choice", "Operation", required=True,
      choices=tuple(op.key for op in all_operations()),
      description="Which generation mode to run.")
Param("prompt", "text", "Prompt", description="What to generate.")
Param("negative_prompt", "text", "Negative prompt",
      description="What to avoid. Ignored by an operation that does not take one.")
Param("width", "int", "Width", description="Output width in pixels.")
Param("height", "int", "Height", description="Output height in pixels.")
Param("steps", "int", "Steps", description="How many sampling steps to run.")
Param("cfg", "float", "Guidance", description="How strongly to follow the prompt.")
Param("seed", "seed", "Seed",
      description="Leave it out for a random seed; give one to reproduce an earlier image.")
Param("image", "text", "Source image",
      description=(
          "An artifact reference to a stored image to work from, in the form "
          "output:<id> or input:<id>. Required by the image-to-image family, "
          "ignored by text-to-image."
      ))
```

`image` is a `"text"` param and **never** a `"file"` one: `TOOL_PARAM_KINDS` excludes `"file"` because a tool call is JSON and cannot carry an upload object (ADR 0012:140).

**Two behaviours this task must implement, both stated so the vision session does not have to infer them:**

1. **Argument narrowing, announced not silent.** The tool's schema is the union across operations; `submit_job` validates against the **picked** operation, and `validate_params` "rejects keys the operation does not declare" (`operations.py:276-279`). So `run_generate` must drop any argument the picked operation does not declare **before** calling `submit_job` — and must name every dropped argument in the `ToolResult.text`. Never silently. Use `operation.param(key) is not None` as the test.
2. **The `image` reference maps to the operation's FIRST file param.** `Operation.file_params()` returns them in declaration order, and the "use this image here" link already pre-fills the first one for exactly this reason (`operations.py:103-111`). So `run_generate` resolves `image` as `services.resolve_inputs(operation, {operation.file_params()[0].key: reference})` and passes the result as `files=` to `submit_job` — which merges it back into the params it validates (`services.py:600-614`). This mirrors `tools/vision/views.py:726-737` exactly; read those lines before writing it. An `image` argument given to an operation that declares no file param is a dropped argument under rule 1, not an error.

**Timeout.** `run_generate` calls `services.wait_for(job, timeout=GENERATE_WAIT_TIMEOUT_SECONDS, on_poll=...)`, reusing the constant the vision job handler already defines (`tools/vision/jobs.py:118`, `600.0`) rather than declaring a second one. The turn's own deadline is 900s, chosen so one generate fits.

**Progress.** `on_poll` forwards `ctx.job.report_progress(int(elapsed), total=None, unit="seconds", label="generating")` — byte-for-byte the pattern `tools/vision/jobs.py:308-336` already uses. `total=None` because the total genuinely is not known, which is the rule `JobContext` and ADR 0013:410-417 both state: this platform never fabricates a denominator.

**This runner waits on a `GenerationJob`, which is NOT a queue job.** `services.wait_for` polls `refresh_job`, which asks the *image engine* about a generation it already submitted — it never calls `models.contracts.queue.get_job`. That is precisely what `tools/vision/jobs.py:290,338-340` already does inside `run_generate`, and ADR 0012:748-752 already declares it the tool seam. The forbidden thing is waiting on a **queue** job, and this does not.

**Steps**

- [ ] Add `make_tool_ctx`, `snapshot_tools`, and `restore_tools` to `tools/vision/tests/_helpers.py` — **its own copy**, byte-for-byte the three functions Task 7 added to `tools/rag/tests/_helpers.py`, with the same docstrings. This file already carries its own `make_job_ctx` at `:56` for exactly this reason; `make_tool_ctx` reuses it.

- [ ] Write the failing tests. `tools/vision/tests/test_tools.py`:

  ```python
  """The vision tools (spec section 5). VISION-OWNED.

  Every runner calls the SAME service function the page calls -- these
  tests patch that function and assert the call, which is the gate the spec
  names for P1.
  """
  from __future__ import annotations

  import json
  from unittest.mock import MagicMock, patch

  import pytest

  from agents.contracts.tools import all_tools, get_tool, grantable_tools
  from agents.contracts.toolschema import openai_tool_dict
  from models.contracts.jobkinds import resolve_dotted_path
  from models.contracts.operations import ParamError
  from tools.vision.tests._helpers import make_tool_ctx, restore_tools, snapshot_tools


  @pytest.fixture(autouse=True)
  def _snapshot_tools():
      saved = snapshot_tools()
      yield
      restore_tools(saved)


  class TestRegistration:
      def test_both_are_registered_when_the_feature_is_on(self):
          keys = {spec.key for spec in all_tools()}
          assert {"vision.operations", "vision.generate"} <= keys

      def test_every_runner_resolves(self):
          for key in ("vision.operations", "vision.generate"):
              assert callable(resolve_dotted_path(get_tool(key).runner))

      def test_neither_mutates_and_both_are_grantable(self):
          """Creating NEW work product -- a generated image, a queued job --
          is not `mutates`. That word means changing state that already
          exists."""
          grantable = {spec.key for spec in grantable_tools()}
          for key in ("vision.operations", "vision.generate"):
              assert get_tool(key).mutates is False
              assert key in grantable

      def test_declared_roles(self):
          assert get_tool("vision.operations").roles == ()
          assert get_tool("vision.generate").roles == ("vision.generate",)

      def test_neither_declares_a_describer(self):
          """Ruling R3 -- the vision describer is struck from P1 entirely."""
          for key in ("vision.operations", "vision.generate"):
              assert get_tool(key).describer == ""

      def test_with_the_feature_off_no_vision_tool_is_registered(self):
          """One gate, one behaviour: with the flag off there is no vision
          role, no operations, no job kind, and now no tools.

          This test overrides FARABUNKER_FEATURES but makes NO HTTP request
          and calls NO reverse(), which is the documented escape from the
          vision-flag rule (tools/rag/tests/_helpers.py:9-38) -- it calls
          `ready()` directly.
          """
          from django.test import override_settings

          from agents.contracts import tools as tools_module
          from tools.vision.apps import VisionConfig

          saved = dict(tools_module._TOOLS)
          tools_module._TOOLS.clear()
          try:
              with override_settings(FARABUNKER_FEATURES=frozenset()):
                  VisionConfig.ready(MagicMock(spec=VisionConfig))
              assert tools_module._TOOLS == {}
          finally:
              tools_module._TOOLS.clear()
              tools_module._TOOLS.update(saved)


  class TestGenerateSpecShape:
      def test_operation_choices_are_exactly_the_registered_operations(self):
          """Derived at REGISTRATION time, after the five
          register_operation calls -- a module constant would be evaluated
          at import time and ship an empty choices tuple."""
          from models.contracts.operations import all_operations

          param = next(p for p in get_tool("vision.generate").params if p.key == "operation")
          assert set(param.choices) == {op.key for op in all_operations()}
          assert param.required is True

      def test_image_is_a_text_param_never_a_file_one(self):
          """A tool call is JSON (ADR 0012:140), so it cannot carry an
          upload object. An image input is an artifact REFERENCE."""
          param = next(p for p in get_tool("vision.generate").params if p.key == "image")
          assert param.kind == "text"

      def test_the_full_param_set(self):
          assert [p.key for p in get_tool("vision.generate").params] == [
              "operation", "prompt", "negative_prompt", "width", "height",
              "steps", "cfg", "seed", "image",
          ]

      def test_it_renders_to_a_valid_llm_schema_with_an_operation_enum(self):
          rendered = openai_tool_dict(get_tool("vision.generate"))
          assert json.loads(json.dumps(rendered)) == rendered
          props = rendered["function"]["parameters"]["properties"]
          assert set(props["operation"]["enum"])
          assert rendered["function"]["parameters"]["required"] == ["operation"]
          assert rendered["function"]["name"] == "vision__generate"

      def test_every_param_carries_a_description(self):
          """`description` is the entire prompt surface a model gets for an
          argument (ruling R2 half one)."""
          for param in get_tool("vision.generate").params:
              assert param.description


  @pytest.mark.django_db
  class TestVisionOperationsRunner:
      def test_it_calls_operation_catalog_exactly_once_with_no_picked_model(self):
          """`operation_catalog` runs ONE preflight for the whole catalog
          (services.py:319-324); calling it per-operation would pay a
          blocking health round trip each time. `resolved=None` means "the
          role binding answers", which is the only model a tool has."""
          from tools.vision.tools import run_operations

          with patch("tools.vision.services.operation_catalog", return_value=[]) as catalog:
              run_operations({}, make_tool_ctx())

          assert catalog.call_count == 1
          positional, keyword = catalog.call_args
          assert positional in ((), (None,))
          assert keyword.get("resolved") is None

      def test_the_catalog_lands_in_data_verbatim(self):
          from tools.vision.tools import run_operations

          catalog = [{"key": "txt2img", "label": "Text to image", "params": []}]
          with patch("tools.vision.services.operation_catalog", return_value=catalog):
              result = run_operations({}, make_tool_ctx())
          assert result.data["operations"] == catalog
          assert json.loads(json.dumps(result.data)) == result.data
          assert result.artifacts == ()

      def test_the_text_names_each_operation(self):
          from tools.vision.tools import run_operations

          catalog = [
              {"key": "txt2img", "label": "Text to image", "params": []},
              {"key": "upscale", "label": "Upscale", "params": []},
          ]
          with patch("tools.vision.services.operation_catalog", return_value=catalog):
              text = run_operations({}, make_tool_ctx()).text
          assert "txt2img" in text and "upscale" in text

      def test_an_unexpected_argument_is_rejected(self):
          from tools.vision.tools import run_operations

          with pytest.raises(ParamError):
              run_operations({"operation": "txt2img"}, make_tool_ctx())


  @pytest.mark.django_db
  class TestVisionGenerateRunner:
      def test_it_calls_submit_job_then_wait_for(self):
          from tools.vision.tools import run_generate

          job = MagicMock()
          with patch("tools.vision.services.submit_job", return_value=job) as submit, \
               patch("tools.vision.services.wait_for", return_value=job) as wait, \
               patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
              run_generate({"operation": "txt2img", "prompt": "a thing"}, make_tool_ctx())

          assert submit.call_args[0][0] == "txt2img"
          assert submit.call_args[0][1]["prompt"] == "a thing"
          assert wait.call_args[0][0] is job

      def test_it_drops_an_argument_the_picked_operation_does_not_declare_AND_SAYS_SO(self):
          """The tool's schema is the union across operations; `submit_job`
          validates against the PICKED one and rejects an undeclared key.
          So the runner narrows -- but never silently."""
          from tools.vision.tools import run_generate

          job = MagicMock()
          with patch("tools.vision.services.submit_job", return_value=job) as submit, \
               patch("tools.vision.services.wait_for", return_value=job), \
               patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
              result = run_generate(
                  {"operation": "upscale", "prompt": "x", "negative_prompt": "y"},
                  make_tool_ctx(),
              )

          submitted = set(submit.call_args[0][1])
          from models.contracts.operations import get_operation

          declared = {p.key for p in get_operation("upscale").params}
          assert submitted <= declared
          assert "negative_prompt" in result.text  # named, not dropped in silence

      def test_an_image_reference_resolves_to_the_operations_first_file_param(self):
          """`file_params()` returns them in DECLARATION order, and the
          "use this image here" link already pre-fills the first for
          exactly this reason (operations.py:103-111)."""
          from models.contracts.operations import get_operation
          from tools.vision.tools import run_generate

          job = MagicMock()
          with patch("tools.vision.services.resolve_inputs", return_value={"image": MagicMock()}) as resolve, \
               patch("tools.vision.services.submit_job", return_value=job) as submit, \
               patch("tools.vision.services.wait_for", return_value=job), \
               patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
              run_generate(
                  {"operation": "img2img", "prompt": "x", "image": "output:12"},
                  make_tool_ctx(),
              )

          first_file_param = get_operation("img2img").file_params()[0].key
          assert resolve.call_args[0][1] == {first_file_param: "output:12"}
          assert first_file_param in submit.call_args[1]["files"]

      def test_a_malformed_image_reference_is_refused_with_the_shape(self):
          from tools.vision.tools import run_generate

          with pytest.raises(ValueError) as excinfo:
              run_generate(
                  {"operation": "img2img", "prompt": "x", "image": "/etc/passwd"},
                  make_tool_ctx(),
              )
          assert "output:<id>" in str(excinfo.value)

      def test_a_document_reference_is_refused_for_a_generation(self):
          """`resolve_inputs` must keep refusing anything but
          output:/input: -- `parse_input_reference` is not widened."""
          from tools.vision.tools import run_generate

          with pytest.raises(ValueError):
              run_generate(
                  {"operation": "img2img", "prompt": "x", "image": "document:5"},
                  make_tool_ctx(),
              )

      def test_progress_is_reported_per_poll_with_no_fabricated_total(self):
          """ADR 0013:410-417 -- this platform never fabricates a
          denominator."""
          from tools.vision.tools import run_generate

          reported = []
          ctx = make_tool_ctx()
          object.__setattr__(ctx.job, "_report", reported.append)

          job = MagicMock()

          def fake_wait(j, timeout, interval=1.0, on_poll=None):
              on_poll(j, 3.0)
              return j

          with patch("tools.vision.services.submit_job", return_value=job), \
               patch("tools.vision.services.wait_for", side_effect=fake_wait), \
               patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
              run_generate({"operation": "txt2img", "prompt": "x"}, ctx)

          assert reported == [
              {"done": 3, "total": None, "unit": "seconds", "label": "generating"}
          ]

      def test_data_is_job_json_verbatim_and_exposes_urls_never_paths(self):
          from tools.vision.tools import run_generate

          payload = {
              "id": "abc", "status": "succeeded",
              "outputs": [{"id": 7, "url": "/vision/outputs/7/file/"}],
          }
          job = MagicMock()
          with patch("tools.vision.services.submit_job", return_value=job), \
               patch("tools.vision.services.wait_for", return_value=job), \
               patch("tools.vision.services.job_json", return_value=payload):
              result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

          assert result.data == payload
          assert json.loads(json.dumps(result.data)) == result.data
          assert result.artifacts == ("output:7",)
          serialised = json.dumps(result.data)
          assert "/data/" not in serialised and ".safetensors" not in serialised

      def test_an_unbound_role_surfaces_the_platforms_own_copy(self):
          """`VisionUnavailable` carries the message
          `services.role_unbound_message()` already writes for the page. A
          tool must not invent a second sentence for the same state."""
          from tools.vision import services
          from tools.vision.tools import run_generate

          from agents.contracts.tools import ToolRefused

          # VisionUnavailable is a RuntimeError (services.py:99), which is
          # neither of the two classes section 10.1 defines. The runner must
          # translate it to ToolRefused -- the no-retry class -- because
          # nothing the model can say will bind a role.
          assert issubclass(services.VisionUnavailable, RuntimeError)
          assert not issubclass(services.VisionUnavailable, ValueError)

          with patch("tools.vision.services.submit_job",
                     side_effect=services.VisionUnavailable("unbound", services.role_unbound_message())):
              with pytest.raises(ToolRefused) as excinfo:
                  run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

          # The platform's OWN copy, verbatim. A tool must not invent a
          # second sentence for a state the page already has words for.
          assert services.role_unbound_message() in str(excinfo.value)

      def test_a_dead_image_reference_is_a_retryable_valueerror_not_a_refusal(self):
          """Repairable -- the model can name a different artifact -- so it
          gets section 10.2's one retry, which a ToolRefused would not."""
          from agents.contracts.tools import ToolRefused
          from tools.vision import services
          from tools.vision.tools import run_generate

          assert issubclass(services.InputReferenceError, ValueError)
          with patch("tools.vision.services.resolve_inputs",
                     side_effect=services.InputReferenceError("image", "output:99 is gone.")):
              with pytest.raises(ValueError) as excinfo:
                  run_generate(
                      {"operation": "img2img", "prompt": "x", "image": "output:99"},
                      make_tool_ctx(),
                  )
          assert not isinstance(excinfo.value, ToolRefused)
          assert "output:99 is gone." in str(excinfo.value)

      def test_a_timed_out_generation_reports_honestly_and_does_not_raise(self):
          """`wait_for` returns the job AS-IS at the deadline. A
          non-terminal job is a real, reportable state -- not a failure to
          crash on."""
          from tools.vision.tools import run_generate

          job = MagicMock()
          job.is_terminal = False
          with patch("tools.vision.services.submit_job", return_value=job), \
               patch("tools.vision.services.wait_for", return_value=job), \
               patch("tools.vision.services.job_json",
                     return_value={"id": "abc", "status": "running", "outputs": []}):
              result = run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

          assert "still running" in result.text.lower()
          assert result.artifacts == ()

      def test_wait_for_polls_the_engine_and_the_queue_is_never_touched(self):
          """`services.wait_for` polls the IMAGE ENGINE about a generation it
          already submitted; it never calls `models.contracts.queue.get_job`.
          That distinction is the whole rule.

          The SOURCE-TEXT guard on that rule lives in ONE place --
          `foundation/ops/tests/test_column_boundaries.py::
          test_no_tool_runner_blocks_on_a_queue_job` (P1 Task 10), which owns
          the forbidden-name list for every tool module. A second copy here,
          with its own list, would drift from it. This test pins the
          BEHAVIOUR instead: the runner reaches the engine through
          `wait_for` and nothing else."""
          from tools.vision.tools import run_generate

          job = MagicMock()
          with patch("models.contracts.queue.get_job",
                     side_effect=AssertionError("a tool runner must never poll the queue")), \
               patch("models.contracts.queue.enqueue",
                     side_effect=AssertionError("vision.generate enqueues nothing")), \
               patch("tools.vision.services.submit_job", return_value=job), \
               patch("tools.vision.services.wait_for", return_value=job) as wait, \
               patch("tools.vision.services.job_json", return_value={"id": "x", "outputs": []}):
              run_generate({"operation": "txt2img", "prompt": "x"}, make_tool_ctx())

          assert wait.call_count == 1
  ```

- [ ] Run and expect **red**, then write `tools/vision/tools.py` implementing exactly the interfaces above. Its module docstring must state, in this order: that each runner calls the same service function the page calls; that module-scope imports stay pure so `VisionConfig.ready()` keeps its no-DB-no-heavy-imports promise; that `build_generate_spec()` is a function and not a constant because `operation`'s `choices` must be read *after* registration; that argument narrowing is announced, never silent; that the `image` reference maps to `file_params()[0]`; and that `services.wait_for` polls the image engine, not the queue — so the never-block-on-a-queue-job rule is not violated.

- [ ] Register both at the end of `VisionConfig.ready()`, after the five `register_operation` calls and the `register_job_kind` call:

  ```python
          # Tool registration (spec section 4.3). AFTER the early feature
          # exit above and AFTER the five register_operation calls: one
          # gate, one behaviour -- with the flag off there is no vision
          # role, no operations, no job kind, and no tools. And
          # `build_generate_spec()` reads `all_operations()` to fill its
          # `operation` choices, so it must run after they are registered,
          # which is why it is a function and not a module constant.
          from agents.contracts.tools import register_tool
          from tools.vision.tools import VISION_OPERATIONS, build_generate_spec

          register_tool(VISION_OPERATIONS)
          register_tool(build_generate_spec())
  ```

- [ ] Run, expect green, run the matrix under **both** flag states, update `tools/vision/README.md`, and commit:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q tools/vision/tests/test_tools.py
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools
  .venv/bin/pytest -q tools/rag/tests/test_flag_hygiene.py
  # EXPECTED: all green, including the flag-hygiene sweep over the new file.

  git add tools/vision/tools.py tools/vision/apps.py tools/vision/README.md \
          tools/vision/tests/test_tools.py tools/vision/tests/_helpers.py
  git commit -m "$(cat <<'EOF'
  feat(vision): register vision.operations and vision.generate as tools

  VISION-OWNED. Both call the same service functions the page calls:
  operation_catalog for discovery, submit_job + wait_for for a generation.
  ADR 0012:748-752 already declared that the tool seam.

  vision.generate's `operation` choices are derived at REGISTRATION time,
  after the five register_operation calls -- hence a builder function
  rather than a module constant, which would have been evaluated at import
  and shipped an empty choices tuple.

  Two behaviours worth naming. The tool's schema is the union across
  operations while submit_job validates against the PICKED one, so the
  runner narrows the arguments -- and NAMES every dropped one in its
  result text, never silently. And `image` is a "text" param carrying an
  output:/input: artifact reference (a tool call is JSON and cannot carry
  an upload object, ADR 0012:140), resolved through services.resolve_inputs
  onto the operation's FIRST file param, mirroring views.py:726-737.

  Registered AFTER the feature-flag early exit: one gate, one behaviour --
  flag off means no role, no operations, no job kind, and no tools.

  wait_for polls the IMAGE ENGINE about a generation already submitted; it
  never calls queue.get_job. The never-block-on-a-queue-job rule is intact
  and a source test pins it.

  No describer, by ruling R3.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 10: structural guards, purity, docs, and the P1 gates

**Files**
- Create: `agents/contracts/tests/test_purity.py`
- Create: `agents/contracts/README.md`
- Modify: `models/registry/tests/test_registry_paths.py` — extend the P0 resolver guard to every `ToolSpec.runner`
- Modify: `foundation/ops/tests/test_column_boundaries.py` — add the never-block-on-a-queue-job guard and the light-module guard
- Modify: `agents/README.md` — replace the "Empty in P0" table row for `contracts/` with what actually shipped
- Modify: `docs/DEV.md` — the `testpaths` line and the two run commands
- Test: the three guard modules above

**Interfaces**
- Consumes: `agents.contracts.tools.all_tools`; `models.contracts.jobkinds.resolve_dotted_path`; `subprocess`.
- Produces: no importable API.

**Steps**

- [ ] **Guard 1 — purity.** Create `agents/contracts/tests/test_purity.py`:

  ```python
  """`agents/contracts/` imports no Django. Pinned, not hoped for.

  "Pure" that is only asserted in a docstring is a hope. This imports every
  module in the package in a SUBPROCESS with no DJANGO_SETTINGS_MODULE set
  at all -- so an accidental `from django.conf import settings` fails here
  with an ImproperlyConfigured (or an unexpected `django` in sys.modules)
  rather than silently making a rule-1 pure leaf dependent on a configured
  Django, which would break its universal importability.

  The live tripwire this catches is not hypothetical: `models/contracts/
  jobkinds.py:34` imports `django.utils.module_loading`, so importing
  `JobContext` for real -- rather than under `if TYPE_CHECKING:` -- turns
  this test red immediately.
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
      import agents.contracts.artifacts
      import agents.contracts.tools
      import agents.contracts.toolschema
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
          "import agents.contracts.artifacts",
          "import agents.contracts.artifacts\n    import django.utils.timezone",
      ))
      leaked = [m for m in result.stdout.strip().split("|") if m]
      assert "django" in leaked or "django.utils.timezone" in leaked
  ```

- [ ] **Guard 2 — every `ToolSpec.runner` resolves.** Extend P0's `models/registry/tests/test_registry_paths.py`: add `ToolSpec.runner` to `_registered_paths()` so the same walk that covers `planner`/`handler`/`summarizer`/`on_terminal`/`rematerialize` now covers tool runners too. Update the module docstring to say so. These strings fail **lazily** — a wrong one boots cleanly, renders every page, passes every test that patches the underlying service, and then throws `ImportError` on the one tool call that reaches it:

  ```python
      for spec in all_tools():
          found.append((f"ToolSpec({spec.key!r}).runner", spec.runner))
  ```

  Raise the anti-vacuous floor from `>= 8` to `>= 20`. Counted at P1's end with the vision flag on: **14** P0 registrations (`rag.ask` 3 + `rag.ingest` 4 + `vision.generate` 3 + `rag.reencode` 3 + `rag.embed`'s one `rematerialize`) plus **6** tool runners and prove it bites by temporarily pointing one `ToolSpec.runner` at a nonexistent module, running, seeing red, and reverting.

- [ ] **Guard 3 — no tool runner blocks on a queue job.** Add to `foundation/ops/tests/test_column_boundaries.py`:

  ```python
  # Every module that declares tool runners. A new one is added here in the
  # same commit that creates it -- `test_every_registered_runner_lives_in_a_
  # swept_module` below makes forgetting impossible.
  TOOL_MODULES = (
      "tools/rag/tools.py",
      "tools/vision/tools.py",
      "models/registry/tools.py",
  )

  # `enqueue` is allowed (rag.ingest fires and forgets); `get_job` is not.
  _FORBIDDEN_IN_A_RUNNER = ("get_job",)


  def test_no_tool_runner_blocks_on_a_queue_job():
      """A tool runner may enqueue a queue job and return its id; it must
      never call `get_job` in a loop, and it must never call `enqueue` for
      work whose result it needs.

      On a default install `JobSettings.memory_budget_bytes` is null
      (models/queue/models.py:184-192), which
      models/queue/scheduler.py:374-380 reads as sequential mode: at most
      one job on the whole machine. An agent.turn that enqueued a job and
      blocked on it would hold the machine's one slot while the job it
      waits for can never be admitted -- then the orphan sweep marks it
      stale and, on a second orphaning, fails it permanently. Deadlock,
      then data loss. Certain, not probable.

      Crude source-text scan, deliberately -- the same shape as the
      existing structural guard against writing to the global LlamaIndex
      Settings (tools/rag/tests/test_retrieval.py:1046-1049, ADR
      0010:600-604). `tools.vision.services.wait_for` is NOT caught by
      this and must not be: it polls the IMAGE ENGINE about a generation
      already submitted and never touches the queue.
      """
      offenders = {}
      for relative in TOOL_MODULES:
          text = (REPO_ROOT / relative).read_text(encoding="utf-8")
          hits = [needle for needle in _FORBIDDEN_IN_A_RUNNER if needle in text]
          if hits:
              offenders[relative] = hits
      assert offenders == {}, offenders


  def test_every_registered_runner_lives_in_a_swept_module():
      """Anti-vacuous pin: TOOL_MODULES is a hand-maintained list, so a new
      tool module that nobody added would silently escape the sweep."""
      from agents.contracts.tools import all_tools

      swept = {relative[:-3].replace("/", ".") for relative in TOOL_MODULES}
      for spec in all_tools():
          module = spec.runner.rsplit(".", 1)[0]
          assert module in swept, f"{spec.key} runs in unswept module {module}"


  def test_no_tool_module_imports_its_service_layer_at_module_scope():
      """Registration imports no implementation module.
      `AppConfig.ready()` imports each module above to register its specs,
      and `ready()` promises no DB and no heavy imports at startup. Every
      runner therefore imports lazily, INSIDE its body -- the same shape
      tools/rag/categories.py:40 already uses.
      """
      import ast

      offenders = {}
      allowed_prefixes = ("agents.contracts", "models.contracts", "__future__")
      # NOT allowed, and this is the point: `tools.rag.retrieval`,
      # `tools.rag.ingest`, `tools.vision.services`, `models.registry.bindings`.
      # Every one of those is a lazy, in-body import in the runners.
      for relative in TOOL_MODULES:
          tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
          for node in tree.body:                     # MODULE SCOPE ONLY
              if isinstance(node, ast.ImportFrom) and node.module:
                  if not node.module.startswith(allowed_prefixes):
                      offenders.setdefault(relative, []).append(node.module)
              elif isinstance(node, ast.Import):
                  for alias in node.names:
                      if not alias.name.startswith(allowed_prefixes):
                          offenders.setdefault(relative, []).append(alias.name)
      assert offenders == {}, offenders
  ```

- [ ] Prove each new guard bites, then revert each probe:
  - point one `ToolSpec.runner` at `nowhere.at.all.run` → the resolver guard goes red naming that tool;
  - add `from models.contracts.queue import get_job` to `tools/rag/tools.py` → guard 3 goes red;
  - add `from tools.rag import retrieval` at module scope in `tools/rag/tools.py` → the light-module guard goes red;
  - add `import django.utils.timezone` to `agents/contracts/tools.py` → the purity guard goes red.

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q agents/contracts/tests/test_purity.py \
                      models/registry/tests/test_registry_paths.py \
                      foundation/ops/tests/test_column_boundaries.py
  # EXPECTED after each revert: all passed.
  ```

- [ ] Write `agents/contracts/README.md`: the three modules, the rule-1 purity claim and the test that pins it, the raise-vs-None split on `get_tool`, the `mutates` definition and why a mutating tool is registered-but-not-grantable, ruling R3's inert `describer`, and the never-block-on-a-queue-job rule. Update `agents/README.md`'s table so the `contracts/` row describes what shipped rather than what was planned.

- [ ] Update `docs/DEV.md`: the `testpaths` value (`tools models foundation agents scripts`) and the reversed-order command (`.venv/bin/pytest -q scripts agents foundation models tools`). Check what `foundation/ops/tests/test_docs_sync.py` asserts before editing — it pins four backup/restore constants verbatim against `docs/OPERATIONS.md`, none of which this phase touches, but confirm rather than assume.

- [ ] **The P1 gates** (spec §12.2), in order:

  ```bash
  # Gate 1 -- every tool's runner resolves.
  .venv/bin/pytest -q models/registry/tests/test_registry_paths.py

  # Gate 2 -- every tool round-trips through openai_tool_dict.
  FARABUNKER_FEATURES='vision,media' .venv/bin/python -c "
  import json, os, django
  os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from agents.contracts.tools import all_tools, describe_tool
  from agents.contracts.toolschema import key_from_wire_name, openai_tool_dict, wire_name
  for spec in all_tools():
      rendered = openai_tool_dict(spec)
      assert json.loads(json.dumps(rendered)) == rendered, spec.key
      assert key_from_wire_name(rendered['function']['name']) == spec.key
      described = describe_tool(spec)
      assert set(described) == {'key','label','description','roles','mutates','params'}
  print(f'{len(all_tools())} tools render and round-trip')
  "
  # EXPECTED: '6 tools render and round-trip'

  # Gate 3 -- each runner calls the existing service function.
  #   Asserted per-tool in Tasks 7, 8, 9 by patching that function.

  # Gate 4 -- suite green in both flag states and both collection orders.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation models tools

  # Gate 5 -- no migrations.
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "NO MIGRATIONS"

  # Gate 6 -- Django is happy and nothing shadows the stdlib.
  .venv/bin/python manage.py check
  .venv/bin/python manage.py collectstatic --noinput --dry-run
  ```

  Any difference in collected count between the configured and reversed orders is registration leakage — a module-global registry surviving between tests — and must be chased, not accepted.

- [ ] **The ladder.** P1 ships no UI, so Rung 3 is narrow but it is still a rung and it is still driven in a browser. **Restart the worker first** — `docs/DEV.md:279-290`: `watcher` and `worker` run single management commands with no reload machinery, and this phase changes `AppConfig.ready()` in three apps:

  ```bash
  docker compose restart watcher worker
  ```

  Then the **Smoke Checklist**, at Rung 2 (preview) and again at Rung 3 (live):

  1. **`/setup/`** renders every registered engine's install guide and its live reachability check. *(Proves the new `supports_tool_calling` on the protocol broke no adapter, and that `foundation.setup`'s read-only surface over the engine registry still works.)*
  2. **`/inference/`** renders the model console, and "Supported APIs" still lists each engine with its source path. *(Proves `InferenceConfig.ready()`'s new `register_tool` call did not break app startup, and that the engine registry is intact.)*
  3. **`/rag/`** loads, an ask returns an answer with citations, and an upload goes PENDING → READY. *(Proves `RagConfig.ready()`'s three new `register_tool` calls did not break the app that owns the most job kinds — and that the worker, restarted above, still resolves them.)*
  4. **`/vision/`** renders its operation chooser and one generation completes. *(Proves `VisionConfig.ready()`'s two new registrations sit correctly after the feature-flag early exit and after `register_operation`.)*
  5. **`/queue/`** lists the jobs from steps 3-4 with their summaries. *(Proves no job kind's registration was disturbed.)*
  6. **`manage.py shell`** → `from agents.contracts.tools import all_tools, describe_tool; [describe_tool(s) for s in all_tools()]` prints six described tools with no `runner` and no `describer` key. *(The one direct look at what P1 actually built.)*
  7. With `FARABUNKER_FEATURES='vision'` only, repeat steps 1-3 and confirm `/vision/` still exists; then with the flag genuinely off in a scratch shell, confirm `all_tools()` contains the four non-vision tools and neither vision one. *(One gate, one behaviour. `FARABUNKER_FEATURES=''` is not a supported state to run the SUITE under, `docs/DEV.md:238-244`, so this is a `manage.py`-level probe.)*

  **Rung 4 — fresh pixels on the owner's live system.** Only after Rung 4 may this work be described as done.

- [ ] Commit:

  ```bash
  git add agents/contracts/README.md agents/contracts/tests/test_purity.py agents/README.md \
          models/registry/tests/test_registry_paths.py \
          foundation/ops/tests/test_column_boundaries.py docs/DEV.md
  git commit -m "$(cat <<'EOF'
  test(agents): permanent structural guards for the tool contract, plus docs

  Four guards, each proven to bite by a deliberate red run:

  - agents/contracts imports NO Django, pinned in a SUBPROCESS with no
    DJANGO_SETTINGS_MODULE set. "Pure" asserted only in a docstring is a
    hope; this is a property.
  - Every ToolSpec.runner resolves through resolve_dotted_path, folded into
    P0's existing walk over planner/handler/summarizer/on_terminal/
    rematerialize. These strings fail LAZILY -- a wrong one boots cleanly
    and throws on the one tool call that reaches it.
  - No tool runner calls get_job. `enqueue` is allowed (rag.ingest fires
    and forgets); blocking is not, because sequential mode makes it a
    certain deadlock. services.wait_for polls the IMAGE ENGINE and is
    correctly not caught.
  - No tool module imports its service layer at MODULE scope (AST check),
    so AppConfig.ready() keeps its no-DB-no-heavy-imports promise.

  Each guard carries an anti-vacuous pin so a stale module list or a broken
  probe fails loudly instead of passing by looking at nothing.

  docs/DEV.md updated for the new testpaths and reversed-order command.
  New agents/contracts/README.md; agents/README.md's contracts row now
  describes what shipped.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

## Coverage against the spec

| Spec item | Task |
|---|---|
| §4.1 `ToolSpec` frozen dataclass, `TOOL_PARAM_KINDS`, `__post_init__` rules | 2 |
| §4.2 `ToolResult`, `ToolContext`, `StepBudget` | 2 |
| §4.3 `register_tool` / `all_tools` / `get_tool` (raises) / `grantable_tools` / `describe_tool` | 4 |
| §4.3 registration in each `AppConfig.ready()`, vision after its flag gate | 7, 8, 9 |
| §4.4 one validation floor — `HasParams`, `validate_tool_args` | 1, 2 |
| §4.5 artifacts (`ARTIFACT_KINDS`, `parse_artifact`, `artifact_url_name`); `parse_input_reference` not widened | 5 |
| §4.6 `wire_name` / `key_from_wire_name` / `openai_tool_dict`; no `FunctionTool`, no `predict_and_call` | 3 |
| §4.7 `supports_tool_calling` on the protocol + `OllamaEngine`; `"tools"` stays out of `CAPABILITIES` | 6 |
| §4.8 / R2 half one — `Param.description` kept and consumed as the prompt surface | 3 (test), 7, 8, 9 (every param carries one) |
| §4.8 / R3 — `describer` declared, inert, never called; no live-facts keys | 2, 3, 4, 7, 8, 9 |
| §5 `rag.search` / `rag.ask` / `rag.ingest` | 7 |
| §5 `models.status`, DB-only (ruling R2) | 8 |
| §5 `vision.operations` / `vision.generate` (VISION-OWNED) | 9 |
| §6.3 a tool runner never blocks on a queue job | 7 (rag.ingest), 9 (wait_for is not the queue), 10 (guard) |
| §11.1 no `conftest.py`; per-package `_helpers.py`; helpers duplicated per app | 2, 7, 8, 9 |
| §11.1 the vision-flag rule | Global Constraints; 5, 9 |
| §11.2 HTTP-layer engine doubles (`fake_ollama_show`) | 6 |
| §11.3 tool-contract test list (post-init rejections, idempotent register, raising `get_tool`, `grantable_tools`, array for `multiple`, one-key-per-field `describe_tool`, wire-name round trip, `ParamError` per-arg reasons) | 2, 3, 4 |
| §11.3 structural guard — every `ToolSpec.runner` resolves | 10 |
| §12.2 `pytest.ini` `testpaths` gains `agents` in P1 | 2 |
| §12.2 gate — every tool's runner resolves | 10 |
| §12.2 gate — every tool round-trips through `openai_tool_dict` | 10 |
| §12.2 gate — each runner calls the existing service function | 7, 8, 9 |
| §12.2 gate — suite green in both flag states | every task; 10 |
| §12.2 gate — no new UI, so Rung 3 is "the suite plus `/setup/` still renders" | 10 |
| §12.2 VISION-OWNED scope, describer struck | 9 |


---

## Plan review

### Round 1 — AMEND (4 MAJOR / 6 minor). Author applied all findings.

**MAJOR**

| # | Finding | Applied in |
|---|---|---|
| M5 | The purity test would have failed on its first run. `models/contracts/jobkinds.py:34` does `from django.utils.module_loading import import_string` at module scope, so `agents/contracts/tools.py`'s plain `from models.contracts.jobkinds import JobContext` would drag Django into a rule-1 pure leaf. | `ToolContext.job` is annotation-only and `from __future__ import annotations` is in force, so the import moved under `if TYPE_CHECKING:` with the reason stated at the import, on the dataclass, in `__init__.py`, in the purity test's docstring, and in the Global Constraints. |
| M6 | Task 8's `models/registry/tools.py` docstring explained the ruling by naming the engine-probe method it refuses to call — and its own `test_the_source_makes_no_engine_call_of_any_kind` scans that file's text for exactly that name. The module would have failed its own guard on its explanation. | Docstring reworded to "runs no health probe", plus an explicit note that this module never spells that name and why. |
| M7 | Task 9 carried a local source-text test forbidding `get_job` **and** `enqueue` in `tools/vision/tools.py` — a second copy of Task 10's guard 3 with a different forbidden list, guaranteed to drift. | The duplicate is deleted. Task 10's guard owns the rule and the list for every tool module; Task 9's replacement pins the **behaviour** instead — `queue.get_job`/`queue.enqueue` patched to raise, and `wait_for` asserted called exactly once. |
| M8 | `services.VisionUnavailable` is a **`RuntimeError`** (`services.py:99`), not a `ValueError`. §10.1 classifies tool failures into refused (no retry) and raised (one retry); a bare `RuntimeError` fits neither and would reach P2's `invoke_tool` unclassified. The Task 9 test asserted `pytest.raises(ValueError)`, which would not have caught it. | Task 9's Produces section now names the base classes and mandates translation in a table: `VisionUnavailable` → `ToolRefused` with `role_unbound_message()` preserved verbatim; `InputReferenceError` (a `ValueError`) → `ValueError`, message preserved; `ParamError` propagates unchanged. Two tests pin the split. |

**minor (all 6 applied)** — m8 the anti-vacuous floor in the resolver guard is **`>= 20`**, not 14: P0's registrations already total 14 (`rag.ask` 3 + `rag.ingest` 4 + `vision.generate` 3 + `rag.reencode` 3 + `rag.embed`'s `rematerialize`), plus the six v1 tool runners; m9 `_search_result_for` is **moved** to `retrieval.py` beside `_vector_citations` — the sibling its own docstring names — in a pure-relocation commit, rather than imported by a tool module out of a *view* module, which would invert the dependency direction and make the view layer load-bearing for a background job; m10 accepted as authored, no change; m11 line references corrected (`DISCOVERY_TIMEOUT` at `ollama.py:45`; `test_unknown_engine_capability_is_dropped` at `test_engines.py:254-262`; the optional-engine-seam block cited consistently as `base.py:400-431`); m12 Task 2's Interfaces now names what is consumed **at runtime** (`Param`, `validate_params`) and separates what is not (`ParamError` is raised *through* `validate_params`; `HasParams` is satisfied structurally and never imported), and `__init__.py`'s summary adds `ToolRefused` and `validate_tool_args`; m13 `test_artifacts.py`'s docstring now states its one deliberate column crossing — a *test* importing `tools.vision.services` to prove the two parsers cannot drift, which is not what rule 2 governs.

### Orchestrator ruling

**Accepted all.** No further review round: the executor re-verifies every `file:line` reference against the tree before running each task, and P1's own baseline is measured on the merged post-P0 tree rather than predicted.
