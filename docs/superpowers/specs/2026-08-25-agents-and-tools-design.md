# Agents & Tools — design spec

**Date:** 2026-08-25
**Status:** Design, not built. Written against `HEAD = eb75b1d`.
**Phase:** ROADMAP Phase 1.6 (`docs/ROADMAP.md:227-257`), superseding that phase's stated shape in three named places (§13.3).
**Lands as:** ADR 0015 plus five PRs (§11).

This spec designs the central agent layer: a tool contract every feature registers against, an
agent runtime that drives tools either by LLM decision or by a fixed process flow, and a
permanent chat product at `/chat/`. It also specifies the physical regroup of the repository
into four columns that the layer requires.

No model names or versions appear anywhere in this document. That is deliberate and permanent:
the repository is going public, and ADR 0010's third amendment
(`docs/adr/0010-model-management-framework.md:290-380`) already forbids the platform from
naming a model for the operator.

---

## Table of contents

1. [Context](#1-context)
2. [Owner decisions, and the three places this spec deviates](#2-owner-decisions-and-the-three-places-this-spec-deviates)
3. [Target tree, move map, and the import law](#3-target-tree-move-map-and-the-import-law)
4. [The tool contract](#4-the-tool-contract)
5. [The v1 tools](#5-the-v1-tools)
6. [The agent runtime](#6-the-agent-runtime)
7. [Data model](#7-data-model)
8. [The chat surface](#8-the-chat-surface)
9. [Engines, and the hosted-API path](#9-engines-and-the-hosted-api-path)
10. [Error handling](#10-error-handling)
11. [Testing](#11-testing)
12. [Phasing](#12-phasing)
13. [Rejected alternatives](#13-rejected-alternatives)
14. [Named gaps and out of scope](#14-named-gaps-and-out-of-scope)

---

## 1. Context

### 1.1 What exists today

The platform already has four of the five pieces an agent layer needs, each built as a
frozen-dataclass registry with dotted-path callables and a module-level dict:

| Registry | Definition | Registration | Lookup |
|---|---|---|---|
| Roles | `core/inference/roles.py:58` `RoleSpec` | `register_role` (`roles.py:81`) | `all_roles`/`get_role` (`roles.py:90`, `:95`) |
| Operations | `core/inference/operations.py:79` `Operation` | `register_operation` (`operations.py:128`) | `all_operations`/`get_operation`/`operations_for` (`operations.py:135`, `:140`, `:145`) |
| Job kinds | `core/inference/jobkinds.py:175` `JobKind` | `register_job_kind` (`jobkinds.py:243`) | `all_job_kinds`/`get_job_kind` (`jobkinds.py:253`, `:258`) |
| Engines | `core/inference/engines/base.py:274` `InferenceEngine` | `register` (`engines/__init__.py:18`) | `get_engine` (`engines/__init__.py:23`) |

Every one of them is registered from an `AppConfig.ready()` that deliberately imports no
implementation module — `modules/rag/apps.py:16-24` and `modules/vision/apps.py:24-33` both say so
in their own docstrings, and the planner/handler/summarizer are stored as strings resolved lazily by
`core.inference.jobkinds.resolve_dotted_path` (`jobkinds.py:273-288`, a thin wrapper over Django's
`import_string`).

Two schema utilities already exist and are already documented as the seam a chatbot tool will use:

- `core.inference.operations.describe(operation) -> dict` (`operations.py:150`), whose own docstring
  names "a chatbot tool's parameter list, an MCP tool definition" as its purpose.
- `core.inference.operations.validate_params(operation, raw) -> dict` (`operations.py:250`), whose
  docstring says "the page's form and (later) the chatbot tool both land here".

ADR 0012 states the same commitment twice: "A chatbot tool is simply a caller of
`modules.vision.services` (`preflight`/`submit_job`/`refresh_job`/`wait_for`/`delete_job`) — the same
seam the `/vision/` page itself calls" (`docs/adr/0012-image-generation-engine-adapter.md:748-752`),
and "`core.inference.operations.validate_params` is the schema *floor* every caller shares, including
a future chatbot tool that builds no Django form at all" (`0012:676-679`).

The fifth piece — the tool registry itself — does not exist. Nothing in the repo defines a tool,
an agent, or a conversation surface. The only mention of MCP anywhere in `docs/` is inside a quoted
copy of `operations.describe`'s own docstring
(`docs/superpowers/plans/2026-08-24-vision-architecture-adjustments.md:692`) — i.e. the same
forward-looking sentence quoted above, not a separate commitment.

### 1.2 The two facts that constrain the whole design

**The one-door rule.** ADR 0013 §1 (`docs/adr/0013-inference-execution-queue.md:43-53`):
`core/inference/queue.py::enqueue()`/`get_job()` "is now the *only* way a model runs", and
"every future model-consuming feature (`chat.converse`, `vision.generate`) must register a job kind
rather than reach for a synchronous call, by construction (there is no other seam left to reach
for)" (`0013:328-333`). An agent turn is a model execution. It is therefore a job.

**Sequential mode is the default.** `console/jobs/models.py:184-192` ships
`JobSettings.memory_budget_bytes` as `null`, and `console/jobs/scheduler.py:374-380` reads that as
rule 1:

```python
    if budget_bytes is None:
        # Rule 1: sequential mode. At most one job on the whole machine,
        # ever -- admit the head candidate only, and only if nothing is
        # running (a job already running IS that one job).
        if running or not ordered:
            return []
        return [ordered[0].job_id]
```

A running job blocks every admission. A handler that enqueues a sub-job and blocks waiting for it
therefore deadlocks with certainty, not with probability, on a default install. This is the single
most important structural constraint on the agent runtime and it drives §6.3.

**Honesty note:** ADR 0013 does *not* state "nested enqueue is forbidden". Grepping `docs/`,
`console/jobs/*.py` and `core/inference/*.py` for `nested`, `job-waits`, `waits on a job` returns
nothing. The deadlock is *implied* by rule 1 above, by "admissible *only* into a genuinely idle
machine" (`0013:161-163`), and by the no-backfill proof whose stated justification is "it makes
deadlock structurally impossible" (`0013:101-106`) — a proof a job-waits-on-job breaks. ADR 0015
must therefore **state this as a new decision**, not cite it as an existing one.

---

## 2. Owner decisions, and the three places this spec deviates

The owner's decisions are binding and are carried through verbatim in substance:

| # | Decision | Where realized |
|---|---|---|
| 1 | Agent brain is *both* an LLM-native bounded ReAct loop *and* fixed process flows, through one tool contract | §6.2 (loop), §6.5 (flows), §4 (one contract) |
| 2 | Agents are DB rows; resident agents declared in code and synced into rows (`resident=True`, not editable/deletable); tools are code-registered | §7.1, §7.5 |
| 3 | A permanent chat product at `/chat/`, not an agent-builder UI; ships with base-level resident agents | §8, §7.5 |
| 4 | Full physical regroup into four columns; Django app labels preserved | §3 |
| 5 | One chat turn = one queue job (`agent.turn`); tools invoked inline, never nested enqueue | §6.1, §6.3 |
| 6 | Agent-as-tool with hard depth and step bounds | §6.4 |
| 7 | Hosted API engines designed-for, not built | §9 |
| 8 | ADR 0010 amendment constraints hold: capability-scoped tools, no grantable settings-mutating tools, RAG-as-tool reuses the existing seam | §4.3, §5, §13 |

### 2.1 Deviation 1 (name only) — the fourth column cannot be called `platform/`

The owner named the fourth column `platform/`. Its **contents are exactly as decided**; only the
directory name changes, for a reason this repository has already ruled on once.

ADR 0010 §2 (`docs/adr/0010-model-management-framework.md:128-133`), verbatim:

> **Why not `platform/`:** it shadows the Python standard library's `platform` module.
> `import platform` anywhere in this codebase would become ambiguous (or require every such import
> to guard against resolving to our package instead of the stdlib one), a footgun for zero naming
> benefit. `console/` was free of that collision and already reads naturally as "the operator's
> console into the box."

Verified independently, three ways:

1. `python3 -c "import sys; print('platform' in sys.stdlib_module_names)"` → `True`. The same check
   returns `False` for `tools`, `models`, `agents`, and `foundation`.
2. Reproduced: with a `platform/__init__.py` present in the working directory, CPython 3.13 raises
   `AttributeError: module 'platform' has no attribute 'system' (consider renaming
   '.../platform/__init__.py' since it has the same name as the standard library module named
   'platform' and prevents importing that standard library module)`. `manage.py` puts the repo root
   at `sys.path[0]`, which wins over the stdlib.
3. 77 modules in the project's own `site-packages` do a bare `import platform`, including
   `django/contrib/staticfiles/management/commands/collectstatic.py`. A `platform/` package at the
   repo root breaks `manage.py collectstatic`, which the Docker image runs.

Zero modules in `site-packages` import a bare `models`, `tools`, `agents`, or `foundation`.

**This spec uses `foundation/`.** Alternates, if the owner prefers a different word:
`chassis/`, `bunker/`. `console/` is *not* an alternate — see §3.4 for why retaining it would
break the module-boundary law rather than uphold it.

### 2.2 Deviation 2 (sequencing) — VISION-OWNED work and the P0 move

The owner marked the vision package move as VISION-OWNED and delegated to a separate session.
The move touches `config/settings.py:232` and `config/urls.py:20`, which the rest of the P0 move
also touches. Two PRs editing the same four lines is a guaranteed conflict on a repo whose live
deploy merges main SHAs only (`docs/DEV.md:302-316`).

**Recommendation:** P0 is one PR containing the whole `git mv` (vision included — it is a pure
rename plus four string edits, no vision logic), and VISION-OWNED covers only the *code* work in
P1 (`tools/vision/tools.py`, `ToolResult` adoption). If the owner keeps the move itself
VISION-OWNED, sequence as P0a (everything but vision) then P0b (vision) back-to-back in one day,
with the vision session rebasing on P0a. Flagged as an owner decision, not settled here.

### 2.3 Deviation 3 (scope addition) — `ChatSession`/`ChatMessage` retirement changes a public seam

Retiring those tables (owner decision, §7.6) also retires the `session_id` parameter of
`modules/rag/retrieval.py::answer_question` (`retrieval.py:461-467`), because that parameter's only
effect is the write at `retrieval.py:571-574`. ADR 0010's agent amendment names that parameter
explicitly — "`modules/rag/retrieval.py::answer_question` — with its `session_id`-keyed grounding —
is the retrieval path" (`0010:277-280`). ADR 0015 must therefore amend ADR 0010 on this point, not
merely cite it. Recorded in §13.

---

## 3. Target tree, move map, and the import law

### 3.1 Target tree

```
config/                      unchanged (settings, urls, asgi, wsgi)
manage.py                    unchanged
scripts/                     unchanged

tools/                       one folder per tool-owning feature; a future tool is one new folder
  rag/                       Django app, label "rag"
  vision/                    Django app, label "vision"

models/                      model handling AND execution
  contracts/                 pure, Django-free (was core/inference/)
    roles.py  operations.py  jobkinds.py  queue.py  bindings.py  gateway.py  catalog.py
    engines/                 base.py  ollama.py  comfyui.py  whisper.py  comfyui_workflows/
  registry/                  Django app, label "inference"  (was console/inference/)
  queue/                     Django app, label "jobs"       (was console/jobs/)

agents/                      the central brain
  contracts/                 pure, Django-free — the TOOL CONTRACT (new)
    tools.py                 ToolSpec, ToolResult, ToolContext, StepBudget, registry
    toolschema.py            ToolSpec -> LLM tool-calling JSON, and back
    artifacts.py             the artifact-reference vocabulary
  runtime/                   Django app, label "agents" (new)
    models.py apps.py jobs.py loop.py flows.py resident.py services.py
    management/commands/sync_agents.py
  chat/                      Django app, label "chat" (new)
    views.py urls.py apps.py templates/chat/

foundation/                  shared, feature-agnostic platform code
  format.py                  was core/format.py     (pure leaf)
  files.py                   was core/files.py      (pure leaf)
  ops/                       Django app, label "ops"    (was console/ops/)
  setup/                     Django app, label "setup"  (was console/setup/)
  templates/_shell.html      was templates/_shell.html
```

`core/`, `console/`, and `modules/` cease to exist. `modules/home/README.md` (a placeholder with no
code) moves to `tools/home/README.md`.

There is no `static/` directory anywhere in the repo today (`STATIC_URL = "static/"` at
`config/settings.py:321`, no `STATICFILES_DIRS`, no `STATIC_ROOT`); all CSS/JS is inline in
templates. "Shared static" in the owner's decision therefore has no files to move — only
`templates/_shell.html`, which becomes `foundation/templates/_shell.html` and requires
`TEMPLATES[0]["DIRS"]` to change from `[BASE_DIR / "templates"]` to
`[BASE_DIR / "foundation" / "templates"]` (`config/settings.py:254`).

### 3.2 File-level move map

Every package and loose file, old path → new path. Every one of these is `git mv` only — no content
change except the string edits enumerated in §3.5.

| Old | New |
|---|---|
| `core/inference/roles.py` | `models/contracts/roles.py` |
| `core/inference/operations.py` | `models/contracts/operations.py` |
| `core/inference/jobkinds.py` | `models/contracts/jobkinds.py` |
| `core/inference/queue.py` | `models/contracts/queue.py` |
| `core/inference/bindings.py` | `models/contracts/bindings.py` |
| `core/inference/gateway.py` | `models/contracts/gateway.py` |
| `core/inference/catalog.py` | `models/contracts/catalog.py` |
| `core/inference/engines/base.py`, `ollama.py`, `comfyui.py`, `whisper.py`, `__init__.py` | `models/contracts/engines/` (same filenames) |
| `core/inference/engines/comfyui_workflows/` (`__init__.py`, `_fragments.py`, `txt2img.py`, `img2img.py`, `inpaint.py`, `upscale.py`, `flux2_edit.py`, `qwen_edit.py`) | `models/contracts/engines/comfyui_workflows/` (same filenames, unchanged contents) — see §3.8 (R1) |
| `core/format.py` | `foundation/format.py` |
| `core/files.py` | `foundation/files.py` |
| `core/tests/test_format.py`, `core/tests/test_files.py` | `foundation/tests/test_format.py`, `foundation/tests/test_files.py` |
| `core/README.md` | `models/contracts/README.md` (split: the format/files paragraph to `foundation/README.md`) |
| `console/inference/` (36 `.py` incl. 7 migrations, 16 tests, 2 management-command files; + 9 templates + README) | `models/registry/` |
| `console/jobs/` (22 `.py`: 9 top-level, 3 migrations, 7 tests incl. 6 test modules, 3 management; + 2 templates) | `models/queue/` |
| `console/jobs/management/commands/run_jobs.py` | `models/queue/management/commands/run_jobs.py` — called out explicitly because it is the **compose worker entrypoint**. `compose.yaml`'s command is `manage.py run_jobs`, i.e. a *command name*, which Django resolves by walking each installed app's `management/commands/` directory — so the package move does not touch it. The file moves; the compose command does not change. |
| `console/ops/` (12 `.py` incl. 4 tests and 3 management-command files) | `foundation/ops/` |
| `console/setup/` (all 4 py + 1 template + 1 test + README) | `foundation/setup/` |
| `modules/rag/` (61 `.py`: 20 top-level, 13 migrations, 24 tests incl. 23 test modules, 4 management; + 6 templates + README) | `tools/rag/` |
| `modules/vision/` (37 `.py`: 10 top-level, 6 migrations, 20 tests incl. 19 test modules; + 10 templates + README) | `tools/vision/` |
| `modules/home/README.md` | `tools/home/README.md` |
| `templates/_shell.html` | `foundation/templates/_shell.html` |
| `core/__init__.py` | **deleted** (`core/` ceases to exist) |
| `console/__init__.py` | **deleted** (`console/` ceases to exist) |
| `modules/__init__.py` | **deleted** (`modules/` ceases to exist) |
| `core/inference/__init__.py` | `models/contracts/__init__.py` |
| `core/tests/__init__.py` | `foundation/tests/__init__.py` |
| — | **new**: `tools/__init__.py`, `models/__init__.py`, `agents/__init__.py`, `foundation/__init__.py` |

Every other `__init__.py` (23 of them: each app package, its `migrations/`, its `tests/`, its
`management/` and `management/commands/`, plus `core/inference/engines/` and
`core/inference/engines/comfyui_workflows/`) rides along inside its own package and needs no
attention. Four new empty top-level `__init__.py` files are the only files P0 creates from
nothing; `models/contracts/__init__.py` and `foundation/tests/__init__.py` are renames of the two
rows above, not new files.

Five of the console/inference test modules test `core/inference` code rather than console code
(`test_roles.py`, `test_jobkinds.py`, `test_queue_seam.py`, `test_engines.py`, `test_catalog.py`),
and six vision test modules do the same (`test_engine_base.py`, `test_gateway.py`,
`test_comfyui_engine.py`, `test_comfyui_generator.py`, `test_comfyui_workflows.py`,
`test_operations.py`). They exist where they do because `pytest.ini:4` sets
`testpaths = modules console scripts`, which excludes `core/`. The regroup fixes the cause
(§3.6.3); moving those files to a `models/contracts/tests/` package is **optional cleanup,
explicitly deferred out of P0** so that P0 stays a provable pure rename. They ride along inside
`models/registry/tests/` and `tools/vision/tests/` and keep passing.

### 3.3 The import law (post-regroup)

Three rules replace the current two-sentence law (`docs/adr/0010-model-management-framework.md:121-124`,
as amended at `:521-524`).

**Rule 1 — pure leaves are universally importable.** `foundation/format.py`, `foundation/files.py`,
everything under `models/contracts/`, and everything under `agents/contracts/` are pure: no Django
models, no Django views, no database access, and no import of any non-pure module. Any column may
import them, in any direction. This is exactly what `core/` meant, split so the pure code sits
beside what it serves. ADR 0014 already states the criterion for a shared leaf —
`core/format.py::format_timecode` is one because "four modules across a module boundary call it …
which is precisely the condition for a shared leaf in `core/`"
(`docs/adr/0014-media-ingestion.md:623-634`); today `core/format.py` has 12 production import sites across 6 packages
(`modules/vision/models.py:22`, `modules/vision/views.py:28`, `modules/rag/sidecar.py:39`,
`modules/rag/models.py:15`, `modules/rag/retrieval.py:109`, `modules/rag/ingest.py:102`,
`modules/rag/views.py:42`, `console/inference/models.py:22`, `console/inference/views.py:132`,
`console/jobs/views.py:45`, `console/ops/backup.py:52`, `console/ops/restore.py:36`), and
`core/files.py` has 3 (`modules/rag/ingest.py:101`, `console/ops/backup.py:51`,
`console/ops/restore.py:35`).

**Rule 2 — Django apps are column-private.** `tools/rag`, `tools/vision`, `models/registry`,
`models/queue`, `agents/runtime`, `agents/chat`, `foundation/ops`, `foundation/setup` are not
importable across a column boundary, with exactly one named exception, carried over verbatim in
substance from ADR 0010's dependency-direction amendment (`0010:521-524`):

> A §5 module MAY import from `console.inference.bindings` — and ONLY that one module — as the
> single sanctioned seam onto trusted platform code. `console.inference.models` and
> `console.inference.views` remain off-limits to every module, with no exception.

Post-regroup: a `tools/*` **or** `agents/*` app may import `models.registry.bindings`, and only
that module. `models.registry.models` and `models.registry.views` stay off-limits. Today's four
importers (`modules/rag/jobs.py:74`, `modules/rag/views.py:41`, `modules/vision/jobs.py:101`,
`modules/vision/views.py:25`) are the complete set *today*; the agent layer adds its own
(`agents/runtime/jobs.py` and `agents/chat/views.py`, for `resolve_connection_named`/`picker_options`
/`role_primary`), which is why the rule names both column families rather than pretending the list
is closed.

**Rule 3 — cross-column *work* goes through a seam, never an import.** Three seams:

- the queue: `models.contracts.queue.enqueue`/`get_job` (`queue.py:77`, `:92`), dispatching through
  `settings.INFERENCE_QUEUE_BACKEND`;
- the gateway: `models.contracts.gateway.get_llm`/`get_llm_for`/`get_embed_model`/
  `get_embed_model_for`/`get_image_generator`/`get_image_generator_for`/`get_transcriber`/
  `get_transcriber_for` (`gateway.py:65-180`);
- the tool registry: `agents.contracts.tools`, whose `ToolSpec.runner` is a dotted-path string
  resolved at call time by `models.contracts.jobkinds.resolve_dotted_path` (`jobkinds.py:273`).

Rule 3's third clause is what keeps `agents/` from importing `tools/` at module scope, exactly as
`AppConfig.ready()` today registers a job kind without ever importing its handler
(`modules/rag/apps.py:16-24`). There is no import cycle: `tools/*` imports `agents.contracts`
(pure, rule 1); `agents/runtime` reaches `tools/*` only through a string.

### 3.4 Why the fourth column cannot keep the name `console/`

`foundation/format.py` and `foundation/files.py` are rule-1 pure leaves that `tools/rag` and
`tools/vision` import directly. If the fourth column were named `console/`, those imports would
read `from console.format import format_timecode` in a `tools/` app — a direct violation of the
standing law that a feature module never imports `console/` except the one bindings seam
(`0010:121-124`, `:521-524`). Retaining `console/` would therefore require *widening* that
exception, which is precisely what the amendment at `0010:526-544` refuses to do. A fresh
name with a fresh, explicit law (rule 1 above) is the honest option, and it is what the owner's
`platform/` decision was reaching for.

### 3.5 What makes the move DB-free — the exact mechanism

**Every one of the six AppConfigs already sets `label` explicitly:**

| AppConfig | `name` | `label` |
|---|---|---|
| `console/inference/apps.py:6` `InferenceConfig` | `:21` `"console.inference"` | `:22` `"inference"` |
| `console/jobs/apps.py:6` `JobsConfig` | `:19` `"console.jobs"` | `:20` `"jobs"` |
| `console/ops/apps.py:8` `OpsConfig` | `:25` `"console.ops"` | `:26` `"ops"` |
| `console/setup/apps.py:7` `SetupConfig` | `:13` `"console.setup"` | `:14` `"setup"` |
| `modules/rag/apps.py:7` `RagConfig` | `:12` `"modules.rag"` | `:13` `"rag"` |
| `modules/vision/apps.py:10` `VisionConfig` | `:17` `"modules.vision"` | `:18` `"vision"` |

Django derives a label from the module path only when the subclass has not set one —
`django/apps/config.py:34-35`:

```python
        if not hasattr(self, "label"):
            self.label = app_name.rpartition(".")[2]
```

Because every subclass sets `label`, that line never runs for this project. Changing `name` from
`"modules.rag"` to `"tools.rag"` therefore changes nothing label-derived. Concretely:

- **Table names.** No model in the repo sets `Meta.db_table` (grep: zero occurrences), so every
  table is `f"{label}_{model}"`. Renaming `models/registry` does not rename
  `inference_modelconnection`. `console/ops/backup.py:56-66` hardcodes seven of these
  (`"rag_document"`, `"rag_category"`, `"rag_askrecord"`, `"vision_generationjob"`,
  `"inference_modelconnection"`, `"inference_rolebinding"`, `"data_rag_chunks"`) with the rationale
  spelled out at `backup.py:24` — "(`f"{app_label}_{model_name.lower()}"`) rather than importing the
  models". They stay correct.
- **Index names.** `console/jobs/models.py:140,144` names its indexes `"jobs_claim_scan"` and
  `"jobs_orphan_sweep"` as literals. Literals do not move.
- **`django_migrations`.** Rows are keyed `(app_label, name)`. Across the repo's 26 migration
  files, every one of the ~10 non-empty `dependencies` entries is intra-app and label-keyed (`("rag", "0001_initial")`,
  `("inference", "0002_seed_from_env")`, …). There are no cross-app dependencies and no
  `swappable_dependency` anywhere. No fake-migrate is needed.
- **`django_content_type`.** Keyed by `app_label`. Unchanged, so no orphaned content types and no
  permission churn.
- **Historical model lookups.** Migrations use `apps.get_model("rag", "Category")`
  (`modules/rag/migrations/0004_seed_default_categories.py:19,29`,
  `0006_merge_duplicate_categories.py:14-15`) and `apps.get_model("inference", "ModelConnection")`
  (`console/inference/migrations/0002_seed_from_env.py:39,40,79`). All label-keyed.
- **Templates.** `APP_DIRS: True` (`config/settings.py:255`) discovers `<app>/templates/` from
  `AppConfig.path`, which Django re-derives from the imported module
  (`django/apps/config.py:47`). Template *names* are label-namespaced dirs
  (`templates/rag/ask.html`, `templates/inference/console.html`), so every
  `render(request, "rag/ask.html")` keeps resolving.
- **URL names.** All literals (`"rag-ask"`, `"inference-console"`, `"jobs-queue"`,
  `"vision-create"`, `"setup-index"`), never dotted paths. Every `reverse()` keeps working.
- **Management commands.** Discovered by walking each app's `management/commands/` dir. Command
  names are filename-derived; `compose.yaml:86,112` and `Dockerfile:32` reference only command names
  and `config.*`.

**Therefore: the P0 regroup PR contains zero migrations.** `manage.py makemigrations --check
--dry-run` must exit 0 on it. That is a P0 gate (§12.1).

### 3.6 Every path reference that must be rewritten

Four classes, in descending order of how loudly they fail: import statements (fail at import),
dotted-path literals (fail lazily, at job execution), filesystem-path literals (three of the four
fail *silently*), and prose/config references (never fail, just lie).

#### 3.6.1 Import statements (594: 228 prod / 366 test)

The bulk of the diff, and the class the move map's "pure rename" framing must not hide. Every
`from {core,console,modules}.…` / `import {core,console,modules}.…` line is rewritten. Counted at
`HEAD = eb75b1d` with
`grep -rn "^\s*from \(core\|console\|modules\)\.\|^\s*import \(core\|console\|modules\)\." --include="*.py" .`:

| Source package | Total lines | Prod | Test |
|---|---|---|---|
| `core.inference` | 301 | 121 | 180 |
| `modules.rag` | 91 | 39 | 52 |
| `console.inference` | 72 | 26 | 46 |
| `console.jobs` | 54 | 13 | 41 |
| `modules.vision` | 45 | 10 | 35 |
| `core.format` | 15 | 12 | 3 |
| `console.ops` | 9 | 3 | 6 |
| `core.files` | 5 | 3 | 2 |
| `console.setup` | 2 | 1 | 1 |
| **Total** | **594** | **228** | **366** |

228 production lines across **64 files**; 366 test lines. These are mechanical (`sed`) and they fail
loudly — a stale import is an `ImportError` at app load, so a green suite is sufficient proof for
this whole class.

**Consequence for the P0 estimate:** P0 is not a metadata-only rename. It is ~230 file moves plus
594 import rewrites plus ~1116 `mock.patch` target rewrites plus the literals below — a large,
mechanical, reviewable-by-grep diff, not a small one. Budget it as such.

#### 3.6.2 Dotted-path literals (35 strings in 8 files)

This is the complete production list. There are **no** dotted-path literals anywhere else in
production code: not in a view, model, service, engine, template, or management command.

**`config/settings.py`** (9):

| Line | Current | New |
|---|---|---|
| `:140` | `"console.inference.bindings.db_provider"` | `"models.registry.bindings.db_provider"` |
| `:149` | `"console.jobs.backend"` | `"models.queue.backend"` |
| `:231` | `"modules.rag"` | `"tools.rag"` |
| `:232` | `"modules.vision"` | `"tools.vision"` |
| `:233` | `"console.inference"` | `"models.registry"` |
| `:234` | `"console.jobs"` | `"models.queue"` |
| `:235` | `"console.setup"` | `"foundation.setup"` |
| `:236` | `"console.ops"` | `"foundation.ops"` |
| `:268` | `"modules.vision.context_processors.features"` | `"tools.vision.context_processors.features"` |

Plus `:254` `TEMPLATES[0]["DIRS"]` → `[BASE_DIR / "foundation" / "templates"]`, and two new
`INSTALLED_APPS` entries in P2/P3 (`"agents.runtime"`, `"agents.chat"`).

**`config/urls.py`** (5): `:8` `"modules.rag.urls"` → `"tools.rag.urls"`; `:9`
`"console.inference.urls"` → `"models.registry.urls"`; `:10` `"console.jobs.urls"` →
`"models.queue.urls"`; `:14` `"console.setup.urls"` → `"foundation.setup.urls"`; `:20`
`"modules.vision.urls"` → `"tools.vision.urls"`.

**`AppConfig.name`** (6): one per `apps.py`, as tabulated in §3.5. **`label` is not touched.**

**Registration strings** (14 live + 1 in a comment):

| File:line | String |
|---|---|
| `modules/rag/apps.py:34` | `rematerialize="modules.rag.services.reencode_all"` |
| `modules/rag/apps.py:65,66,67` | `plan_ask`/`run_ask`/`summarize_ask` |
| `modules/rag/apps.py:96,97,98,100` | `plan_ingest`/`run_ingest`/`summarize_ingest`/`on_ingest_terminal` |
| `modules/vision/apps.py:58,59,60` | `plan_generate`/`run_generate`/`summarize_generate` |
| `console/inference/apps.py:39,40,41` | `plan_reencode`/`run_reencode`/`summarize_reencode` |
| `modules/rag/apps.py:81` | (a comment naming a dotted path — update for truth) |

**These 14 fail LAZILY.** Registration stores the string without importing it, and
`resolve_dotted_path` (`jobkinds.py:288`) re-imports fresh at execution time with no caching. A move
that misses one boots cleanly, renders every page, and passes every test that mocks the handler —
and then throws `ImportError` the first time a real background job of that kind runs. The two
existing in-tree mitigations are the string-equality assertions at `modules/rag/tests/test_apps.py:42,101-103,119-121`,
`modules/vision/tests/test_apps.py`, and `console/inference/tests/test_jobkinds.py`. P0 must also add
a direct guard: a test that walks `all_job_kinds()` and `all_roles()` and calls
`resolve_dotted_path` on every `planner`/`handler`/`summarizer`/`on_terminal`/`rematerialize`
string, asserting each resolves. That test is cheap, permanent, and would have caught this class of
breakage before it reached a worker.

**One migration imports a project module** — the highest-severity single item in the move.
`modules/rag/migrations/0006_merge_duplicate_categories.py:8`:

```python
from modules.rag.categories import choose_canonical
```

Django imports every migration module when building the graph, so a stale path here breaks
`migrate` and `makemigrations` outright, not just that one migration. The fix is **not** to rewrite
the import: a migration must be frozen against the app code it ran with, and this one is already
coupled to a function that can change under it. **Inline `choose_canonical` into the migration
module** (it is small and pure) and delete the import. That is a content change in a migration file,
so it is the one deliberate exception to "P0 is a pure rename" — call it out in the PR body, and
pin it with a test that the migration graph loads (`manage.py makemigrations --check --dry-run`
already does this).

#### 3.6.3 Filesystem-path literals

**Four filesystem-path literals fail SILENTLY** (they become no-ops instead of failing):

| File:line | Literal | Consequence if missed |
|---|---|---|
| `pytest.ini:4` | `testpaths = modules console scripts` | Tests stop being collected. At P0 it becomes `testpaths = tools models foundation scripts` — which also finally brings today's uncollected `core/tests` (29 tests, `docs/DEV.md:259-266`) into the default run. `agents` is appended in **P1**, which is when `agents/` first exists (`agents/contracts/`, §12.2) — pytest errors on a `testpaths` entry that does not exist, so it cannot be added earlier, and must not be forgotten until P2. |
| `modules/rag/tests/test_retrieval.py:1046-1049` | `for app_dir in ("modules/rag", "core/inference")` | The global-`Settings` guard test silently stops guarding. |
| `modules/rag/tests/test_flag_hygiene.py:82-84` | `for tree in ("modules", "console")` | The vision-flag hygiene sweep silently stops sweeping. |
| `console/inference/tests/test_views.py:5768` | `assert ollama["source_path"] == "core/inference/engines/ollama.py"` | Fails loudly (good) — `_engine_source_path` (`console/inference/views.py:291-304`) computes a real relative path. |

The three silent ones are a P0 gate: each must be updated *and* proven still-guarding by a
deliberate red run (temporarily reintroduce the thing they guard against, confirm they fail).

#### 3.6.4 `mock.patch` targets (~1116 across 49 test files)

**~1116 `mock.patch("…")` targets across 49 test files** must be rewritten (602 `modules.rag`,
237 `console.inference`, 218 `core.inference`, 54 `modules.vision`, 3 `console.ops`,
2 `console.jobs`). These fail loudly at test time — `mock.patch` raises on an unimportable target —
so a mechanical `sed` plus a green suite is sufficient proof for this class.

#### 3.6.5 Prose, comment, docstring, and config path references

These never fail — they just become lies, which is worse in a codebase whose docstrings are load
bearing. All must be swept:

- **`config/settings.py`** comments naming a moved module: `:51` (`console`), `:59`
  (`core.inference.engines.whisper.WhisperEngine`), `:64` (`console.inference.discovery.discover`),
  `:174` (`modules.rag.views.document_upload`), `:184` (`modules.rag.store.store_file`).
- **Every `apps.py` class docstring**, each of which names its own package path and its siblings'
  (e.g. `console/inference/apps.py:7-18`, `modules/rag/apps.py:8-9`).
- **`console/inference/views.py:291-296`** — `_engine_source_path`'s docstring gives
  `"core/inference/engines/ollama.py"` as its worked example, and
  `console/inference/tests/test_views.py:5768` asserts that exact string (§3.6.3).
- **Deploy files**: `compose.yaml:113` and `compose.preview.yaml:162` (both cite
  `console/jobs/worker.py`), `Dockerfile:15` (cites `modules/rag/transcode.py::probe_duration`).
- **`docs/` — 21 files** contain a `core/`, `console/`, or `modules/` path reference
  (`grep -rl "core/inference\|console/jobs\|console/inference\|modules/rag\|modules/vision\|core\.inference\|console\.jobs\|modules\.rag" docs/`).
  P4 (§12.5) owns the doc sweep; P0 owns the code comments and the three deploy files.

Gate for this class: after the sweep,
`grep -rn "core/inference\|console/\|modules/" --include="*.py" --include="*.yaml" --include="Dockerfile" .`
returns only intentional historical references (an ADR quote, a migration comment about what a
column used to be called), each one deliberate and reviewed.

### 3.7 A named hazard in the `models/` name

`models/` does not collide with the stdlib and no installed package imports a bare `models`
(verified: 0 files in `site-packages`). But every Django `models.py` in the repo opens with
`from django.db import models`, and files under `models/` will also carry absolute imports like
`from models.contracts.roles import CAPABILITIES`. Those two do not conflict — `from X.Y import Z`
resolves through `sys.modules`, never through a module-level name — but a bare `import models`
followed by `models.contracts.…` *would* be shadowed. **Convention, to be stated in
`models/README.md` and in each column's module docstring: never `import models`; always
`from models.<sub> import …`.** No exception exists in the codebase today and none should be
created.

### 3.8 R1 — the ComfyUI workflow template registry is preserved verbatim

Binding constraint from the vision track. The graph-template registry stays exactly what it is
today: a module-level dict keyed by `(model family, operation key)`, plus a second dict keyed by
`(family, variant)` for per-variant param defaults.

`core/inference/engines/comfyui_workflows/__init__.py:54-61`:

```python
_TEMPLATES: dict[tuple[str, str], Template] = {
    ("", "txt2img"): txt2img.build,
    ("", "img2img"): img2img.build,
    ("", "inpaint"): inpaint.build,
    ("", "upscale"): upscale.build,
    ("flux2", "edit"): flux2_edit.build,
    ("qwen_image", "edit"): qwen_edit.build,
}
```

and `__init__.py:126-128`:

```python
_VARIANTS: dict[tuple[str, str], dict[str, dict]] = {
    ("flux2", flux2_edit.DISTILLED): {"edit": flux2_edit.DISTILLED_DEFAULTS},
}
```

with the four public readers `get_template(operation_key, family="")` (`:64`),
`template_keys(family="")` (`:82`), `families()` (`:95`), and
`variant_defaults(operation_key, family="", variant="")` (`:150`).

**Where it lands: `models/contracts/engines/comfyui_workflows/`.** It is pure, Django-free graph
construction consumed by exactly one adapter (`ComfyUIEngine.supported_operations` returns
`template_keys(family)` verbatim, `core/inference/engines/comfyui.py:638-650`), so it belongs
beside that adapter under rule 1 — *not* under `tools/vision/`, which would invert the dependency
(a `models/contracts` engine adapter cannot import a `tools/` package).

**P0 constraint:** the eight files move unchanged. `_TEMPLATES` and `_VARIANTS` keep their exact
keys, the four readers keep their exact signatures, and
`modules/vision/tests/test_comfyui_workflows.py` moves with `tools/vision/tests/` and stays green
without an edit beyond its patch targets. Any change to the keying is out of scope for every phase
in this spec.

**Known gap, recorded for ROADMAP Phase 1.55 (§12.5), not closed here:** `_TEMPLATES` covers
`("", txt2img|img2img|inpaint|upscale)` and `(flux2|qwen_image, edit)` only. A multi-file family has
no `txt2img` template — `(flux2, "txt2img")` is the named example — so a connection declaring that
family reports a narrower operation list than the model can actually do
(`template_keys("flux2") == ("edit",)`). Full onboarding means: every `(family, operation)` pair a
model genuinely supports gets a template. That is vision-track work, planned in Phase 1.55, not
built by this spec.

---

## 4. The tool contract

Lives in `agents/contracts/` — pure, Django-free, universally importable (rule 1).

### 4.1 `ToolSpec` — a frozen dataclass in a module-level registry

Deliberately **not** a class hierarchy. Every sibling registry in this codebase
(`RoleSpec`, `Operation`, `JobKind`) is a frozen dataclass with dotted-path strings for its
callables, specifically so registration never imports the implementing module — see
`jobkinds.py:175-184` and `modules/vision/apps.py:51-55`, which says so in a code comment. A base
class would force that import at `ready()` time and break the property those docstrings exist to
protect.

```python
# agents/contracts/tools.py

from dataclasses import dataclass, field

from models.contracts.operations import Param, ParamError, validate_params

# Param kinds a tool may declare. "file" is excluded: a tool call is JSON
# (ADR 0012:140 -- "future chatbot tool call is JSON, so neither can carry an
# upload object"), so an image input is an artifact REFERENCE (see
# agents/contracts/artifacts.py), never an upload object.
TOOL_PARAM_KINDS = frozenset({"text", "int", "float", "choice", "seed", "asset"})


@dataclass(frozen=True)
class ToolSpec:
    """One tool an agent may call.

    `runner` is a dotted-path string to `callable(args: dict, ctx: ToolContext)
    -> ToolResult`, resolved lazily by
    `models.contracts.jobkinds.resolve_dotted_path` -- never a live callable, so
    a `ToolSpec` stays plain data and registration imports nothing, exactly like
    `JobKind.handler`.

    `roles` is the set of `RoleSpec.key`s this tool's runner may consume. The
    agent-turn planner unions these across an agent's granted tools and declares
    the result to the queue at enqueue time; a tool that consumes no model
    declares `()`.

    `mutates` is True when the tool changes state that ALREADY EXISTS --
    configuration, a role binding, a platform setting, or previously-ingested
    content. Creating new work product (a generated image, a queued job, a
    conversation turn) is not `mutates`. A `mutates=True` tool is registered but
    NOT grantable until Identity & Auth lands (ADR 0010:266-276).
    """

    key: str                          # "rag.search" -- namespace is the owning column
    label: str                        # operator-facing
    description: str                  # what the LLM reads; the whole prompt surface of a tool
    params: tuple[Param, ...] = ()
    roles: tuple[str, ...] = ()
    runner: str = ""
    mutates: bool = False

    def __post_init__(self) -> None:
        if not self.key or " " in self.key:
            raise ValueError(f"Tool key must be a non-blank, space-free string, got {self.key!r}")
        if "__" in self.key:
            # `wire_name` maps "." -> "__" and `key_from_wire_name` maps back
            # (§4.6). A key that already contains "__" makes that round trip
            # ambiguous, so it is a definition-time error, not a runtime
            # surprise on the one tool call that happens to hit it.
            raise ValueError(f"Tool key must not contain '__' (wire-name collision): {self.key!r}")
        if not self.runner:
            raise ValueError(f"Tool {self.key!r} declares no runner")
        for param in self.params:
            if param.kind not in TOOL_PARAM_KINDS:
                raise ValueError(
                    f"Tool {self.key!r} param {param.key!r}: kind {param.kind!r} is not usable "
                    f"in a JSON tool call; must be one of {sorted(TOOL_PARAM_KINDS)}"
                )
```

`Param` is reused verbatim from `models/contracts/operations.py:41-75` — same fields, same
`__post_init__` kind validation, same `description` field (added last in field order on purpose,
`operations.py:66-69`).

### 4.2 `ToolResult`, `ToolContext`, `StepBudget`

```python
@dataclass(frozen=True)
class ToolResult:
    """What a runner returns.

    `text` is what goes back to the LLM as the tool message -- the only part the
    model ever sees. `data` is JSON-safe structured payload for the UI (RAG
    citations land in `data["citations"]`, in the exact dict shape
    `modules/rag/retrieval.py:325-339` already produces). `artifacts` are
    JSON-safe reference strings (see agents/contracts/artifacts.py) -- never
    filesystem paths, never bytes.
    """

    text: str = ""
    data: dict = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()


class StepBudget:
    """The one mutable object in this module: a turn's remaining allowance,
    SHARED by every tool call and every delegated sub-agent in that turn.

    Mutable by design -- steps are spent. `ToolContext` stays frozen and holds a
    reference, the same shape `JobContext` uses for its injected closures
    (models/contracts/jobkinds.py:118-121).
    """

    def __init__(self, *, steps: int, deadline_monotonic: float, recoveries: int = 1) -> None:
        self.steps_left = steps
        self.deadline_monotonic = deadline_monotonic
        self.recoveries_left = recoveries

    def spend(self, n: int = 1) -> None: ...
    @property
    def exhausted(self) -> bool: ...       # steps_left <= 0
    @property
    def expired(self) -> bool: ...         # time.monotonic() >= deadline_monotonic


@dataclass(frozen=True)
class ToolContext:
    """The second argument every tool runner is called with.

    `job` is this turn's `models.contracts.jobkinds.JobContext` -- the runner's
    only progress-reporting path, and the same object the vision job handler
    already threads into `services.wait_for`'s `on_poll`
    (modules/vision/jobs.py:308-336).
    """

    conversation_id: str          # UUID as a string
    agent_key: str                # the agent whose turn is calling
    depth: int                    # 0 at the top level; +1 per agent.delegate hop
    budget: StepBudget
    job: JobContext
```

### 4.3 Registry and description

Mirrors `roles.py:78-97` and `jobkinds.py:240-270` exactly, including the raise-vs-None split
(`get_role` returns `None` because a role may legitimately be absent; `get_job_kind` raises because
enqueuing an unregistered kind is a caller bug — a tool call for an unregistered tool is likewise a
bug).

```python
_TOOLS: dict[str, ToolSpec] = {}

def register_tool(spec: ToolSpec) -> None:
    """Register `spec` under its `.key`, replacing any existing entry. Idempotent,
    like `register_role`/`register_job_kind`."""
    _TOOLS[spec.key] = spec

def all_tools() -> list[ToolSpec]:
    """Every registered tool, in registration order."""

def get_tool(key: str) -> ToolSpec:
    """The tool registered under `key`. Raises `ValueError` naming it if absent."""

def grantable_tools() -> list[ToolSpec]:
    """Every registered tool with `mutates=False` -- the set an agent row may
    name in its `tool_keys`. ADR 0010:266-276: a settings-mutating tool is
    registered (so it is visible, documented, and testable) but must not be
    grantable before Identity & Auth."""

def describe_tool(spec: ToolSpec) -> dict:
    """`spec` as plain, JSON-safe data. Written out explicitly (one key per
    field) rather than via `dataclasses.asdict`, so a field added without a
    serialization decision fails a test instead of silently appearing in a
    public contract -- the exact rationale
    `models/contracts/operations.py:178-182` gives for `_describe_param`.
    Reuses `operations._describe_param` for the `params` list.

    `runner` and `describer` are INTERNAL and deliberately unserialized: one
    is a dotted path into this codebase, the other is reserved (§4.8) -- both
    are implementation, and neither belongs in a contract handed to an LLM, an
    HTTP client, or a future MCP surface. The one-key-per-serialized-field
    test must therefore assert their ABSENCE, not their presence.

    In P1-P4 this output carries NO live-facts keys. §4.8's `supported` /
    `unsupported_reason` / `options` are reserved for R2/Phase 1.55 and are
    unreachable now: nothing calls a `describer`, so nothing can produce
    them (ruling R3)."""
    return {
        "key": spec.key, "label": spec.label, "description": spec.description,
        "roles": list(spec.roles), "mutates": spec.mutates,
        "params": [_describe_param(p) for p in spec.params],
    }
```

Registration happens in `AppConfig.ready()`, gated the same way the app's roles are:
`tools/rag/apps.py::RagConfig.ready()` registers the RAG tools; `tools/vision/apps.py::VisionConfig.ready()`
registers the vision tools *after* its existing `if FEATURE not in settings.FARABUNKER_FEATURES:
return` early exit (`modules/vision/apps.py:34-35`), so with the flag off there is no vision role,
no operations, no job kind, and now no tools — one gate, one behaviour.

### 4.4 Argument validation — one floor, the existing one

`validate_params(operation, raw)` (`operations.py:250-347`) touches **exactly one** thing on its
first argument: `operation.params`, read twice (`operations.py:276,282`). It never calls
`file_param_keys()` — the `"file"` kind is handled inline in the coercion loop
(`operations.py:326-328`), by kind, not by consulting a key set. `ToolSpec` therefore needs no
`file_param_keys()` method at all, and this spec does not add one.

**Decision:** widen `validate_params`'s annotation from `operation: Operation` to a structural
protocol `HasParams` declared in `operations.py`, whose sole member is
`params: tuple[Param, ...]`. `Operation` already satisfies it; so does `ToolSpec`. This is a
two-line change to `operations.py` and no behaviour change. The alternative — a second validator in
`agents/contracts` — would create exactly the parallel seam ADR 0012:676-679 says must not exist.

`agents/contracts/tools.py` exposes:

```python
def validate_tool_args(spec: ToolSpec, raw: dict) -> dict:
    """Coerce and range-check `raw` against `spec.params`. Raises
    `models.contracts.operations.ParamError`, whose `.errors` maps a param key
    to a human-readable reason (operations.py:201-209) -- which is what makes a
    bad tool call the ONE failure worth handing back to the model for a retry
    (see §10.2)."""
    return validate_params(spec, raw)
```

### 4.5 Artifacts

```python
# agents/contracts/artifacts.py

# The artifact-reference vocabulary. "output"/"input" are vision's own,
# already parsed by `tools.vision.services.parse_input_reference`
# (modules/vision/services.py:379-389) and already minted by
# `stage_upload` (services.py:503-508) and by
# templates/vision/_output_actions.html. "document" is this spec's one
# addition, for a RAG source file.
ARTIFACT_KINDS = ("output", "input", "document")
_SHAPE = "an artifact reference looks like output:<id>, input:<id>, or document:<id>"

def parse_artifact(reference: str) -> tuple[str, int]:
    """`(kind, pk)` or `ValueError` naming the shape. Same `partition(":")` +
    `isdigit()` shape as vision's own parser, deliberately -- a reference that
    vision minted must parse identically here."""

def artifact_url_name(kind: str) -> str:
    """The URL name that serves this artifact kind's bytes:
    "output" -> "vision-output-file" (modules/vision/urls.py:23),
    "input"  -> "vision-input-file"  (modules/vision/urls.py:24),
    "document" -> "rag-document-file" (modules/rag/urls.py:34).
    The chat template reverses this; no layer below the view ever learns a
    filesystem path."""
```

`tools.vision.services.parse_input_reference` stays the authority for the two kinds vision owns and
is **not** widened — when an artifact is fed back *into* a vision tool,
`services.resolve_inputs(operation, references)` (`services.py:452`) is the caller and it must keep
refusing anything but `output:`/`input:`.

### 4.6 Rendering a tool to the LLM

**Verified against the installed packages** (the repo's venv is at
`<repo>/.venv`, Python 3.13.3): `llama-index-core 0.14.23`,
`llama-index-llms-ollama 0.10.1`, `ollama 0.6.2`, `pydantic 2.13.4`. `requirements.txt:14-16` pins
only floors (`>=0.12`, `>=0.5`, `>=0.6`), so nothing needs bumping.

What the installed LLM object supports:

- `llama_index.llms.ollama.base.Ollama` subclasses `FunctionCallingLLM`
  (`site-packages/llama_index/llms/ollama/base.py:75`, base imported at `:44`).
- `_prepare_chat_with_tools` (`base.py:327-351`) builds
  `[tool.metadata.to_openai_tool(skip_length_check=True) for tool in tools]` and returns
  `{"messages": …, "tools": tool_specs or None}`.
- `get_tool_calls_from_response(self, response, error_on_no_tool_call: bool = True) -> List[ToolSelection]`
  (`base.py:365-369`) reads `ToolCallBlock`s off `response.message.blocks` (`:371-375`). It works on
  any `ChatResponse`, regardless of how the tools were passed in.
- All four chat paths pop `tools` from kwargs and forward it to the SDK: `chat` (`base.py:403,412`),
  `stream_chat` (`:453,463`), `achat` (`:621,630`), `astream_chat` (`:537,547`). The SDK posts it to
  `/api/chat` as `tools=list(_copy_tools(tools))` (`site-packages/ollama/_client.py:390-394`), and
  its `tools` parameter accepts raw mappings (`_client.py:314`).
- `allow_parallel_tool_calls` defaults to `False` with the upstream comment "doesn't appear to be
  supported by Ollama" (`base.py:333`), and `_validate_chat_with_tools_response` calls
  `force_single_tool_call` unless parallel is allowed (`base.py:353-362`).

**Decision: emit the OpenAI-compatible tool dict ourselves and call `llm.chat(messages, tools=[…])`. Do not
build `FunctionTool` objects and do not use `predict_and_call`.** Three reasons:

1. `FunctionTool.from_defaults` (`site-packages/llama_index/core/tools/function_tool.py:172-184`)
   requires a live Python callable and synthesizes a pydantic schema from it
   (`create_schema_from_function`, `function_tool.py:242`). Our runners are dotted-path strings by
   design; building `FunctionTool`s would force an eager import of every tool module every time a
   prompt is built — the exact property `JobKind`'s dotted-path design exists to avoid.
2. It would install pydantic as a second validation floor beside `validate_params`, which
   ADR 0012:676-679 rules out.
3. `predict_and_call` (`function_calling.py:202-211`) runs the tool itself, which would hide the
   step budget, the depth guard, and the recovery policy this design must own.

```python
# agents/contracts/toolschema.py

_JSON_TYPES = {
    "text": "string", "seed": "string", "asset": "string",
    "choice": "string", "int": "integer", "float": "number",
}

def wire_name(key: str) -> str:
    """`"rag.search"` -> `"rag__search"`. The OpenAI function-name grammar is
    `^[a-zA-Z0-9_-]{1,64}$` and models are trained against it; a dotted name is
    a needless correctness risk even where a server tolerates it."""
    return key.replace(".", "__")

def key_from_wire_name(name: str) -> str:
    """The inverse. A name that round-trips through neither direction is an
    unknown tool -- handled as a tool error (§10.3), never guessed at."""
    return name.replace("__", ".")

def openai_tool_dict(spec: ToolSpec) -> dict:
    """`spec` as the tool-calling JSON both the Ollama HTTP API and every
    OpenAI-compatible API accept. A `"choice"` param whose options the ENGINE
    owns (empty `spec.params[i].choices`) emits no `enum` -- an honest "the
    schema does not know", exactly as `operations.describe` does
    (operations.py:161-163)."""
    props, required = {}, []
    for p in spec.params:
        prop = {"type": _JSON_TYPES[p.kind], "description": p.description or p.label}
        if p.kind == "choice" and p.choices:
            prop["enum"] = list(p.choices)
        if p.kind in ("int", "float"):
            if p.min is not None: prop["minimum"] = p.min
            if p.max is not None: prop["maximum"] = p.max
        if p.multiple:
            # `validate_params` answers a `multiple` param with a LIST
            # (operations.py:335-341), and the only such param today is the
            # asset one (`LORA_PARAMS`, operations.py:456-457). The schema
            # must say so, or the model sends a bare string and the tool
            # silently runs with one adapter where several were meant.
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

### 4.7 Detecting whether a bound model can call tools — and the honest fallback

`Ollama.metadata.is_function_calling_model` is **hardcoded `True`** (`base.py:127-130`, `:189-198`)
with an upstream `# TODO: Detect if selected model is a function calling model?` at `base.py:196`.
It is a declaration, not a detection, and must not be used as a check.

The real signal exists one layer down: Ollama's `/api/show` response carries a `capabilities` list
(`site-packages/ollama/_types.py:564,579` — `ShowResponse.capabilities: Optional[List[str]]`), which
includes `"tools"` for a tool-calling model. `/api/tags`' typed model does **not** declare that field
(`_types.py:518-527`), and `core/inference/engines/ollama.py:253` currently reads
`model.get("capabilities", [])` off a `/api/tags` row — which may always be absent. That is worth
probing against a live server during P1, but it does not change the design.

**Decision: add one optional method to the `InferenceEngine` protocol.**

```python
# models/contracts/engines/base.py, alongside loaded_footprint/unload (base.py:404-421)

    def supports_tool_calling(self, model_id: str, endpoint: str) -> bool | None:
        """Whether `model_id` at `endpoint` can be driven with tool calls, or
        `None` if this engine does not report it -- the same
        opportunistic-fact shape as `loaded_footprint` above: a measurement,
        never a claim. Called via `getattr(engine, "supports_tool_calling",
        None)`, so an adapter predating this method degrades to `None`, never
        `AttributeError`."""
```

`OllamaEngine` implements it against `/api/show`, reading the RAW `"tools"` string. It does **not**
touch `_map_capabilities` (`core/inference/engines/ollama.py:91-105`) or `_CAPABILITY_MAP`
(`ollama.py:63-67`), so the existing test that pins `"tools"` being dropped from the platform
vocabulary — `console/inference/tests/test_engines.py:252-261`,
`test_unknown_engine_capability_is_dropped` — stays green, untouched.

**`"tools"` is deliberately NOT added to `CAPABILITIES` (`core/inference/roles.py:19`).** That set
is the *role* vocabulary, validated by `RoleSpec.__post_init__` (`roles.py:71-75`) and, per
ADR 0010 §3 (`0010:137-141`), it *is* the manifest vocabulary. "Can call tools" is a model trait,
not a purpose a role can name; adding it would let someone register a nonsensical
`RoleSpec(..., capability="tools")`.

**The fallback, stated as behaviour:**

- `supports_tool_calling` returns **`False`** → the chat view's preflight refuses **before enqueue**
  with an honest 503 naming the role and linking to `{% url 'inference-console' %}` — the same shape
  `modules/vision/views.py:714-719` uses for an unbound role, with copy modelled on
  `modules/vision/services.py:117::role_unbound_message`.
- Returns **`None`** (engine does not report) → the turn runs. If the model emits no tool call, that
  is a final answer and the turn records `tool_calls: 0`. Honest, and indistinguishable from a model
  that simply chose not to use a tool.
- **Never** parse a tool call out of prose, never inject a hand-rolled `<tool>` syntax into the
  system prompt, never retry with a "please respond in JSON" nudge. Tool calls come from
  `llm.get_tool_calls_from_response()` or they did not happen.

### 4.8 R2 — `Param.description` is kept, and a param can be marked unsupported for the picked model

Binding constraint from the vision track, in two halves.

**Half one: `Param.description` stays.** `core/inference/operations.py:66-70` declares it last in
field order, with a code comment explaining why ("every existing construction passes `key`, `kind`,
`label` positionally and the rest by keyword, so a field added anywhere earlier would silently
change what a positional fourth argument means"). It is already the source of a form field's help
text (`modules/vision/forms.py:135-138`) and of a described param's `"description"` key
(`operations.py:180`). This spec adds a third consumer — it is the entire prompt surface a model
gets for an argument (`openai_tool_dict`, §4.6, emits `p.description or p.label`). **`description`
must not be removed, renamed, or made optional-by-omission in any phase.**

**Half two: the hook — `describe(resolved)`, a per-tool live description.** A tool's raw
`describe_tool(spec)` (§4.3) reports what the *schema* knows, engine-blind, exactly as
`operations.describe` does (`operations.py:150-163`). Vision already has the live counterpart:
`modules/vision/services.py:304::operation_catalog(resolved=None)` runs one `preflight()` and
overwrites each param's `"options"` and `"default"` from `live_options`/`live_defaults`
(`services.py:180`, `:223`). What does not exist yet, and what the future constant vision input
screen needs, is a third live fact: *is this param supported at all* for the picked model, family,
and operation.

**Design (hook only — the screen is not built here):** `ToolSpec` gains one optional field,
a dotted path to a live describer:

```python
@dataclass(frozen=True)
class ToolSpec:
    ...
    # Dotted path to `callable(spec: ToolSpec, resolved: ResolvedModel | None)
    # -> dict` returning `describe_tool(spec)` with per-param live facts merged
    # in, or "" for a tool whose schema is the whole truth. A string, not a
    # callable, for the same reason `runner` is one: registration imports
    # nothing.
    describer: str = ""
```

and `describe_tool` grows one live-facts key per param, alongside the existing
`_describe_param` output:

```python
{
    "key": "...", "kind": "...", ...,          # unchanged, from _describe_param
    "supported": True,                          # False = this model/family/operation
                                                # cannot honour this param at all
    "unsupported_reason": "",                   # operator-readable when supported is False
    "options": [...],                           # live engine options, when known
}
```

Contract, stated so the future screen can be built against it without a redesign:

- `supported` is `True` by default and by omission. A describer that cannot tell says `True` — the
  same honesty rule `describe`'s empty-`choices` case follows (`operations.py:161-163`): the schema
  reports what it knows and never guesses a restriction into existence.
- `supported=False` **must** carry a non-blank `unsupported_reason`. A disabled input with no
  explanation is a lie about what the platform supports.
- `supported` is advisory to the *renderer*, and to the renderer only. It never replaces
  `validate_params`, which stays the one floor (§4.4), and (ruling R3) it never reaches the LLM tool
  schema. A model that calls a tool with an unsupported param gets a real engine-side failure
  surfaced honestly (`GenerationRejected` → `ENGINE_REJECTED`,
  `modules/vision/services.py:654-655`), not a silently-dropped argument.
- The vision describer derives `supported` from facts that already exist: the operation the param
  belongs to, narrowed by `services.operations_for_model(resolved)` (`services.py:257`), which
  itself reads `ComfyUIEngine.supported_operations` → `template_keys(family)`
  (`core/inference/engines/comfyui.py:638-650`); plus `variant_defaults(operation_key, family,
  variant)` (`comfyui_workflows/__init__.py:150`) for a param a variant pins.
**Orchestrator ruling R3 — `describer` is an inert reserved field in this phase.** It is declared on
`ToolSpec`, documented, and defaulted to `""`; **nothing calls it.** In particular
`openai_tool_dict` (§4.6) does **not** consult it and does **not** filter params by live
`supported` — the tool schema handed to the model is built from the static `ToolSpec.params` alone,
on every turn. Two reasons: a per-prompt describer call means an engine round-trip (`preflight`)
inside the turn loop, which is exactly the unbounded-latency cost ruling R2 rejects for
`models.status`; and a tool schema that silently changes shape between turns is a debugging
nightmare for no v1 benefit. A model that passes an argument the picked connection cannot honour
gets the honest engine-side failure (`GenerationRejected` → `ENGINE_REJECTED`,
`modules/vision/services.py:654-655`), surfaced as a tool error with one recovery (§10.2). Wiring
`describer` up is R2/Phase-1.55 work, and the field exists now only so that work is a fill-in rather
than a contract change.

The screen itself — one constant vision input surface that enables and disables inputs from this
data — is Phase 1.55 work and is listed in §12.5, not built here.

---

## 5. The v1 tools

Each maps onto an existing service function. None grows a parallel seam.

| Tool key | `roles` | `mutates` | Runner → existing function |
|---|---|---|---|
| `rag.search` | `("rag.embed",)` | no | `tools.rag.retrieval.retrieve_nodes(...)` (`modules/rag/retrieval.py:344`) then `apply_score_floor(nodes, score_floor, hybrid=hybrid)` (`retrieval.py:435`) — the no-LLM path `SearchView` uses |
| `rag.ask` | `("rag.answer", "rag.embed")` | no | `tools.rag.retrieval.answer_question(...)` (`retrieval.py:461`) — the exact seam ADR 0010:277-280 names |
| `rag.ingest` | `("rag.embed",)` | **yes** | `tools.rag.ingest.enqueue_reingest(doc)` (`modules/rag/ingest.py:1229`) |
| `vision.operations` | `()` | no | `tools.vision.services.operation_catalog(resolved=None)` (`modules/vision/services.py:304`) |
| `vision.generate` | `("vision.generate",)` | no | `tools.vision.services.submit_job(...)` (`services.py:560`) + `services.wait_for(...)` (`services.py:801`) |
| `models.status` | `()` | no | `models.contracts.roles.all_roles()` + `models.registry.bindings.role_primary(key)` (`console/inference/bindings.py:192`) — **DB only, no HTTP** |
| `agent.delegate` | computed (§6.4) | no | `agents.runtime.services.run_delegate(...)` |
| `flow.run` | computed (§6.5) | no | `agents.runtime.flows.run_flow(...)` |

**`rag.search`** params: `query` (text, required), `category` (choice, engine-free — options are
`Category` names, so the schema declares `choices=()` honestly and the runner validates against the
DB), `top_k` (int, min 1, max 50, defaulting from `RagSettings.get_solo().retrieval_top_k`,
`modules/rag/models.py:405`; `get_solo` at `:444`).

The runner follows `SearchView` exactly: take `settings_row = RagSettings.get_solo()`, call
`retrieval.retrieve_nodes(query, category or None, settings_row, embed_resolved=embed_resolved)`
— which returns the **triple** `(nodes, hybrid, index)` (`modules/rag/retrieval.py:344-350`;
`SearchView` unpacks it at `views.py:1506`) — then
`retrieval.apply_score_floor(nodes, settings_row.retrieval_score_floor, hybrid=hybrid)`
(`views.py:1531`). **The score floor and the hybrid flag are read from `RagSettings`, never
from tool arguments**: they are operator policy (ADR 0014 §14), and a tool param that let a model
lower the floor would be a settings mutation wearing a search tool's clothes. Result: `text` = a
numbered list of snippets with locators;
`data["results"]` = the same dict shape `modules/rag/views.py:1334::_search_result_for` produces
(`views.py:1364-1373`); `artifacts` = `("document:<id>", …)` deduped.

**`rag.ask`** params: `question` (text, required), `category` (choice). The runner re-resolves both
roles fresh at call time — `run_ask` does exactly this and documents why ("a queued job can sit for
a while", ADR 0013:205-213). Result: `text` = the answer; `data["citations"]` = the citation dicts
verbatim from `retrieval._vector_citations` (`retrieval.py:283`, per-node dict at `:325-339`);
`artifacts` = `("document:<id>", …)`. Locators come from the existing
`retrieval.locator_for`/`locator_text_for` (`retrieval.py:129`, `:159`) — never re-derived, per
ADR 0014:611-620.

**`rag.ingest`** takes `document_id` (int, required) and nothing else. **Store-path only** in the
strongest available sense: it accepts no path at all, so the model has zero filesystem reach.
`enqueue_reingest` re-hashes the retained store copy, resets the row to PENDING, and enqueues
(`ingest.py:1229-1258`). It is `mutates=True` because a re-ingest replaces existing chunks — so it is
registered, documented, and tested, but never grantable until Identity & Auth (§4.3). Accepting a
new file path is out of scope (§14).

**`vision.generate`** params are derived at registration from the operations that are registered:
`operation` (choice, `choices` = the registered operation keys), plus `prompt`, `negative_prompt`,
`width`, `height`, `steps`, `cfg`, `seed`, and `image` (a `"text"` param carrying an
`output:<id>`/`input:<id>` reference for the image-to-image family). The runner calls
`services.submit_job(operation_key, params, files=None, resolved=picked)` — which itself runs
`validate_params` first (`services.py:613`) and `preflight` second (`services.py:615-617`) — then
`services.wait_for(job, timeout=..., on_poll=report)` with `report` forwarding to
`ctx.job.report_progress(int(elapsed), total=None, unit="seconds", label="generating")`, byte-for-byte
the pattern `modules/vision/jobs.py:308-336` already uses. Result: `text` = a one-line description;
`data` = `services.job_json(job)` (`services.py:742`, whose shape is fixed at `:759-798` and exposes
URLs, never paths); `artifacts` = `("output:<id>", …)`.

**`models.status`** — kept, per orchestrator ruling **R2**, on two grounds: it is the `models/`
column's own proof that it adopts the tool contract (without it, `models/` is the one column that
registers no tool), and it traces directly to the owner's "it should have access to the tools within
the system". But it is **DB-only**: it returns, per registered role, the role key and label, its
capability, and the display name plus connection pk from `role_primary`
(`console/inference/bindings.py:192-221` — three honest states: bound connection, explicit env
override, or genuinely unassigned).

**It makes no `is_healthy` HTTP probe, and no engine call of any kind.** Reachability is a live
network fact whose cost is unbounded and whose latency lands inside a turn that is already holding
the machine's one execution slot (§6.3); `/setup/` is the surface that answers "is the engine up",
and it answers it on demand, for a human, outside the queue. If a role is bound but its engine is
down, the agent finds out the honest way — the tool that needs it fails and surfaces the error
(§10.1). The tool reads nothing else and writes nothing. It lives in the `models/` column
(`models/registry/tools.py`), so importing `models.registry.bindings` is an in-column import and
crosses no boundary.

**`vision.operations`** exists so an agent can discover what the box can actually generate before
calling `vision.generate` — `operation_catalog` already layers a bound engine's live option lists
over `describe(op)` (`services.py:304-344`), which is exactly the answer a model needs.

---

## 6. The agent runtime

### 6.1 One chat turn = one queue job

New job kind, registered by `agents/runtime/apps.py::AgentsConfig.ready()`:

```python
register_job_kind(
    JobKind(
        key="agent.turn",
        label="Agent turn",
        planner="agents.runtime.jobs.plan_turn",
        handler="agents.runtime.jobs.run_turn",
        summarizer="agents.runtime.jobs.summarize_turn",
        default_priority=100,
        on_terminal="agents.runtime.jobs.on_turn_terminal",
    )
)
```

`default_priority=100` matches the queue-wide default (`console/jobs/models.py:181`
`DEFAULT_PRIORITY_DEFAULT = 100`), so a chat turn sits ahead of `rag.ingest` and `vision.generate`
(both 200, `modules/rag/apps.py:99`, `modules/vision/apps.py:61`) and level with `rag.ask` (which
declares `None`, `modules/rag/apps.py:68`). Lower runs first (`console/jobs/models.py:136`).

`on_terminal` is **required**, not optional. A turn has a durable side-effect that predates the job —
the `Turn` row the view creates before enqueuing — which is the exact stranded-row condition
ADR 0013:452-461 added the hook for and which `rag.ingest` already uses
(`modules/rag/apps.py:100`, `modules/rag/jobs.py:503`).

**Payload** (JSON-safe, no ORM objects, no paths — matching ADR 0014:208-211's "deliberately no
path" rule):

```python
{"conversation": "<uuid str>", "turn": <int Turn pk>, "agent": "<agent key>",
 "text": "<the user's message>", "connection": "<ModelConnection pk as str>" | None,
 "mode": "chat" | "flow", "flow": "<flow key>" | None}
```

`"connection"` is a pk as a *string*, matching both existing precedents
(`modules/rag/jobs.py:9-10`, `modules/vision/jobs.py:11-24`).

**Planner:**

```python
def plan_turn(payload: dict) -> tuple[list[ModelRef], bool]:
    """The agent's own LLM role plus the UNION of the roles of every tool it may
    call, resolved fresh at enqueue time -- the admission snapshot ADR 0013's
    `ModelRef` docstring describes (models/contracts/jobkinds.py:40-52).

    The tool set is the transitive closure over `agent.delegate`, bounded by
    MAX_AGENT_DEPTH (§6.4), de-duped by agent key so a delegation cycle
    terminates.

    THREE tolerant drops, all applied to the same list and producing the same
    filtered set the handler later builds its tool schemas from (§6.2 step 4):
    a `tool_keys` entry absent from `all_tools()` is dropped; an entry whose
    registered spec is `mutates=True` is dropped; and a tool whose declared
    role will not `resolve()` is dropped. Each is dropped from the plan AND
    from the tool list the handler hands the model -- the same tolerant shape
    `plan_ingest` uses for its extract role (modules/rag/jobs.py:408-424).
    The first two are what make ruling R1's permissive save (§7.1) safe: an
    unregistered or mutating key that reaches a row never reaches a prompt. The agent's OWN chat role is not tolerant: an
    unresolvable chat role is a hard failure, and the view's preflight has
    already refused the request before this planner ever runs (§8.3).

    Returns `exclusive=True`. A turn may load a chat model, then an embedding
    model, then an image checkpoint, sequentially, inside one job. Declaring
    that honestly is better than being folded into it by
    `effectively_exclusive`'s unmeasured-footprint branch
    (console/jobs/scheduler.py:305-334, ADR 0013:158-168), which is where an
    undeclared turn would land anyway.
    """
```

In `"flow"` mode the planner declares the union of the flow's steps' tool roles and **no chat
role** — a flow runs no LLM (§6.5).

### 6.2 The bounded ReAct loop

```python
MAX_STEPS_DEFAULT = 8                # LLM calls per turn; per-agent override via Agent.max_steps
TURN_DEADLINE_SECONDS = 900          # wall clock; > vision's own GENERATE_WAIT_TIMEOUT_SECONDS
                                     # (600.0, modules/vision/jobs.py:118) so one generate fits
HISTORY_TURNS = 20                   # prior turns replayed into the prompt


def run_turn(payload: dict, models: list[ModelRef], ctx: JobContext) -> dict:
    """Run one agent turn to completion, inline. Returns
    `{"turn_id", "text", "artifacts", "tool_calls", "steps_used", "summary"}`."""
```

Shape, in order:

1. Load `Turn`, `Conversation`, `Agent`. Build `StepBudget(steps=agent.max_steps,
   deadline_monotonic=time.monotonic() + TURN_DEADLINE_SECONDS)`.
2. Build the message list: the agent's `system_prompt` as a `MessageRole.SYSTEM` message, then the
   last `HISTORY_TURNS` turns of the conversation, then this turn's user text as
   `MessageRole.USER`.

   **A past `TOOL` turn renders as exactly two messages, in this order** — one explicit shape, so
   history replay and live loop append are byte-identical:

   ```python
   ChatMessage(role=MessageRole.ASSISTANT, blocks=[ToolCallBlock(
       tool_call_id=turn.tool_call.get("id") or "",
       tool_name=wire_name(turn.tool_call["tool"]),
       tool_kwargs=turn.tool_call["args"],
   )]),
   ChatMessage(role=MessageRole.TOOL, content=turn.text),
   ```

   `tool_call_id` is sourced from `Turn.tool_call["id"]` (§7.3) and is **reserved, not load
   bearing today**: `_convert_to_ollama_messages` builds each wire entry as
   `{"function": {"name": …, "arguments": …}}` and never reads `tool_call_id`
   (`site-packages/llama_index/llms/ollama/base.py:265-286`), so nothing this design sends to Ollama
   carries it. It is threaded through anyway because an OpenAI-compatible engine adapter (§9) does
   require it to correlate a tool result with its call, and re-deriving it later from rows that
   never stored it is impossible. Recording it costs one JSON key.

   Verified against the installed integration: `_convert_to_ollama_messages`
   (`site-packages/llama_index/llms/ollama/base.py:245-308`) turns a `ToolCallBlock` into
   `{"function": {"name": …, "arguments": …}}` under the message's `tool_calls` key (`:265-286`),
   and emits `message.role.value` verbatim as the wire role — `"tool"` for `MessageRole.TOOL`
   (`site-packages/llama_index/core/base/llms/types.py:61`). `ToolCallBlock`'s fields are
   `tool_call_id`, `tool_name`, `tool_kwargs` (`types.py:1125-1135`). The **wire** name goes in
   `tool_name`, never the dotted key, so replayed history matches what the model was originally
   offered (§4.6). `HISTORY_TURNS` is a fixed cap, not a token budget: the bound model's
   `context_window` is an operational bound the platform already owns
   (`ModelConnection.context_window`, `console/inference/models.py:77`; ADR 0010:412-435) and a
   token counter would be a second, drifting one.
3. Resolve the LLM: `get_llm_for(resolved)` where `resolved` comes from the payload's `connection`
   — `resolved, connection_name = resolve_connection_named(int(pk), "chat")`, which returns a
   **`tuple[ResolvedModel, str]`**, not a bare `ResolvedModel`
   (`console/inference/bindings.py:317`); the name is what the assistant turn's "answered by" line
   records — or from `resolve(agent.role_key)` when no override was picked. Never
   `get_llm()`-by-default — the picker override is explicit, matching `run_ask`'s
   `_resolve_answer` (`modules/rag/jobs.py:109`).
4. Build the tool schemas from the SAME filter the planner applied (§6.1), so `get_tool` is only
   ever called on a key that is present:

   ```python
   granted_and_resolvable = [
       key for key in agent.tool_keys
       if (spec := _registered(key)) is not None      # absent -> dropped, never get_tool'd
       and not spec.mutates                            # mutating -> dropped (ADR 0010:266-276)
       and _roles_resolve(spec)                        # unresolvable role -> dropped
   ]
   tool_dicts = [openai_tool_dict(get_tool(k)) for k in granted_and_resolvable]
   ```

   `_registered(key)` is `_TOOLS.get(key)` — a None-returning lookup, deliberately, because
   `get_tool` RAISES on an absent key (§4.3, matching `get_job_kind`) and an absent key is normal
   here, not a bug. Empty for a no-tool agent, in which case the loop is a single
   `llm.chat(messages)` call.
5. Loop, at most `budget.steps_left` times:
   - `ctx.report_progress(step, total=max_steps, unit="items", label="thinking")` —
     `"items"` is one of the three units `JobContext.report_progress` names
     (`models/contracts/jobkinds.py:123-136`), and the worker throttles the write
     (`PROGRESS_INTERVAL_SECONDS = 5`, `console/jobs/worker.py:125`), so calling it every iteration
     is free.
   - `response = llm.chat(messages, tools=tool_dicts or None)`
   - `calls = llm.get_tool_calls_from_response(response, error_on_no_tool_call=False)`
   - No calls → this is the final answer. Break.
   - One or more calls → **take `calls[0]`, discard the rest.** This is an explicit policy of this
     design, not a property of the library: `force_single_tool_call`
     (`site-packages/llama_index/llms/ollama/base.py:63`) is only reached through
     `_validate_chat_with_tools_response` (`:361-362`), i.e. through `chat_with_tools`, which this
     design does not use (§4.6). Plain `Ollama.chat()` (`:400-440`) passes the model's tool calls
     through uncapped, and `_convert_to_ollama_messages` appends every `ToolCallBlock` it is given
     (`:265-286`). So a model that emits three calls in one response really does hand us three.
     Rationale for taking one: a step is the unit the budget is denominated in, and running N tools
     per step makes the budget mean N times less; and the recovery policy (§10.2) is defined per
     failing call. The discard is **recorded, never silent** — the tool `Turn`'s `tool_call` dict
     carries `"discarded": [{"tool": …, "args": …}, …]` for every call not run, and the tool message
     fed back to the model says which one was executed. Then: `invoke_tool` inline (§6.3), append the
     assistant tool-call message and the tool result message, write a `Turn(role=TOOL, …)` row,
     `budget.spend(1)`, continue.
6. Loop exhausted without a final answer → an honest final: a fixed sentence saying the agent ran
   out of steps, plus whatever the last tool returned. Never a fabricated answer, never a silent
   truncation.
7. Write the assistant `Turn` (`state="done"`, `text`, `artifacts` accumulated from every
   `ToolResult`), and return.
8. **`run_turn` wraps steps 1–7 in its own `try/except` and writes the failure itself before
   re-raising.** This is required, not defensive: `on_terminal` does **not** fire when the handler
   ran and raised. `Worker._execute` gates the hook on a `handler_started` flag
   (`console/jobs/worker.py:618,623,670`) whose docstring says so outright — "when the handler DID
   run and raised, its own writeback (or deliberate lack of one) is the correct and complete account
   of that failure, and this hook must NOT also fire for it" (`worker.py:607-610`) — and
   `JobKind.on_terminal`'s own contract restricts it to a job "that reaches a TERMINAL state WITHOUT
   ever running its own `handler`" (`core/inference/jobkinds.py:200-204`). So:

   ```python
   try:
       ...  # steps 1-7
   except Exception as exc:
       Turn.objects.filter(pk=payload["turn"]).update(
           state=Turn.State.FAILED, error=str(exc),      # never a traceback
       )
       raise                                             # let the worker record the job failure
   ```

   This is the shape `modules/rag/jobs.py`'s handler already uses for its own Document writeback.
   `on_turn_terminal` (§10.3) covers the three paths where the handler never started; this `except`
   covers the one where it did.

`budget.expired` is checked at the top of every iteration and before every tool call; expiry ends
the turn the same way exhaustion does.

### 6.3 Tools run INLINE. A tool may enqueue; it may never wait on what it enqueued.

This is the new decision ADR 0015 must state (see §1.2's honesty note).

**Why inline.** With `JobSettings.memory_budget_bytes` at its shipped `null`
(`console/jobs/models.py:184-192`), `plan_admissions` returns `[]` whenever anything is running
(`console/jobs/scheduler.py:374-380`). An `agent.turn` that enqueued `rag.ask` and blocked on
`get_job(id)` would hold the machine's one slot while the job it waits for can never be admitted.
The worker's orphan sweep would then mark it stale after `STALE_AFTER_SECONDS = 120`
(`console/jobs/worker.py:106`) and, on a second orphaning, fail it permanently
(`models/contracts/jobkinds.py:191-195`). Deadlock, then data loss. Certain, not probable.

**Why inline is not a new seam.** Every v1 tool calls a function that the platform *already* calls
synchronously from inside a job handler:

- `vision.generate` calls `services.submit_job` + `services.wait_for` — precisely what
  `modules/vision/jobs.py:290,338-340` does inside `run_generate`. ADR 0012:748-752 already declares
  this the tool seam.
- `rag.ask` calls `retrieval.answer_question` — precisely what `modules/rag/jobs.py:244` does inside
  `run_ask`. ADR 0013:50-53 says there is exactly one code path that calls it; after this change
  there are two, both worker-side, both under the one door.
- `rag.search` calls `retrieve_nodes` + `apply_score_floor` — no model beyond the query embedding.

**The enqueue rule.** A tool *may* enqueue (fire-and-forget) and return the queue job id without
waiting — `rag.ingest` does exactly this. In sequential mode the child simply sits queued until the
turn finishes and then runs; no deadlock, because nothing blocks. **The forbidden thing is waiting.**
Stated as one sentence for the ADR and for `agents/runtime/loop.py`'s module docstring:

> A tool runner must never block on a queue job. It may enqueue one and return its id; it must never
> call `get_job` in a loop, and it must never call `enqueue` for work whose result it needs.

Enforced by a test that greps `tools/*/tools.py` and `agents/runtime/**` for `get_job` in a loop —
crude, but the same shape as the existing structural guard against writing to LlamaIndex's global
`Settings` (`modules/rag/tests/test_retrieval.py:1046-1049`, ADR 0010:600-604).

**Memory rotation still holds.** ADR 0013's one-door rule is not weakened: the turn is a job, it
declared every model it may touch to the planner, and it is `exclusive=True`, so
`Worker._evict_to_match_plan` (`console/jobs/worker.py:801`) evicts to match that plan before the
turn launches, uncapped for an exclusive job (ADR 0013:176-188). Nothing in this design calls a
model outside the queue.

### 6.4 Agent-as-tool, and bounded self-delegation

```python
MAX_AGENT_DEPTH = 2      # root turn is depth 0; a delegate is 1; its delegate is 2; no deeper
```

`agent.delegate` is **one** code-registered tool (tools are code-registered; agents are rows), with
params `agent` (text, required — an `Agent.key`) and `task` (text, required). Its `roles` field is
empty in the registry, because a delegate's roles are not knowable at registration time; the
*planner* supplies them by walking the closure (§6.1).

Runner shape:

```python
# agents/runtime/services.py
def run_delegate(args: dict, ctx: ToolContext) -> ToolResult:
    """Run a nested agent loop inline, inside the SAME turn job, sharing the
    SAME StepBudget.

    Refuses -- `ToolRefused`, no retry -- when: `ctx.depth >= MAX_AGENT_DEPTH`;
    the named agent row does not exist, is disabled, or is not reachable from
    the root agent's declared closure (so the planner's role declaration cannot
    be out-run at execution time); or the budget is already exhausted.
    """
```

Three properties make this safe:

1. **The budget is shared, not nested.** A delegate spends from the root turn's `StepBudget`. Total
   LLM calls per turn are bounded by `agent.max_steps` regardless of how the delegation tree is
   shaped. This, not the depth cap, is the real guard.
2. **The depth cap is enforced by omission, not by refusal.** At `ctx.depth == MAX_AGENT_DEPTH - 1`,
   `agent.delegate` is simply left out of the tool list handed to the delegate's LLM. The model is
   never offered a tool it will be refused for using. The `ToolRefused` branch above is the
   belt-and-braces path for a malformed call.
3. **Self-delegation is allowed and is the point.** An agent may name itself. The canonical shape —
   the "talk to itself" the owner asked for — is a resident `critic` agent with no tools and a
   critique-shaped system prompt: the main agent drafts, calls
   `agent.delegate(agent="critic", task="<draft>")`, and revises. Two extra steps out of eight, one
   depth level, same job, same budget, no new machinery.

Both the delegating call and the delegate's own tool calls write `Turn` rows with
`depth = ctx.depth + 1`, so the chat surface can render the sub-conversation collapsed and an
operator can audit exactly what ran.

### 6.5 Flows — the same tool contract, no LLM

ADR 0005:17-20 records that a reference n8n workflow was studied and explicitly rejected as an
implementation ("no n8n, no cloud"), with "Native Python (LlamaIndex + Django)" as the mapped
replacement (`docs/adr/0005-rag-module-architecture.md:36`). A flow here is data plus a small
resolver, not a workflow engine.

A `Flow` row (§7.4) holds `steps`, an ordered list:

```json
[
  {"tool": "rag.search",      "args": {"query": "$input.topic", "top_k": 5}},
  {"tool": "vision.generate", "args": {"operation": "txt2img",
                                       "prompt": "$steps.0.data.results.0.snippet"}}
]
```

```python
# agents/runtime/flows.py

_REF = "$"   # a string arg beginning with "$" is a reference, never a literal

def resolve_ref(ref: str, *, input: dict, steps: list[ToolResult]) -> object:
    """`"$input.<key>"` -> that input value.
    `"$steps.<N>.text"` -> step N's ToolResult.text.
    `"$steps.<N>.data.<path>"` -> a dotted walk into step N's `data`, where an
    all-digit segment indexes a list.
    `"$steps.<N>.artifacts.<i>"` -> one artifact reference string.
    A reference that does not resolve raises `FlowReferenceError` naming the
    reference and the step -- never `None` silently substituted, because a
    silently-empty prompt is exactly the failure a flow exists to prevent."""

def run_flow(flow_key: str, input: dict, ctx: ToolContext) -> ToolResult:
    """Run every step in order, resolving `$` references against prior results.
    Each step goes through the SAME `invoke_tool` the ReAct loop uses -- same
    validation floor, same budget, same depth guard, same error handling. A
    step failure ends the flow (no per-step recovery: a flow is a declared
    sequence, and a model is not present to reinterpret it)."""
```

Two entry points, one implementation:

- A flow **as a tool**: the code-registered `flow.run` tool (params `flow`, `input`), so an LLM agent
  can invoke a fixed sequence as a single step.
- A flow **as a turn**: `agent.turn` with `"mode": "flow"`. No LLM is built, no chat role is declared
  by the planner, no tool schemas are rendered. The turn's `text` is the last step's
  `ToolResult.text`. This is the "n8n-style tool sequence, no LLM decision" half of owner decision 1,
  and it goes through the identical tool contract.

Flows are rows for the same reason agents are: unlimited user-built ones, plus code-declared
residents synced in (§7.5). There is no flow-builder UI in this phase (§14).

---

## 7. Data model

New Django app `agents/runtime`, `name = "agents.runtime"`, **`label = "agents"`** (explicit, per
§3.5's contract). New Django app `agents/chat`, `name = "agents.chat"`, **`label = "chat"`**
(explicit; it holds no models today but declares the label so a future one is free).

### 7.1 `Agent`

```python
class Agent(models.Model):
    key = models.CharField(max_length=64)              # CI-unique, see Meta
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    system_prompt = models.TextField(blank=True, default="")
    # Which RoleSpec backs this agent's LLM. Defaults to `chat.converse`
    # (ROADMAP:237 names it); a per-agent override lets a lean model back one
    # agent and a conversational one back another -- ROADMAP:242-245's
    # "per-role model split as a first-class outcome", realized.
    role_key = models.CharField(max_length=255, default=CHAT_CONVERSE_ROLE)
    tool_keys = models.JSONField(default=list, blank=True)   # granted ToolSpec keys
    max_steps = models.PositiveIntegerField(default=MAX_STEPS_DEFAULT)
    # True for a code-declared agent synced from `agents/runtime/resident.py`.
    # A resident row is not editable and not deletable (enforced in
    # `save()`/`delete()` and by the absence of any editing surface).
    resident = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(Lower("key"), name="uniq_agent_key_ci")]
```

CI-unique-on-a-name is the house pattern: `modules/rag/models.py` Category
(`uniq_category_name_ci`), `console/inference/models.py:169-173`
(`uniq_modelconnection_name_ci`), `:194-198` (`uniq_rolebinding_role_key_ci`).

**`tool_keys` validation — ruling R1, option (a).** `Agent.save()` validates against
**`all_tools()`**, not `grantable_tools()`, and the two failure modes are treated differently:

- **A key whose registered spec is `mutates=True` is REJECTED**, with a message naming it.
  ADR 0010:266-276 binds here and admits no tolerance: a settings-mutating tool is registered but
  not grantable before Identity & Auth, so a row that grants one must never save.
- **A key that is not in `all_tools()` at all is ACCEPTED and logged** (`logger.info`, one line per
  key). This is the case a strict check gets wrong: `general` grants `flow.run`, which is not
  registered until P3, and it grants the vision tools, which are not registered at all when the
  `"vision"` feature flag is off. Rejecting those would make `sync_agents` fail on a legal install
  (§7.5). An unregistered key is a *not-yet* or a *not-here*, not a privilege escalation — there is
  nothing to escalate to, because §6.1 and §6.2 step 4 never resolve it.

That is still where ADR 0010:258-265's "tool access is capability-scoped, not ambient" becomes
code: an agent can only ever call a tool its row names *and* that is registered *and* that is
non-mutating. The three conditions are checked in different places (save, plan, prompt-build), and
all three must hold.

### 7.2 `Conversation`

```python
class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="conversations")
    title = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
```

UUID pk matches `GenerationJob` (`modules/vision/models.py:101`) and the retiring `ChatSession`
(`modules/rag/models.py:290`). `PROTECT` so a resident agent can never be deleted out from under a
conversation. `title` is set from the first ~60 characters of the first user turn; it is never
generated by a model (that would be a second, invisible model call per conversation).

### 7.3 `Turn`

```python
class Turn(models.Model):
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

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="turns")
    index = models.PositiveIntegerField()
    role = models.CharField(max_length=16, choices=Role.choices)
    text = models.TextField(blank=True, default="")
    # On a TOOL turn; null otherwise. Five keys, all JSON-safe:
    #   "tool"      -- the ToolSpec key that ran ("rag.search"), dotted, NOT
    #                  the wire name (§4.6); the wire name is derivable, the
    #                  key is what every other layer indexes by.
    #   "args"      -- the validated args dict (`validate_tool_args` output).
    #   "agent"     -- the Agent.key whose loop issued the call; differs from
    #                  the conversation's agent on a delegated turn (§6.4).
    #   "id"        -- the model-supplied tool_call_id, or "" when the engine
    #                  supplied none. Recorded so history replay can hand it
    #                  back (§6.2 step 2) without inventing one.
    #   "discarded" -- [{"tool": ..., "args": ...}, ...] for every call in the
    #                  same response that was NOT run (§6.2 step 5). Empty
    #                  list in the ordinary single-call case. Never omitted:
    #                  a missing key and an empty list must not both mean
    #                  "nothing discarded".
    tool_call = models.JSONField(null=True, blank=True)
    # ToolResult.data on a TOOL turn (RAG citations live in data["citations"]);
    # null otherwise.
    data = models.JSONField(null=True, blank=True)
    artifacts = models.JSONField(default=list, blank=True)      # ["output:12", "document:7"]
    depth = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DONE)
    error = models.TextField(blank=True, default="")
    # The `agent.turn` job that produced (or is producing) this turn. NOT a
    # ForeignKey: `agents/` may not import `models.queue` (import law rule 2),
    # exactly the reasoning `GenerationJob.queue_job_id` records
    # (modules/vision/models.py:135-143).
    queue_job_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["index"]
        constraints = [
            models.UniqueConstraint(fields=["conversation", "index"], name="uniq_turn_index"),
        ]
        indexes = [models.Index(fields=["conversation", "index"], name="agents_turn_thread")]
```

Only an ASSISTANT turn ever holds a non-`done` `state`; USER and TOOL turns are written after the
fact and are always `done`.

### 7.4 `Flow`

```python
class Flow(models.Model):
    key = models.CharField(max_length=64)              # CI-unique
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    steps = models.JSONField(default=list)             # [{"tool": ..., "args": {...}}, ...]
    resident = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(Lower("key"), name="uniq_flow_key_ci")]
```

`steps` is validated on save: every `tool` must be a registered, grantable key, and every `$`
reference must be syntactically resolvable against the steps that precede it.

### 7.5 Resident agents — code-declared, row-synced

```python
# agents/runtime/resident.py   -- pure data, no Django import

@dataclass(frozen=True)
class ResidentAgent:
    key: str
    label: str
    description: str
    system_prompt: str
    tool_keys: tuple[str, ...]
    role_key: str = CHAT_CONVERSE_ROLE
    max_steps: int = MAX_STEPS_DEFAULT

RESIDENT_AGENTS: tuple[ResidentAgent, ...] = (
    ResidentAgent(
        key="general", label="General assistant",
        description="Conversation with every safe tool on the box.",
        tool_keys=("rag.search", "rag.ask", "vision.operations", "vision.generate",
                   "models.status", "agent.delegate", "flow.run"),
        ...),
    ResidentAgent(
        key="librarian", label="Librarian",
        description="Answers only from the document library, with citations.",
        tool_keys=("rag.search", "rag.ask"), ...),
    ResidentAgent(
        key="illustrator", label="Illustrator",
        description="Turns a description into a prompt and generates an image.",
        tool_keys=("vision.operations", "vision.generate"), ...),
    ResidentAgent(
        key="critic", label="Critic",
        description="Reviews a draft and returns concrete objections. No tools.",
        tool_keys=(), ...),
)

def sync_resident_agents(AgentModel) -> dict:
    """Upsert every RESIDENT_AGENTS entry as `resident=True`, keyed CI on `key`.
    Takes the model CLASS so a data migration can pass
    `apps.get_model("agents", "Agent")` and the management command can pass the
    real class -- one implementation, no duplicated literals. Idempotent
    (`update_or_create`), matching
    `modules/rag/migrations/0004_seed_default_categories.py`'s own
    get_or_create idiom.

    NEVER raises for a tool key that is not currently registered -- a
    feature-gated tool (vision, with the flag off) or a not-yet-shipped one
    (`flow.run`, which lands in P3) is the normal case, not a fault (ruling
    R1). Such a key is written to the row verbatim and logged at INFO; the
    prompt builder drops it at run time (§6.1). Returns
    `{"created": n, "updated": n, "unregistered_keys": [...]}`."""
```

This needs **no bypass of §7.1's `save()` validation**, and must not have one: that rule already
accepts an unregistered key and only rejects a *registered* `mutates=True` one. `sync_resident_agents`
therefore goes through the ordinary `save()` path. The one thing it can still raise on is a resident
declaring a registered mutating tool — which is correct, and is a code bug caught at deploy, exactly
as ADR 0010:266-276 demands.

Two callers: the P2 data migration (`agents/0002_seed_resident_agents.py`) and
`manage.py sync_agents`, which a deploy runs after a code change that alters `RESIDENT_AGENTS`.
Django forbids DB access in `AppConfig.ready()`, so there is no third path and none is wanted.

`illustrator` is the owner's worked example: "I chat about create an image and it will create a
prompt and execute an image generation method" — the agent's system prompt says to call
`vision.operations` first, compose a prompt, then call `vision.generate`. `librarian` is the
RAG-only agent. `general` has all four safe columns plus delegation.

**Resident rows are not editable and not deletable.** `Agent.save()` refuses to change any field of
a `resident=True` row except `enabled`; `Agent.delete()` refuses outright. The chat surface offers no
editing UI at all (§8), so this is defence in depth, not the primary mechanism.

### 7.6 Retiring `ChatSession` / `ChatMessage`

**Evidence that they are dead.** The only production code that touches either model is
`modules/rag/retrieval.py`, imported at `:115` and written at `:571-574`:

```python
    if session_id:
        session, _ = ChatSession.objects.get_or_create(id=session_id)
        ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content=question)
        ChatMessage.objects.create(session=session, role=ChatMessage.Role.ASSISTANT, content=answer)
```

There are **no reads anywhere** — nothing feeds a prior turn back into a prompt, which
`retrieval.py:526-527` states in its own docstring ("it does not yet feed prior turns back into the
query as conversational memory"). The `session_id` reaching that branch comes from
`modules/rag/jobs.py:246` ← `modules/rag/views.py:1087` (read) and `:1100` (payload) ← a `session_id` key in the POST
body — and `modules/rag/templates/rag/ask.html:464-476` never sends that key. The only shipped
caller that can populate it is `manage.py ask --session <uuid>`
(`modules/rag/management/commands/ask.py:30-35,55-57`).

**Retirement plan (P2):**

1. Delete the `--session` flag from `modules/rag/management/commands/ask.py`.
2. Delete the `session_id` parameter from `answer_question` (`retrieval.py:461-467`) and the branch
   at `:571-574`, and the import at `:115`.
3. Delete `"session_id"` from `rag.ask`'s payload — `modules/rag/jobs.py:246`,
   `modules/rag/views.py:1087` and `:1100`, and the `AskView` docstring at `views.py:904-906`.
4. One migration, `rag/0013_retire_chat_tables.py`: `DeleteModel("ChatMessage")` then
   `DeleteModel("ChatSession")`. **A drop, not a data migration.** A live box may hold rows from a
   `manage.py ask --session` run; the recovery path is the backup every deploy already takes
   (`console/ops/backup.py`), and the drop is recorded in ADR 0015 and in `docs/OPERATIONS.md`.
   Nothing reads the rows, so nothing loses a feature.
5. Delete the tests that pin them: `modules/rag/tests/test_retrieval.py:569-589`,
   `modules/rag/tests/test_models.py:392-416`.
6. Amend ADR 0010's agent amendment (`0010:277-280`), which names "`answer_question` — with its
   `session_id`-keyed grounding" as the retrieval seam. The seam survives; the parameter does not.
   Conversation memory now lives in `agents.runtime.Turn`, which is a richer table
   (`tool_call`, `data`, `artifacts`, `depth`) and belongs to the agent column, not the RAG column.
7. Rewrite `docs/ROADMAP.md:245-250`, which plans to reuse them.

---

## 8. The chat surface

A permanent product at `/chat/`. **Not** an agent-builder: there is no create-agent form, no
tool-picker UI, no flow editor. Agents come from code (residents) or from rows an operator writes
by other means until a builder is designed.

### 8.1 Mounting and gating

`config/urls.py` gains `path("chat/", include("agents.chat.urls"))`, **ungated**, exactly like
`/rag/` (`config/urls.py:8`). It is not put behind a `FARABUNKER_FEATURES` token, for a concrete
reason: `docs/DEV.md:235-246` fixes the two supported suite states at `'vision,media'` and
`'vision'`, and a third token would either break that contract or leave the whole surface untested
in one of them. `agents/runtime` and `agents/chat` register unconditionally.

`foundation/templates/_shell.html` gains a **Chat** nav link beside the existing seven
(`templates/_shell.html:164-174`), ungated for the same reason.

### 8.2 Views and URLs

| URL | View | `name` | Method |
|---|---|---|---|
| `""` | `ChatIndexView(TemplateView)` — conversation list + agent picker | `chat-index` | GET |
| `"start/"` | `conversation_start` `@require_POST` | `chat-start` | POST |
| `"c/<uuid:conversation_id>/"` | `ConversationView(TemplateView)` — the thread | `chat-conversation` | GET |
| `"c/<uuid:conversation_id>/turn/"` | `turn_create` `@require_POST` | `chat-turn` | POST |
| `"c/<uuid:conversation_id>/delete/"` | `conversation_delete` `@require_POST` | `chat-conversation-delete` | POST |
| `"turns/<int:turn_id>/"` | `turn_status` | `chat-turn-status` | GET |

Templates under `agents/chat/templates/chat/`: `base.html` (`{% extends "_shell.html" %}`, matching
`modules/vision/templates/vision/base.html:1`), `index.html`, `conversation.html`, `_turn_card.html`,
`_tool_card.html`, `_unavailable.html`, `_form_errors.html`. Inline CSS/JS in
`{% block extra_style %}` / inline `<script>`, per the repo's existing zero-static-files posture.

### 8.3 The 202 + poll contract

`turn_create` follows `AskView.post` (`modules/rag/views.py:1029-1127`) step for step:

1. Validate `text` is a non-empty string → else 400 with a per-field message.
2. Validate the optional `connection` pk against the chat capability —
   `resolved, connection_name = resolve_connection_named(int(pk), "chat")`, a
   **`tuple[ResolvedModel, str]`** (`console/inference/bindings.py:317`) — → 503 on failure.
3. **Preflight, before enqueue** — the vision pattern (`modules/vision/views.py:714-719`):
   - resolve the agent's `role_key`; unbound or engine-unreachable → 503;
   - `getattr(engine, "supports_tool_calling", None)` returning `False` while the agent has
     granted tools → 503 naming the role and the model, with an "Assign a model" link to
     `{% url 'inference-console' %}` and a `/setup/#engine-<engine>` deep link, copy modelled on
     `modules/vision/templates/vision/create.html:50-65`;
   - every granted tool's roles are resolved; an unresolvable one is reported in the response as a
     note (the turn still runs, without that tool) — the tolerant half of `plan_turn`.
4. Create the USER `Turn` and the ASSISTANT `Turn(state=QUEUED)` in one transaction.
5. `enqueue("agent.turn", payload, priority=priority)` →
   `core.inference.queue.QueueUnavailable` (`queue.py:50`) → 503; any other exception → logged,
   503, and both `Turn` rows rolled back.
6. Stamp `queue_job_id` on the assistant turn, then **202** with
   `{"turn_id", "state": "queued", "position", "priority", "status_url"}` for XHR, or a redirect to
   `chat-conversation` with `?pending=<turn_id>` for the no-JS path.

`turn_status` follows `AskJobStatusView` (`modules/rag/views.py:1152-1241`) including its never-500
rule (`views.py:1157-1160`): **always 200 for a readable turn** — queued, running, done, failed and
cancelled all report state in the response *body*; only two non-200s exist, 404 for an unknown turn
and 503 for `QueueUnavailable`. Per-state bodies:

- queued → `{"state", "position", "priority"}`
- running → `{"state", "progress", "step", "label"}` (from `InferenceJob.progress`, the
  `{"done","total","unit","label"}` dict `report_progress` writes)
- done → `{"state", "html"}` where `html` is the rendered `_turn_card.html`
- failed → `{"state", "error", "setup_url"}` — `setup_url` on every failure, as `AskJobStatusView`
  does (`views.py:1232`)
- cancelled → `{"state"}`

Client polling mirrors `modules/rag/templates/rag/ask.html:355-359`: 2000 ms interval, 3 transport
retries, 10-minute ceiling, and it polls the `status_url` the 202 handed back — never a hand-built
URL.

### 8.4 Rendering

The thread renders `Turn` rows in `index` order. `depth > 0` turns render inside a collapsed
disclosure under the delegating turn.

**Tool-call cards** (`_tool_card.html`) show the tool label, the arguments as a small key/value
list, and then, by result kind:

- **Image thumbnails** — for each `output:<id>` artifact, an `<img>` whose `src` is
  `{% url 'vision-output-file' pk %}` (`modules/vision/urls.py:23`). That view streams strictly
  by primary key and clamps `Content-Type` to `image/*` or `application/octet-stream`
  (`views.py:980-1000`), so the chat never learns or exposes a filesystem path.
- **RAG citations** — from `Turn.data["citations"]`, using the keys the existing citation dicts
  already carry (`title`, `locator_text`, `score`, `document_id`;
  `modules/rag/retrieval.py:325-339`), each linking to `{% url 'rag-document-file' document_id %}`
  (`modules/rag/urls.py:34`).
- **Everything else** — `ToolResult.text`, escaped.

**Failure** renders as a card with the operator-readable `Turn.error` and a link to `/setup/`. Never
a traceback (`console/jobs/worker.py:578-582` already sets that rule for job failures).

### 8.5 The `chat.converse` role

Registered by `agents/runtime/apps.py::AgentsConfig.ready()`:

```python
register_role(RoleSpec(CHAT_CONVERSE_ROLE, "Agent conversation", "chat"))
```

with `CHAT_CONVERSE_ROLE = "chat.converse"` declared in `models/contracts/roles.py` beside the
existing role-key constants (`roles.py:29-45`), for the reason that file's own comment gives: it is
the one place both the contracts layer and every column can reach a single definition.

`rematerialize=None` — a chat role does not embed, so there is nothing to re-encode. This is
exactly what ROADMAP:236-241 predicted ("registering its own role — e.g. `chat.converse` — through
the existing role registry. The console, the Getting-models checklist, and per-role binding then
serve it with **zero framework changes**"), and it is true: the console's role rows, the
Getting-models checklist, and the drift guard all derive from `all_roles()` and pick it up with no
code change.

The `/chat/` page carries a per-conversation model picker built from
`models.registry.bindings.picker_options("chat", CHAT_CONVERSE_ROLE, selected)`
(`console/inference/bindings.py:233`) — the same function `/rag/` and `/vision/` already use.

---

## 9. Engines, and the hosted-API path

**Designed-for, not built. Self-hosted first. No hosted adapter ships in this phase.**

### 9.1 It needs no framework change

`InferenceEngine` (`core/inference/engines/base.py:274`) is already the whole contract: `name`,
`api_description`, `library_url`, `install_cmd_template`, `well_known_ports`, `is_healthy`,
`list_installed`, `build_llm`, `build_embedder`, and the optional methods read via `getattr`
(`base.py:404-421`). Registration is one line, `register(HostedChatEngine())`, in
`models/contracts/engines/__init__.py` beside the three existing calls
(`core/inference/engines/__init__.py:34-36`). ADR 0010 §1 already says "adding a second engine is one
new file plus a `register()` call" (`0010:41-49`).

A hosted adapter would declare `well_known_ports = ()`, an `api_description` naming the API shape,
a `library_url` pointing at the provider's published model list (rendered verbatim, never fetched —
`base.py:288-294`), and no `install_cmd_template` (nothing is installed). `is_healthy` becomes a
cheap authenticated probe; `list_installed` returns the provider's published models as
`InstalledModel` rows (`base.py:20-46`), each with `supports_tool_calling=True` where the provider
says so (§4.7).

Model selection stays exactly as it is: an operator registers a `ModelConnection`
(`console/inference/models.py:44`) with `engine="<hosted>"`, an `endpoint`, and a `model_id`; binds
it to a role; and `resolve()` hands the gateway a `ResolvedModel` whose `config` dict is passed
straight into `build_llm(**cfg)` (`core/inference/gateway.py:74`). Nothing in `bindings.py`,
`gateway.py`, `roles.py`, or the console changes.

### 9.2 Where the secret would live — and where it must not

`ModelConnection.config` is a `JSONField` (`console/inference/models.py:83`) and is the obvious
place an API key would land. **It must not go there.** `console/ops/backup.py` dumps that table
(`_TABLE_MODELCONNECTION = "inference_modelconnection"`, `backup.py:62`), which would write a live
credential into every backup archive.

The design: `config` stores a **pointer**, `{"credential": "<name>"}`, and the secret resolves by
that name from the Config & Secrets service — "Encrypted local store"
(`docs/ARCHITECTURE.md:153`), which does not exist yet. Until it does, the pointer resolves from an
environment variable of that name and the adapter fails loudly if it is unset. That is one honest
line of degradation, not a silent fallback.

### 9.3 What is explicitly out of scope, and the standing prior against it

Hosted API engines are **unbroken ground with a strong prior against them**, and ADR 0015 must say
so rather than cite ADR 0010 as precedent. There is no mention of API keys, credentials, or hosted
engines in ADR 0010, 0013, ARCHITECTURE, or DEV. The standing rules point the other way:

- "**No implicit phone-home.** No telemetry, license checks, model downloads, or crash reports leave
  the box." (`docs/ARCHITECTURE.md:64-65`)
- "**Default-deny egress.** Nothing reaches the public internet unless a posture profile explicitly
  opens a gated window, and even then only the airlock manager may use it."
  (`docs/ARCHITECTURE.md:59-61`)
- "the platform itself never pulls a model" (`docs/adr/0010-model-management-framework.md:182-185`)

**Therefore, stated as a constraint on any future hosted adapter:** it is forbidden in the `airgap`
and `isolated-lan` postures (`ARCHITECTURE.md:51-56`) and possible only inside a `gated-sync`
window; enabling one is an operator decision recorded in the posture profile, never a default.

Out of scope for this phase, named so they read as scoped-out: the secrets vault itself;
per-connection credential CRUD in the `/inference/` console; the posture-profile gate that would
permit egress; rate-limit, quota, and billing handling; and streaming responses.

---

## 10. Error handling

### 10.1 Where each failure surfaces

| Failure | Where caught | What the user sees |
|---|---|---|
| Blank/invalid message | `turn_create` | 400, per-field message, no rows created |
| Chat role unbound / engine unreachable | `turn_create` preflight | 503, "Assign a model" link to `/inference/` |
| Bound model cannot call tools | `turn_create` preflight | 503 naming the role; no turn queued |
| Queue tables missing/down | `turn_create` (`QueueUnavailable`, `queue.py:50`) | 503, "run migrations" copy |
| Tool arguments fail validation | `invoke_tool` → `ParamError` | a tool card showing the per-arg reasons; **one** retry (§10.2) |
| Tool raises | `invoke_tool` | a tool card with `str(exc)`, never a traceback; **one** retry |
| Tool refused (not granted / depth / mutating) | `invoke_tool` → `ToolRefused` | a tool card saying so; **no** retry |
| Step budget or deadline exhausted | the loop | an honest final turn saying it ran out of steps |
| `run_turn` raises anything else | `run_turn`'s own `except` (§6.2 step 8), then `console/jobs/worker.py:625-627` | `Turn(state=FAILED, error=str(exc))`. **`on_turn_terminal` does NOT fire here** — the hook is gated on `handler_started` (`worker.py:618,623,670`), so the handler must write its own failure |
| Turn cancelled from `/queue/` | `console/jobs/backend.py::cancel_job` | `on_turn_terminal` flips the turn to `cancelled` |
| Worker orphaned twice | `console/jobs/claim.py::_sweep_orphans` | `on_turn_terminal` flips the turn to `failed` |

### 10.2 The one-recovery rule

`StepBudget.recoveries_left` starts at 1. After a tool call fails, the loop makes **one** more LLM
call, feeding the failure back as the tool message so the model can correct itself. A second tool
failure in the same turn ends the turn with an honest final answer naming both failures.

Why one: a `ParamError` is the most repairable failure there is — `.errors` maps a param key to a
human-readable reason (`core/inference/operations.py:201-209`), which is exactly the information a
model needs to fix a call. A second failure after being told precisely what was wrong is not a
transient, and burning the remaining budget on it produces a slower version of the same wrong
answer.

Every failed tool call still spends a step. A failing loop cannot outrun the budget.

### 10.3 `on_turn_terminal`

```python
def on_turn_terminal(payload: dict, state: str) -> None:
    """One conditional UPDATE, filtered on the states a stranded turn can be in
    -- exactly the shape `modules/rag/jobs.py:503,559-565` uses for a stranded
    Document. Runs AFTER the job row's terminal write commits (all three call
    sites schedule it with `transaction.on_commit`), and its exceptions are
    caught and logged by `invoke_on_terminal`
    (models/contracts/jobkinds.py:291), never propagated -- a broken hook must
    never break cancel, the orphan sweep, or the worker's own failure
    writeback."""
    Turn.objects.filter(
        pk=payload["turn"], state__in=(Turn.State.QUEUED, Turn.State.RUNNING),
    ).update(
        state=Turn.State.CANCELLED if state == "cancelled" else Turn.State.FAILED,
        error=(
            "Cancelled from the queue before it ran."
            if state == "cancelled"
            else "The worker running this turn stopped responding; it was not retried again."
        ),
    )
```

An unknown tool name coming back from the model (a wire name that maps to no registered key,
§4.6) is a tool error, not a crash: it produces a tool turn saying the tool does not exist, spends a
step, and burns the one recovery. It is never guessed at, fuzzy-matched, or silently ignored.

### 10.4 Progress

`ctx.report_progress(step, total=max_steps, unit="items", label=<what it is doing>)` at the top of
each loop iteration; the label names the phase (`"thinking"`, `"searching the library"`,
`"generating an image"`). During a `vision.generate` tool call, `services.wait_for`'s `on_poll`
forwards `ctx.job.report_progress(int(elapsed), total=None, unit="seconds", label="generating")` —
`total=None` because the total genuinely is not known, which is the rule `JobContext` and
ADR 0013:410-417 both state ("this platform never fabricates a denominator"). The `/queue/` page
renders it with no change, and `/chat/`'s poller reads the same dict.

`checkpoint()` is deliberately **not** used. A turn's resume point would be a partially-consumed
message list plus a partially-spent budget, and replaying half a conversation into a model is worse
than failing honestly and letting the operator resend. Named as a deferral, not an oversight (§14).

---

## 11. Testing

Per ADR 0008 (`docs/adr/0008-engineering-standards.md:15-30`): tests and docs ship in the same PR,
never as a follow-up. No linter is configured in this repo (`requirements.txt:36-38` lists only
`pytest` and `pytest-django`); the standard is the surrounding code's altitude and
`CONTRIBUTING.md:62-63`.

### 11.1 Conventions this work must follow

- **No `conftest.py`, anywhere.** `find . -name conftest.py` returns nothing, and all three existing
  helper modules say so in their own docstrings (`modules/rag/tests/_helpers.py:1-7`,
  `modules/vision/tests/_helpers.py:1-7`, `console/inference/tests/_helpers.py:1-7`). New, in the phase that creates each package: `agents/contracts/tests/_helpers.py` (P1),
  `agents/runtime/tests/_helpers.py` (P2), `agents/chat/tests/_helpers.py` (P3).
- **Autouse fixtures stay defined per test module and delegate their bodies to `_helpers`.**
- **Helpers are duplicated per app, not imported across apps** — `make_job_ctx(**overrides)` exists
  in both `modules/rag/tests/_helpers.py:76` and `modules/vision/tests/_helpers.py:56` on purpose;
  the agents apps get their own.
- **Doubles are HTTP-layer for engines.** `FakeComfyUI` (`modules/vision/tests/_helpers.py:118`)
  patches `httpx.get`/`.post` so the adapter's real URL building and parsing run; the engine's own
  methods are never mocked away (`_helpers.py:8-12`). New `fake_ollama_show(...)` follows this shape
  for `supports_tool_calling`.
- **The vision-flag rule** (`modules/rag/tests/_helpers.py:9-38`, enforced by
  `modules/rag/tests/test_flag_hygiene.py`): any test that overrides `FARABUNKER_FEATURES` with a
  literal `frozenset(...)` *and* does an HTTP request or `reverse()` must keep `"vision"` in the
  set. Chat tests do HTTP and `reverse()` constantly, so this applies to every one of them.

### 11.2 New doubles

- **`FakeToolLLM`** — the LLM seam double. Exposes `chat(messages, tools=None) -> ChatResponse` and
  `get_tool_calls_from_response(response, error_on_no_tool_call=False) -> list`, scripted with an
  ordered list of turns: `("tool", "rag.search", {...})` or `("final", "text")`. Patched in at
  `models.contracts.gateway.get_llm_for`, which is where `docs/DEV.md:181-186` says every test
  already mocks the gateway.
- **`StubTool`** — registers a `ToolSpec` whose runner is a module-level function recording its
  calls. Registered inside a fixture that snapshots and restores `_TOOLS`, the same shape as
  `modules/vision/tests/_helpers.py:25::clear_bindings` and `:99::reset_engine_caches`.
- **`make_agent(**overrides)` / `make_conversation(...)` / `make_turn(...)`** — row builders,
  prefixed `make_` per convention.

### 11.3 What must be tested

**Tool contract:** `ToolSpec.__post_init__` rejects a `"file"` param, a blank runner, a spaced key;
a key containing `"__"` is rejected at definition time;
`register_tool` is idempotent; `get_tool` raises naming the key; `grantable_tools` excludes every
`mutates=True` spec; `openai_tool_dict` emits `{"type": "array", "items": {...}}` for a `multiple`
param and never consults `describer` (ruling R3); `describe_tool` emits one key per field (a new field without a serialization
decision fails); `openai_tool_dict` produces valid JSON Schema, omits `enum` for an engine-owned
choice, and round-trips `wire_name`/`key_from_wire_name`; `validate_tool_args` raises `ParamError`
with per-arg reasons.

**Loop:** a no-tool agent produces one LLM call and one assistant turn; a one-tool path produces
LLM → tool → LLM and three turn rows; **a response carrying three tool calls runs exactly one
(`calls[0]`), spends exactly one step, and records the other two under the tool turn's
`tool_call["discarded"]`** (M4 — plain `chat()` does not cap, so this is our policy and must be
pinned); a past `TOOL` turn replays as an assistant `ToolCallBlock` message plus a `MessageRole.TOOL`
message carrying the result text, with the **wire** name in `tool_name`; budget exhaustion produces the honest final and spends
exactly `max_steps`; deadline expiry ends the turn; a tool raising produces a tool turn plus exactly
one retry, and a second failure ends the turn; an unknown wire name is a tool error not a crash;
`agent.delegate` at `MAX_AGENT_DEPTH` is absent from the delegate's tool list; a delegation cycle
terminates and spends from the shared budget.

**Planner:** `plan_turn` unions the roles of the granted tools plus the chat role; drops a tool whose
role will not resolve; returns `exclusive=True`; a flow-mode payload declares no chat role.

**Job wiring:** `run_turn` raising writes `Turn(state=FAILED, error=str(exc))` **itself** and then
re-raises, and `on_turn_terminal` is proven **not** to fire on that path (the `handler_started` gate,
`console/jobs/worker.py:618,623,670`); `on_turn_terminal` flips exactly the queued/running turns on
the three never-started paths and leaves a done turn alone; the summarizer never touches the database (`console/jobs/views.py::_summarize`'s never-500
philosophy).

**Chat surface:** 400 on blank text; 503 on unbound role, on tool-calling-unsupported, and on
`QueueUnavailable`; 202 body shape; `turn_status` returns 200 for every readable state and 404/503
only in the two named cases; the non-XHR path renders the whole page, not a bare fragment (the
counterpart of `modules/vision/views.py:836`'s named test); a tool card renders an `output:` artifact
as an `<img>` pointing at `vision-output-file` and a citation pointing at `rag-document-file`.

**Resident sync:** `sync_resident_agents` is idempotent across two runs; it does **not** raise when
a declared `tool_keys` entry is unregistered (vision flag off, or `flow.run` before P3), writes the
key verbatim, and returns it under `unregistered_keys`; the prompt builder then drops that key
(§6.1). A test asserts every key in `RESIDENT_AGENTS` is either registered under the full flag set
or explicitly listed as a known-later key — so a typo still fails, while a legal gap does not.

**Flows:** `resolve_ref` handles `$input.*`, `$steps.N.text`, `$steps.N.data.<path>` with list
indexing, and raises naming the reference when it does not resolve; a flow turn builds no LLM;
`Flow.save()` rejects an unknown tool and a forward reference.

**Structural guards (new, permanent):**
- every registered `JobKind`'s `planner`/`handler`/`summarizer`/`on_terminal` and every `RoleSpec`'s
  `rematerialize` and every `ToolSpec`'s `runner` resolves through `resolve_dotted_path` — the guard
  that would have caught §3.6.2's lazy-failure class;
- no tool runner blocks on a queue job (§6.3);
- the existing no-global-`Settings` guard (`modules/rag/tests/test_retrieval.py:1046-1049`) is
  extended to the new columns.

### 11.4 How to run it

Per `docs/DEV.md:213-266`, unchanged except that P0 fixes `testpaths`:

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_<yours>'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision' .venv/bin/pytest -q
.venv/bin/pytest -q                                        # configured order
.venv/bin/pytest -q foundation agents models tools scripts # reversed
```

Never a bare `test_farabunker` (`DEV.md:219-224`). Both flag states, both collection orders. After
P0, `pytest -q core` disappears because `core/` does — its 29 tests join the default run via the new
`testpaths`. Drop `agents` from the reversed-order command until **P1** creates that directory
(§3.6.3) — from P1 onward it is present and must be listed.

Restart rules bite this work hard: `docs/DEV.md:279-290` — "a change to ingest, queue, or job-kind
code does not reach them until you restart them" (`docker compose restart watcher worker`).
`agent.turn` is job-kind code. Every one of P1–P4's smoke tests must restart the worker first.

---

## 12. Phasing

Five PRs. Each is independently mergeable, independently deployable, and independently green.

> **Execution is deferred for budget. This is a plan only.** Nothing in P0–P4 is scheduled by this
> document. The phase order, the gates, and the per-phase content below are the record of *how* the
> work would be done when it is funded — not a commitment that it starts. Two consequences: (a) the
> `HEAD = eb75b1d` citations in this spec go stale as the tree moves, so re-verify every `file:line`
> before executing any phase; (b) the ROADMAP items in §12.5 are recorded as **open**, not
> in-progress.

### 12.1 P0 — the regroup (pure move)

**Content:** the `git mv`s in §3.2; the 594 import rewrites (§3.6.1); the 35 dotted-path literal
edits (§3.6.2); the prose/comment/config sweep (§3.6.5); the `TEMPLATES["DIRS"]` change; the
`pytest.ini` `testpaths` change; the three silent filesystem-path literals; the ~1116 test patch
targets; the inlining of `choose_canonical` into
`modules/rag/migrations/0006_merge_duplicate_categories.py`; new `README.md` stubs at
`tools/`, `models/`, `agents/`, `foundation/` stating the import law (§3.3); the new
dotted-path-resolution guard test (§11.3).

**Gates, all required:**

1. **Every path in `git diff -M --stat` is a detected rename**, and every content delta is confined
   to (a) import lines (§3.6.1), (b) the enumerated dotted-path literals (§3.6.2), (c) the four
   filesystem-path literals (§3.6.3), (d) `mock.patch` targets (§3.6.4), (e) the prose/comment/config
   sweep (§3.6.5), and (f) the one migration inlining. A hunk that is none of those is scope creep
   and comes out. This is **not** "100% renames" — 594 import lines and ~1116 patch targets change.
2. `grep -rn "from \(core\|console\|modules\)\." --include="*.py" .` returns **empty**, and so
   does the same grep for `import core.` / `import console.` / `import modules.`.
3. `manage.py makemigrations --check --dry-run` exits 0 — **no migrations in this PR.**
4. `manage.py collectstatic --noinput --dry-run` runs — catches a stdlib-shadowing regression
   (§2.1).
5. `manage.py migrate --plan` against a restored production backup shows zero operations.
6. Full suite green in both flag states and both collection orders (§11.4).
7. The three silent guards proven still-guarding by a deliberate red run (§3.6.3).
8. Rung 2 preview stack, then Rung 3 live, then Rung 4 fresh pixels (`docs/DEV.md:190-324`). No
   success language before Rung 4.

**VISION-OWNED:** the `modules/vision` → `tools/vision` move, per the owner's decision. See §2.2
for the recommended alternative and the sequencing if the split is kept.

**This PR alone is a PR.** Nothing else lands with it.

### 12.2 P1 — the tool contract and the v1 tools

**Content:** `agents/contracts/{tools,toolschema,artifacts}.py`; the `HasParams` widening of
`validate_params` (§4.4); `InferenceEngine.supports_tool_calling` plus the `OllamaEngine`
implementation (§4.7); `tools/rag/tools.py` (`rag.search`, `rag.ask`, `rag.ingest`);
`models/registry/tools.py` (`models.status`); registration in each `AppConfig.ready()`; and
**`pytest.ini`'s `testpaths` gains `agents`** — P1 is the phase that first creates that directory
(`agents/contracts/`, `agents/contracts/tests/`), so its tests are otherwise not collected at all.

**VISION-OWNED:** `tools/vision/tools.py` (`vision.operations`, `vision.generate`) and the
`ToolResult` shaping of `services.job_json` output.

**No describer is implemented in P1, by anyone.** Per ruling R3 the `describer` field is inert: P1
ships the field declaration on `ToolSpec` and its docstring, and nothing else. There is no vision
describer, no `supported`/`unsupported_reason` computation, and no caller. Writing one is
R2/Phase 1.55 work (§12.5).

**Gates:** every tool's runner resolves; every tool round-trips through `openai_tool_dict`; each
tool's runner calls the existing service function (asserted by patching that function and checking
the call); suite green in both flag states; no new UI, so Rung 3 is "the suite plus `/setup/` still
renders".

### 12.3 P2 — agent data, the turn runtime, resident agents

**Content:** `agents/runtime` app (models, migrations, `apps.py` registering `chat.converse` and
`agent.turn`, `jobs.py`, `loop.py`, `services.py`, `resident.py`, `sync_agents`); the
`agent.delegate` tool; the `ChatSession`/`ChatMessage` retirement (§7.6) including its migration and
the `answer_question` signature change.

**Gates:** `agent.turn` runs end to end from a shell (`manage.py shell` → enqueue → `run_jobs`);
`on_turn_terminal` proven against a cancel and against a double-orphan; the retirement migration
applies and reverses cleanly on a restored backup; suite green both ways; worker restarted before
any smoke test.

### 12.4 P3 — `/chat/` and flows

**Content:** `agents/chat` app (views, urls, templates), the `_shell.html` nav link, the
`config/urls.py` mount; `Flow` model + migration, `agents/runtime/flows.py`, the `flow.run` tool,
flow-mode `agent.turn`.

**Gates:** the full ladder to Rung 4 — a real conversation at `:8000` that asks a question, gets
citations, asks for an image, and gets a thumbnail; the no-JS path works (submit → redirect →
refresh shows the answer); every error path in §10.1 reproduced in a browser.

### 12.5 P4 — ADR 0015 and the doc sweep

**Content:** `docs/adr/0015-<slug>.md` following the house shape verified across 0010/0012/0013/0014
— `# ADR NNNN — Title`, `**Status:** Accepted`, `**Date:**`, `## Context`, `## Decision` split into
numbered `### N. <assertive claim>` subsections, `## Consequences`, a named-gaps section, and
`## See also`. Plus:

- an **amendment appended to ADR 0010** covering: the `session_id` retirement (§2.3), the import-law
  restatement (§3.3), and the `platform/` naming decision being re-confirmed rather than reversed;
- an **amendment appended to ADR 0013** stating the new inline-tools rule (§6.3), since it is a new
  constraint on the queue's contract and ADR 0013:406-407 sets the precedent of recording cross-ADR
  amendments on both sides;
- `docs/ROADMAP.md` Phase 1.6 rewritten: `modules/chat` → `agents/`, and the ChatSession reuse
  bullet (`:245-250`) replaced;
- **`docs/ROADMAP.md` Phase 1.55 (`:76-157`) — two new open items**, added under the phase named
  as next after the regroup, both vision-track and both **open, not started**:
  - [ ] **R1 — full workflow-template onboarding.** Every `(family, operation)` pair a model
    genuinely supports gets a graph template in
    `models/contracts/engines/comfyui_workflows/_TEMPLATES`. Known gap today: a multi-file family
    has no `txt2img` template — `(flux2, txt2img)` is the named example — so
    `template_keys("flux2")` reports `("edit",)` and the create page offers less than the model can
    do. The registry's `(family, operation key)` keying and its variant sibling stay exactly as they
    are (§3.8); this item fills them in, it does not reshape them.
  - [ ] **R2 — one constant vision input screen.** A single create surface whose inputs enable and
    disable themselves from live per-param facts, rather than a form whose field set changes when
    the operation tab changes. The data contract it renders — `supported` /
    `unsupported_reason` / `options` per param, from a tool's or operation's `describer` — is
    designed in §4.8 of this spec; only the screen remains.
- `docs/ARCHITECTURE.md` §1/§5 rewritten for four columns; the "Modules today" list becomes "Tools
  today"; the Core services table gains an "Agent runtime" row;
- `docs/DEV.md` §7/§8 updated for the new `testpaths` and the `/chat/` smoke path;
- `README.md`, `tools/rag/README.md`, `tools/vision/README.md`, `models/registry/README.md`,
  `foundation/setup/README.md` path updates; new `agents/README.md`.

`foundation/ops/tests/test_docs_sync.py` (today `console/ops/tests/test_docs_sync.py`) exists and
will police some of this automatically; check what it asserts before writing.

---

## 13. Rejected alternatives

**Option A — five top-level columns (`rag/`, `vision/`, `models/`, `agents/`, `platform/`).**
Rejected: a new tool then adds a new top-level directory, and the fact that RAG and vision are the
*same kind of thing* disappears from the tree. `tools/` is also where the import law can be stated
once for the whole family rather than per-column. It shares deviation 2.1's `platform/` problem.

**Option B — keep `core/` and `console/`, rename `modules/` → `tools/`.** The cheapest option, and
it was seriously considered. Rejected because it leaves three things the owner's decision was
right to fix: `core/inference` stays split from the registry and queue that are its only consumers;
the execution queue — the substrate every column runs on — stays under a directory named for the
operator's console; and `core/format.py`/`core/files.py` stay in a package whose other contents are
model-specific. The owner asked for the best long-term structure over the easy one.

**A tool class hierarchy (`class Tool(ABC)` with subclasses).** Rejected: every sibling registry in
this codebase is a frozen dataclass plus a module-level dict plus dotted-path strings —
`roles.py:58-96`, `jobkinds.py:175-269`, `operations.py:79-148` — precisely so that
`AppConfig.ready()` registers without importing the implementation. `modules/vision/apps.py:51-55`
says so in a code comment. A base class makes that import mandatory at registration time.

**llama-index `FunctionTool` + `predict_and_call` as the loop.** Rejected for the three reasons in
§4.6: eager imports of every tool module at prompt-build time, pydantic as a second validation floor
beside `validate_params`, and the loop's budget/depth/recovery policy being hidden inside a library
call.

**Nested enqueue — one queue job per tool call.** Rejected: guaranteed deadlock under the shipped
sequential mode (`console/jobs/scheduler.py:374-380`), followed by an orphan sweep that fails the
turn permanently. §6.3.

**Adding `"tools"` to `CAPABILITIES` (`core/inference/roles.py:19`).** Rejected: that set is the
*role* vocabulary, validated by `RoleSpec.__post_init__` (`roles.py:71-75`) and, per ADR 0010 §3
(`0010:137-141`), it *is* the manifest vocabulary. Tool-calling is a model trait. It gets an optional
engine method instead (§4.7), which also leaves
`console/inference/tests/test_engines.py:252-261` green and untouched.

**Trusting `Ollama.metadata.is_function_calling_model`.** Rejected: hardcoded `True` with an upstream
`# TODO` at `site-packages/llama_index/llms/ollama/base.py:196`. It is a declaration, not a
detection.

**Prompt-hacked tool calling for a model that cannot do it.** Rejected outright. No `<tool>` syntax
in the system prompt, no regex over prose, no "respond in JSON" nudge. §4.7 states the two honest
behaviours.

**Keeping `ChatSession`/`ChatMessage` as the agent's memory** (as ROADMAP:245-250 plans). Rejected:
they have no reader anywhere in the repo, their only writer is unreachable from the UI
(`modules/rag/templates/rag/ask.html:464-476` never sends `session_id`), and they cannot carry a tool
call, a tool result, an artifact, or a delegation depth. `Turn` (§7.3) can. §7.6.

**`platform/` as the fourth column's name.** Rejected: already rejected once by ADR 0010 §2
(`0010:128-133`), confirmed by `sys.stdlib_module_names`, reproduced as a live import failure, and
demonstrated to break `collectstatic` via `django/contrib/staticfiles/management/commands/collectstatic.py`.
§2.1.

**Retaining `console/` for the fourth column.** Rejected: it would put rule-1 pure leaves
(`format.py`, `files.py`) behind a name that the module-boundary law forbids a feature app from
importing (`0010:121-124`, `:521-524`). §3.4.

**A per-turn token budget instead of a fixed `HISTORY_TURNS` cap.** Rejected for now: the bound
model's `context_window` is already an operator-owned operational bound
(`console/inference/models.py:77`, ADR 0010:412-435), and a token counter in the agent layer would be
a second, drifting one. Named as a deferral (§14).

**Model-generated conversation titles.** Rejected: a second, invisible model call per conversation,
outside the turn's declared plan, for a cosmetic string. The first 60 characters of the first message
are honest and free.

**`checkpoint()`-based turn resume.** Rejected: replaying half a conversation into a model after a
worker restart is worse than failing honestly. §10.4.

**An MCP server surface.** Not rejected — simply not in scope. The only prior mention is the
forward-looking phrase in `operations.describe`'s docstring (`core/inference/operations.py:157`,
quoted again at `docs/superpowers/plans/2026-08-24-vision-architecture-adjustments.md:692`), which
anticipates such a surface without committing to one. The tool contract in §4 is
shaped so an MCP adapter would be a renderer over `describe_tool`/`openai_tool_dict` plus a
transport, with no change to the registry. Any such surface must reckon with
`docs/ARCHITECTURE.md:59-65` and `:216-218` first.

---

## 14. Named gaps and out of scope

Named so they read as scoped-out rather than forgotten.

1. **No agent-builder UI.** Owner decision 3. Agents come from `resident.py` or from rows written by
   other means. A builder needs Identity & Auth first, because creating an agent is granting
   capabilities.
2. **No flow-builder UI.** Same reason. `Flow.steps` is JSON an operator edits out of band.
3. **No settings-mutating tools are grantable.** ADR 0010:266-276. They are registered (visible,
   documented, tested) and excluded from `grantable_tools()`. The gate lifts when Identity & Auth
   lands, not before.
4. **`/chat/` is unauthenticated**, like every other surface on the box. It inherits the gap ADR
   0010:214-220 and ADR 0013:289-296 already record; it does not widen it. A conversation UUID is
   unguessable, but a `Turn` pk is a sequential integer, so `chat-turn-status` has the same
   enumeration exposure `GET /rag/ask/jobs/<job_id>/` already has. Recorded, not fixed here.
5. **No streaming.** A turn is a job; a job returns a result. Token streaming would need a second
   transport and a second execution path around the queue. Deferred.
6. **No cooperative cancel of a running turn.** `POST /queue/<id>/cancel/` only cancels a *queued*
   job (`docs/ROADMAP.md:194-195`); a running turn runs to its budget or deadline. Inherited from
   ADR 0013, not introduced here.
7. **No turn resume across a worker restart.** §10.4. A drained turn is requeued by the existing
   mechanism and re-runs from the top, spending its budget again.
8. **No token-budget management.** `HISTORY_TURNS = 20` is a fixed cap. A long conversation with a
   small `context_window` will be truncated by the engine, and the platform will not pretend
   otherwise. §13.
9. **`rag.ingest` accepts no path.** Only a `document_id` of a document already in the store. A tool
   that ingests a *new* file needs a path-validation policy the platform does not have and, because
   it is `mutates=True`, an auth layer it also does not have.
10. **File uploads into a chat turn.** ADR 0012:140 already names this: "future chatbot tool call is
    JSON, so neither can carry an upload object". An agent can *reference* an existing artifact
    (`output:<id>`, `input:<id>`, `document:<id>`); it cannot receive bytes. Uploading into a
    conversation is a chat-view feature, not a tool-contract feature, and is deferred.
11. **Q1–Q14 memory governance** remains a parallel track inside `models/`, unchanged by this design
    and not designed here. The agent layer consumes the queue's admission and eviction exactly as
    every other job kind does.
12. **Whether `/api/tags` reports `capabilities` is an open question, resolvable only by a live
    probe.** `core/inference/engines/ollama.py:241-253` builds its `InstalledModel` rows from a raw
    `httpx.get(f"{endpoint}/api/tags")` and reads `model.get("capabilities", [])` off each row —
    it does not use the `ollama` SDK's typed models at all, so what those types do or do not declare
    proves nothing either way about what the server actually sends. **P1 must curl a running
    `/api/tags` and a running `/api/show` and record both shapes** before implementing
    `supports_tool_calling`. If `/api/tags` omits the field, the method reads `/api/show` and the
    existing enrichment at `ollama.py:253` is separately worth fixing. Either way this is an
    implementation detail, not a change to this design.
13. **Vision-side work is VISION-OWNED**: the `tools/vision` package move (P0, see §2.2),
    `tools/vision/tools.py`, and the `ToolResult` shaping of vision's result payloads (P1).
14. **Hosted API engines** are designed-for only (§9). The secrets vault, the posture gate, and
    credential CRUD are all out of scope, and the standing default-deny-egress rules
    (`docs/ARCHITECTURE.md:59-65`) mean a hosted adapter is forbidden in two of the three postures.
15. **R1 — workflow-template coverage** is preserved, not extended (§3.8). `(flux2, txt2img)` and
    every other missing `(family, operation)` pair stay missing through P0–P4; closing them is
    ROADMAP Phase 1.55 work, recorded as an open item in §12.5.
16. **R2 — the constant vision input screen** is not built. Only its data contract is designed
    (§4.8): `describer`, and the `supported` / `unsupported_reason` / `options` per-param keys.
    Recorded as an open item under Phase 1.55 in §12.5.
17. **Execution of every phase in §12 is deferred for budget.** This document is a plan; nothing in
    it is scheduled, and every `file:line` citation must be re-verified against the tree before any
    phase is executed.

---

## Plan review

### Round 1 — AMEND (8 MAJOR / 13 minor). Author applied all findings.

**MAJOR**

| # | Finding | Applied in |
|---|---|---|
| M1 | Move map omitted import statements entirely — the real count is **594** `from/import {core,console,modules}.…` lines (228 prod across 64 files, 366 test). P0 gate 1 ("100% renames") was therefore wrong, and the P0 size estimate was far too small. | New §3.6.1 with a per-package table; §3.6 restructured into 3.6.1–3.6.5; P0 gate 1 restated as "every path is a detected rename (`-M`); content deltas confined to imports + enumerated literals"; new gate 2 (`grep …` → empty); P0 content line and size note corrected. |
| M2 | `on_terminal` does **not** fire when the handler ran and raised — `Worker._execute` gates it on `handler_started` (`console/jobs/worker.py:618,623,670`, docstring at `:607-610`) and `JobKind.on_terminal` is contractually restricted to a job that never ran its handler (`jobkinds.py:200-204`). §10.1 claimed the opposite. | §10.1 row rewritten; new §6.2 step 8 — `run_turn` wraps its body, writes `Turn(state=FAILED, error=str(exc))` itself, then re-raises; §11.3 test added. |
| M3 | `validate_params` never calls `file_param_keys()` — the `"file"` kind is handled inline (`operations.py:326-328`). The invented `ToolSpec.file_param_keys()` and the two-member `HasParams` were both unnecessary. | Method deleted from §4.1; §4.4 rewritten — `HasParams` is `params: tuple[Param, ...]` only. |
| M4 | `force_single_tool_call` is reached only via `chat_with_tools` (`ollama base.py:63`, `:361-362`); plain `chat()` (`:400-440`) passes tool calls through uncapped and `_convert_to_ollama_messages` appends every block (`:265-286`). "Ollama is single-call by construction" was false. | §6.2 step 5 rewritten as an explicit policy: take `calls[0]`, discard the rest, record the discard in the tool turn and in the tool message; §11.3 multi-call test added. |
| M5 | Resident agents could not sync at P2: `general` grants `flow.run` (registered in P3) and the vision tools (absent when the flag is off), so strict validation would crash a legal install. | **Ruling R1, option (a)** applied: §7.1 two-stage rule — strict validation against `all_tools()` at save; tolerant drop at prompt-build time; `sync_resident_agents` never raises for a missing tool, it logs and returns `unregistered_keys`. §11.3 test added. |
| M6 | `agents/` first exists in **P1** (`agents/contracts/`), not P2, so `testpaths` and the reversed-order command were off by a phase and P1's own tests would not have been collected. | §3.6.3 `pytest.ini` row, §11.4, §11.1 (`agents/contracts/tests/`), and §12.2 P1 content all corrected to P1. |
| M7 | Import-law rule 2 named only `tools/*` as permitted to import `models.registry.bindings`, but the agent runtime and chat view need it too; and the "stays the complete set" claim was false. | §3.3 rule 2 now reads "a `tools/*` **or** `agents/*` app"; the closed-list claim replaced with the named new importers. |
| M8 | Ten wrong citations. | All corrected: `vision/services.py:92-96`→`:654-655`; `vision/models.py:187-188`→`:135-143`; `rag/views.py:1077,1088-1092`→`:1087`/`:1100`; `rag/jobs.py:249`→`:244`; `rag/models.py:325-343`→`:405`/`:444`; `rag/views.py:1233-1238`→`:1232`; `scheduler.py:378-384`→`:374-380` (all occurrences); `vision/tests/_helpers.py:117`→`:118`; `rag/models.py:288`→`:290`; URL-name cites moved from `views.py:1009/1021/715` to `vision/urls.py:23-24` and `rag/urls.py:34`. |

**minor (all 13 applied)** — prose/comment/config path sweep added as §3.6.5 with its own grep gate (`config/settings.py:51,59,64,174,184`; every `apps.py` docstring; `console/inference/views.py:291-296`; `compose.yaml:113`, `compose.preview.yaml:162`, `Dockerfile:15`; 21 `docs/` files) and folded into P0 content; `__init__.py` rows added to the §3.2 move map (5 sources, 4 new); `openai_tool_dict` now emits `{"type":"array","items":{…}}` for a `multiple` param (`operations.py:335-341`, `:456-457`); `ToolSpec.__post_init__` rejects `"__"` in a key; `rag.search` reads `score_floor`/`hybrid` from `RagSettings` as `SearchView` does (`views.py:1506,1531`) and unpacks `retrieve_nodes`' `(nodes, hybrid, index)` triple; `resolve_connection_named` unpacked as a `(ResolvedModel, str)` tuple in §6.2 step 3 and §8.3 step 2; §14 gap 12 now rests on a required live probe (`ollama.py:241-253` uses raw `httpx`, so SDK types prove nothing); counting fixes (three→five console/inference test modules, four→six vision, `core/format.py` importers seven→11 across 6, "26 dependencies entries"→~10 across 26 files, "13 live"→14 registration strings, real `console/inference` and `console/ops` file counts); the false `grep -rn "mcp" docs/` claim removed from §1.1 and §13 (it hits `docs/superpowers/plans/2026-08-24-vision-architecture-adjustments.md:692`); nine off-by-one citations corrected (`roles.py:58`, `operations.py:79`, `jobkinds.py:175`, `engines/__init__.py:34-36`, `_VARIANTS:126-128`, `variant_defaults:150`, `_TEMPLATES:54-61`, `migrations/0004:19`, `ollama base.py:196`, `:371-375`); §6.2 step 2 now specifies one explicit history shape for a past `TOOL` turn (assistant `ToolCallBlock` message + `MessageRole.TOOL` message), verified against `base.py:245-308` and `types.py:61,1125-1135`.

### Orchestrator rulings

- **R1 — resident-agent tool grants: option (a).** Validate `tool_keys` against `all_tools()` at
  save; drop unregistered or ungranted keys at prompt-build time; `sync_agents` never raises for a
  missing tool, it logs. (§7.1, §7.5, §11.3.)
- **R2 — `models.status`: KEEP, but DB-only.** It is the `models/` column's proof that it adopts the
  tool contract and traces to the owner's "tools within the system", so it stays; but it reports
  bound roles and connections from `bindings` alone and makes **no** `is_healthy` HTTP probe inside
  a turn. (§5.)
- **R3 — `describer`: inert reserved field.** Keep it declared on `ToolSpec` for R2/Phase 1.55, but
  delete the `openai_tool_dict` filtering clause and any per-prompt describer call. The LLM tool
  schema is built from static `ToolSpec.params` on every turn. (§4.6, §4.8.)

### Round 2 — AMEND (1 MAJOR / 8 minor). Author applied all.

**MAJOR — ruling R1 was recorded in the Round 1 changelog but never written into the spec body.**
Round 1's §7.1 edit did not land: the section still validated `tool_keys` against
`grantable_tools()` and rejected any unknown key, §6.1 dropped only unresolvable *roles*, and §6.2
step 4 referenced an undefined `granted_and_resolvable` while calling `get_tool`, which raises on an
absent key (§4.3). Written into all three places now: **§7.1** — `save()` validates against
`all_tools()`; a key whose registered spec is `mutates=True` is **rejected** (ADR 0010:266-276
binds); an **unregistered** key is **accepted and logged**. **§6.1** — the planner's tolerant drop
is now three drops, not one: absent from `all_tools()`, `mutates=True`, or an unresolvable role.
**§6.2 step 4** — `granted_and_resolvable` is defined as exactly that filtered list, built with a
None-returning `_registered(key)` lookup so `get_tool` is only ever called on a present key. §7.5's
"never raises" promise made consistent: it needs no bypass, because §7.1's rule already tolerates
the unregistered case, and a resident granting a *registered* mutating tool still raises at deploy —
correctly.

**minor (8 items):** (1) §4.3 — `runner` and `describer` documented as internal and deliberately
unserialized, with the one-key-per-field test asserting their absence; `describe_tool` emits no
live-facts keys in P1–P4. (2) §12.2 — the vision `describer` implementation struck from
VISION-OWNED; the inert field is the whole P1 deliverable (ruling R3). (3) §7.3 — `tool_call`'s five
keys documented: `tool`, `args`, `agent`, `id`, `discarded` (never omitted). (4) §6.2 step 2 —
`tool_call_id` annotated as reserved for a future OpenAI-compatible engine, sourced from
`Turn.tool_call["id"]`; `_convert_to_ollama_messages` never reads it
(`ollama base.py:265-286`). (5) §3.3 — `core/format.py` production import sites 11 → **12**, now
agreeing with §3.6.1's table. (6) §3.2 — three move-map rows restated on the total-`.py` convention
(`console/jobs` 22 / 6 test modules; `modules/rag` 61 / 23; `modules/vision` 37 / 10 top / 19), and
`console/jobs/management/commands/run_jobs.py` added as its own row with a note that `compose.yaml`'s
`manage.py run_jobs` is unaffected because Django resolves command *names*, not module paths.
(7) Range fixes: `ingest.py:1229-1253`→`:1229-1258`, `AskView.post` `1029-1115`→`1029-1127`,
`AskJobStatusView` `1152-1243`→`1152-1241`, never-500 rule `1155-1165`→`1157-1160`.

**(8) Not applied — reviewer error.** `MessageRole.TOOL` is at
`site-packages/llama_index/core/base/llms/types.py:**61**`, not `:60`
(`grep -n 'TOOL = ' types.py` → `61:    TOOL = "tool"`; `SYSTEM` `:56`, `USER` `:58`, `ASSISTANT`
`:59`). The spec already cited `:61` and it is left unchanged.


---

## Corrections from plan authoring (2026-08-25)

Five items adjudicated while writing `docs/superpowers/plans/2026-08-25-agents-p0-regroup.md`
and `docs/superpowers/plans/2026-08-25-agents-p1-tool-contract.md`, each verified against the
tree or a live server rather than argued. The plans implement these; this section records them so
the spec and the plans do not disagree.

1. **§3.6.3's `pytest.ini` rationale is wrong, and the conclusion is right for a different
   reason.** The section says `agents` cannot be added to `testpaths` before the directory exists
   because "pytest errors on a `testpaths` entry that does not exist". Measured on pytest 9.1.1:
   `pytest -q --collect-only -o testpaths="modules nosuchdir scripts"` **exits 0 and collects
   1736 tests**, silently ignoring the missing entry; only a path given as a *command-line
   argument* errors. The real hazard runs the other way — a **stale** entry silently stops
   collecting an entire tree while the suite still exits 0 — which is why `testpaths` must move in
   the same commit as the directory it names, and why the collected count is the only usable gate
   on such an edit. P0's incremental timeline stands; its stated reason does not.

2. **§5's `category` param must be declared `"text"`, not `"choice"`.** `validate_params` errors
   on a blank `"choice"` param whether or not it is `required`
   (`core/inference/operations.py:298-301` — `"{label} must be chosen."`), while "search every
   category" is the normal case and the documented default (ADR 0009: `None`/empty searches all
   categories). Declared as a choice, every category-less `rag.search` or `rag.ask` call would be
   a validation error. The LLM-facing schema is **byte-identical** either way — `openai_tool_dict`
   maps both kinds to `"string"` and emits no `enum` for an engine-free choice with empty
   `choices` — so nothing is lost. The runner resolves the value against `Category` rows and
   refuses an unknown name with a `ParamError` naming what exists.

3. **§4.7 and §14 gap 12 are corrected on `/api/tags`.** The spec says the SDK's typed
   `ListResponse.Model` does not declare `capabilities`, "which may always be absent", and that
   `core/inference/engines/ollama.py:253`'s `model.get("capabilities", [])` may therefore be
   reading a field that is never there. **Live probe, 2026-08-25, host Ollama at
   `localhost:11434`: `/api/tags` rows DO carry `capabilities`.** That line is not dead code and
   `list_installed` really does report mapped capabilities. `/api/show` is nonetheless **kept** as
   the source for `supports_tool_calling`, by choice rather than by necessity: it answers about
   one named model precisely, where `/api/tags` is a discovery listing whose per-row shape is not
   contractual. The cost is one extra HTTP round trip on a metadata endpoint that loads nothing.
   Same probe: a chat model reports `["completion", "tools"]`, an embedding model `["embedding"]`,
   a vision-capable chat model `["completion", "vision"]`.

4. **§4.3's registration seam is one step more eager than `JobKind`'s, and that is accepted.**
   `AppConfig.ready()` must import each app's `tools.py` to reach the `ToolSpec` constants it
   registers — where a `JobKind` is constructed inline in `apps.py` and imports nothing. The
   dotted-path `runner` indirection is **kept** because it is the contract `agents/runtime` reaches
   tools through (import-law rule 3, which is what keeps `agents/` from importing `tools/` at
   module scope), but it should be recorded honestly that for the six v1 tools it buys nothing at
   startup that the eager `tools.py` import does not already spend. What preserves the property
   that matters is the *lazy service import*: every runner imports `retrieval`/`ingest`/`services`/
   `bindings` **inside its own body**, so `ready()` keeps its no-DB-no-heavy-imports promise. P1
   enforces this with an AST guard over module-scope imports in every tool module, plus an
   anti-vacuous pin that every registered runner lives in a swept module.

5. **§5's `vision.generate` needs two behaviours the spec leaves unstated.** (a) The tool's schema
   is the **union** of params across registered operations, while `submit_job` validates against
   the **picked** operation and `validate_params` "rejects keys the operation does not declare"
   (`operations.py:276-279`). The runner must therefore narrow its arguments to
   `operation.param(key) is not None` before submitting — and must **name every dropped argument
   in the `ToolResult.text`**, never drop one silently. (b) The `image` argument maps to
   `operation.file_params()[0].key` — `file_params()` returns them in declaration order and the
   existing "use this image here" link already pre-fills the first for exactly this reason
   (`operations.py:103-111`) — resolved through `services.resolve_inputs` and passed as `files=`,
   mirroring `modules/vision/views.py:726-737`. An `image` given to an operation that declares no
   file param is a dropped argument under (a), not an error.

---

## Long-term requirement: enterprise control and MCP interop (2026-08-27)

Owner direction, recorded before P2 so the runtime is not built into a shape that has to be
unpicked later. **Nothing here is built by this section.** It states where the tool layer is
going and what P2 must therefore not foreclose.

**The intent, verbatim in substance.** The platform must work as an **enterprise tool bus**. The
same in-process registry (`agents/contracts/tools.py:219`'s `_TOOLS`, read through
`all_tools`/`get_tool`/`grantable_tools`, `tools.py:233-264`) serves **two** kinds of caller: the
platform's own agents, in-process, exactly as they do now — and, later, **external** agents
through a thin **MCP-speaking edge**: `tools/list` and `tools/call` over HTTP, mounted on the
existing Django web app. **Not a separate server. Not a protocol in the middle of an internal
call.** An internal tool call stays a Python function call through `invoke_tool`; MCP is an edge
adapter over the same registry, nothing more.

That edge is **gated behind a future Identity & Auth phase**, not shipped beside it. Auth grants
tools **per PRINCIPAL**. Principal kinds, in the order they arrive: a **resident agent**
(code-declared, `agents/resident.py`), a **user-built agent** (a row), an **external API client**
(the MCP edge's caller). Users and groups come later, as further principal kinds, not as a second
mechanism. **Tenancy follows auth**, not the reverse: visibility scopes on documents and
categories, so a principal sees a subset of the library rather than all of it.

### The six design consequences P2 must honour

1. **Grants attach to a PRINCIPAL, and one function decides availability.**
   `granted_tools(principal, tool_keys)` in `agents/contracts/tools.py` is the only place that
   answers "may this caller call this tool" — it drops an unregistered key (logged, ruling R1)
   and drops a `mutates=True` key (ADR 0010:266-276). `ToolContext` carries `principal`, a frozen
   `Principal(kind, key)` — **P2 introduces it**, replacing today's bare
   `ToolContext.agent_key` (`agents/contracts/tools.py:197`). When grants move to their own
   table, the `tool_keys` argument disappears and the principal alone decides; the call site and
   the return type do not change. `Principal` stays Django-free, like everything else in that
   rule-1 pure leaf.

2. **Retrieval keeps exactly ONE filter point.** `tools.rag.retrieval.retrieve_nodes`
   (`tools/rag/retrieval.py:422-428`) is where the category filter is resolved and the retriever
   is built, for **both** callers — `answer_question` (`retrieval.py:539`) and `rag.search`'s
   runner (`tools/rag/tools.py:214`). A visibility scope is one more filter argument **there**,
   applied once, never a second copy in a tool runner. P2's runners therefore pass the principal
   *through* (in `ToolContext`) and never re-derive it; a runner that grew its own filter would
   be the drift this seam exists to prevent.

3. **The tool-invocation record is its own row.** `ToolInvocation` (principal kind, principal
   key, tool key, args, outcome class ∈ {`ok`, `refused`, `param_error`, `error`, `degraded`},
   result text, error text, started/finished timestamps) is written by `invoke_tool` for **every**
   call. A conversation `Turn` *references* it (nullable FK); it is not a column on `Turn`. An
   external MCP `tools/call` has no conversation and no turn at all, and reuses the same row
   unchanged — which is only possible if the row is not owned by the conversation table.

4. **`mutates: bool` is the seed of `scopes`.** Keep the bool as it is
   (`agents/contracts/tools.py:105`, `grantable_tools` at `:256`). Name the generalization here so
   nobody invents a parallel one: a later `ToolSpec.scopes: tuple[str, ...]` carries the
   capability vocabulary ADR 0010:258-265 already names (`inference:chat`, `vector:read`, …), and
   `mutates=True` becomes shorthand for "declares a write scope". P2 adds no `scopes` field.

5. **`agents/contracts` stays transport-agnostic; adapters live beside it.**
   `agents/contracts/toolschema.py` renders a `ToolSpec` to a wire schema and knows nothing about
   who reads it. `openai_tool_dict` (`toolschema.py:53`) is one adapter; **`mcp_tool_dict` is the
   second, and it is a P2 deliverable** (below). The MCP *edge* — the HTTP views, the session
   handling, the auth check — is a later phase and lives outside `agents/contracts` entirely.

6. **Roadmap phases after P4**, in order, each depending on the one before:
   **Identity & Auth** (principals, grants, the `/inference/` mutation surface closed) →
   **MCP edge** (export: `tools/list` + `tools/call` over HTTP on the existing app; import: this
   platform as an MCP *client* of other servers, with a per-server allowlist of which of their
   tools may be registered) → **tenancy** (visibility scopes on documents and categories,
   applied at consequence 2's single filter point).

### P2 deliverable: `mcp_tool_dict(spec)`

Beside `openai_tool_dict`, in `agents/contracts/toolschema.py`:

```python
def mcp_tool_dict(spec: ToolSpec) -> dict:
    """`spec` as an MCP `tools/list` entry: `name`, `description`,
    `inputSchema`.

    The SAME JSON Schema object `openai_tool_dict` puts under
    `function.parameters`, under MCP's own key name. Two adapters, one
    schema builder -- the shape a tool declares must not depend on who is
    asking, or an external caller and an internal prompt disagree about
    what a tool accepts and the disagreement surfaces as a validation
    error nobody can reproduce.
    """
```

`name` is `wire_name(spec.key)` (`toolschema.py:34`) for the same reason `openai_tool_dict` uses
it: the function-name grammar admits no `.`. Nothing consults `spec.describer` (ruling R3), and
`runner` is not serialized — implementation, never contract (`describe_tool`'s own rule,
`tools.py:278-283`).

**Pinned by a drift test**, not by convention: for every registered spec,
`mcp_tool_dict(spec)["inputSchema"]` equals `openai_tool_dict(spec)["function"]["parameters"]` —
same `properties`, same `required`, same order — and the two names and descriptions agree. A
schema builder that diverges fails that test, which is the whole point of writing the second
adapter now, while there is one builder to share, rather than after an edge exists to drift from.

