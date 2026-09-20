# Agents P0 — Physical Regroup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every Python package in the repository into the four columns the agent layer requires — `tools/{rag,vision}`, `models/{contracts,registry,queue}`, `agents/` (skeleton only), `foundation/{format,files,ops,setup,templates,tests}` — so that `core/`, `console/`, and `modules/` cease to exist, while every Django app label, every database table name, every index name, every URL name, and every row in `django_migrations` stays byte-identical. This PR contains zero migrations and zero behaviour change.

**Architecture:** Thirteen strictly sequential tasks. Task 1 writes the two permanent guards the move is pinned against (app labels + registered dotted-path resolution) *before* anything moves, so they are proven green on the pre-move tree and must stay green through every later task. Tasks 2–9 move one package per task: each task is a `git mv` plus a mechanical two-pass rewrite (dotted prefix, then slash prefix) of every tracked non-`docs/` file that mentions that package, plus the `config/settings.py` / `config/urls.py` / `pytest.ini` edits that package's own move requires — so the suite is green at the end of every single task, never only at the end of the sequence. Task 10 moves the shared shell template and creates the four column `__init__.py`/`README.md` files. Task 11 narrows and red-run-proves the three filesystem-path literals that fail *silently*. Task 12 sweeps the prose/comment/deploy references the mechanical passes could not reach and installs the two permanent grep gates. Task 13 is the full gate matrix and the deploy ladder.

**Tech Stack:** Python 3.12/3.13, Django 5, pytest + pytest-django, PostgreSQL (pgvector) on the branch preview port 5433, BSD `sed` (macOS — `sed -i ''`), `git mv -k`, `git ls-files` pathspec magic. No new Python dependencies, no new migrations, no image rebuild.

**Spec:** docs/superpowers/specs/2026-08-25-agents-and-tools-design.md

## Global Constraints

- **Orchestrator gates — the full matrix, every task, no exceptions.** Two `FARABUNKER_FEATURES` states × two collection orders, plus the out-of-testpaths run while `core/` still exists:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q                                  # configured collection order
  .venv/bin/pytest -q scripts console modules          # reversed — Tasks 1-2 only
  .venv/bin/pytest -q core/tests                       # Tasks 1-2 only; core/ still exists
  ```

  From Task 3 onward `core/tests` no longer exists — it is `foundation/tests`, and it is *inside* `testpaths`, so `pytest -q` covers it and the separate invocation retires. The reversed-order command tracks the moves; its final form (Task 9 onward) is `.venv/bin/pytest -q scripts foundation models tools`, the post-move reversal of today's `scripts console modules`. Each task below names the exact reversed-order command valid at that task.
- **Private test DB per role. Never `test_farabunker`.** Implementers use `farabunker_impl` on the branch preview Postgres at **5433** (`docs/DEV.md:213-232`). Two sessions pointed at the same test database race each other's create/drop lifecycle. Never point a run at a bare `test_farabunker`, and never at `5432` (that is the app's database, not a test database).
- **No `conftest.py`, anywhere.** `find . -name conftest.py` returns nothing today and must keep returning nothing. Shared test helpers live in a per-package `_helpers.py` (`modules/rag/tests/_helpers.py:1-7`, `modules/vision/tests/_helpers.py:1-7`, `console/inference/tests/_helpers.py:1-7` all say so in their own docstrings). Autouse fixtures stay *defined* per test module and delegate their bodies to `_helpers`. Helpers are duplicated per app, never imported across apps.
- **THE VISION-FLAG RULE** (`modules/rag/tests/_helpers.py:9-38`, enforced by `modules/rag/tests/test_flag_hygiene.py`): any test that overrides `settings.FARABUNKER_FEATURES` (directly, via the `settings` fixture, or via `override_settings`) **AND** performs an actual HTTP request or URL resolution in that same test (Django's test `Client`, `reverse()`) **MUST** keep `"vision"` in the overridden set — even when the test's own point is about `"media"` or an empty set. `config/urls.py:19-20` builds its `vision/` mount conditionally, once, at import time; Django resolves the URLconf lazily on the first request/`reverse()` in the process and never re-evaluates it. A test that overrides the flag without ever touching `Client`/`reverse()` is not at risk and may use whatever minimal set proves its point. None of the tests this plan writes override the flag at all, but the guard that *enforces* the rule is edited in Task 11 and must be red-run-proven there.
- **Never-500.** No task in this plan may turn a degraded state into an exception out of a view. The regroup changes no view logic; if a hunk appears to, it is scope creep and comes out.
- **No model names or versions.** Not in code, not in a comment, not in a docstring, not in a test name, not in this plan. The repository is going public and ADR 0010's third amendment (`docs/adr/0010-model-management-framework.md:290-380`) forbids the platform from naming a model for the operator.
- **ADR import law, post-regroup (spec §3.3) — three rules, and they are what this move exists to make statable:**
  - **Rule 1 — pure leaves are universally importable.** `foundation/format.py`, `foundation/files.py`, everything under `models/contracts/`, and everything under `agents/contracts/` are pure: no Django models, no Django views, no database access, no import of any non-pure module. Any column may import them, in any direction.
  - **Rule 2 — Django apps are column-private.** `tools/rag`, `tools/vision`, `models/registry`, `models/queue`, `agents/runtime`, `agents/chat`, `foundation/ops`, `foundation/setup` are not importable across a column boundary, with exactly one exception: a `tools/*` **or** `agents/*` app MAY import `models.registry.bindings`, and only that module. `models.registry.models` and `models.registry.views` stay off-limits to every column, with no exception. (Carried over in substance from ADR 0010:521-524.)
  - **Rule 3 — cross-column *work* goes through a seam, never an import.** Three seams: the queue (`models.contracts.queue.enqueue`/`get_job`), the gateway (`models.contracts.gateway.get_llm*`/`get_embed_model*`/`get_image_generator*`/`get_transcriber*`), and the tool registry (P1).
  - **Corollary this plan must not violate:** `models/contracts/**` imports nothing from `models/registry`, `models/queue`, `tools/*`, `agents/*`, or `foundation/ops`/`foundation/setup`. It may import `foundation/format.py` and `foundation/files.py`. It does not today and must not start to.
- **Never `import models`.** Every Django `models.py` in the repo opens `from django.db import models`; files under `models/` also carry absolute imports like `from models.contracts.roles import CAPABILITIES`. Those two never conflict (`from X.Y import Z` resolves through `sys.modules`, never through a module-level name), but a bare `import models` followed by `models.contracts.…` **would** be shadowed. No such import exists today; none may be created. Stated in `models/README.md` and in `models/__init__.py`'s docstring (Task 10).
- **Tests and docs ship with every task** (ADR 0008, `docs/adr/0008-engineering-standards.md:15-30`). For this plan the "tests" are, in the main, the *existing* 2704-test suite plus the grep gates plus the two guards Task 1 writes. Every task's docs obligation is the in-tree prose the mechanical passes rewrite: package `README.md`s, module docstrings, `AppConfig` class docstrings, `config/settings.py` comments. `docs/**` is **P4's** sweep and is deliberately excluded from every `git ls-files` pathspec below (`':!docs/'`).
- **P0 is a pure rename, with exactly ONE deliberate content exception.** Every content delta must be classifiable as (a) an import line, (b) an enumerated dotted-path literal, (c) a filesystem-path literal, (d) a `mock.patch` target, (e) prose/comment/config path text, or (f) the inlining of `choose_canonical` into `modules/rag/migrations/0006_merge_duplicate_categories.py` (Task 8). A hunk that is none of those is scope creep and comes out.
- **Zero migrations.** `manage.py makemigrations --check --dry-run` must exit 0 on every task. If a step appears to need a migration, STOP and report — it means an app label moved, which is the one thing this plan exists to prevent.
- **Baseline, measured at HEAD `3f60776` on 2026-08-25:** `pytest -q --collect-only` collects **2704** tests under today's `testpaths = modules console scripts`; `pytest --collect-only core` collects **29** more. Post-Task-3 the default run collects **2740** (2704 + Task 1's 7 + the 29 that join when `foundation` enters `testpaths`). Use those as the floor: a task that lowers a collected count has silently stopped collecting something.
- **Branch-only. No commits to `main`, no merges to `main`, no deploy from Tasks 1-12.** The live stack bind-mounts the `main` checkout; leaving it conflicted breaks production. Task 13 owns the deploy ladder.
- **Commit trailers.** Every commit ends with:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```
- **Verification doctrine.** No "done"/"works"/"fixed"/"complete" language about the regroup until fresh pixels have been seen on the owner's live system (Task 13, Rung 4). Every earlier green run is evidence-gathering, not a completion claim.

### The mechanical rewrite idiom (used by Tasks 2-9, verbatim)

Every move task rewrites its own package's prefix in two passes — dotted (imports, `mock.patch` targets, `AppConfig.name`, `INSTALLED_APPS`, `include()` strings, docstrings) then slash (filesystem-path literals, prose, deploy-file comments):

```bash
# pass 1 -- dotted
git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'OLD\.DOTTED' | xargs sed -i '' 's/OLD\.DOTTED/NEW.DOTTED/g'
# pass 2 -- slash
git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'OLD/SLASH'  | xargs sed -i '' 's|OLD/SLASH|NEW/SLASH|g'
```

Four properties make this safe, and all four are load-bearing:

1. **`':!docs/' ':!.superpowers/'`** keeps two classes of frozen record out of P0's diff. `docs/` is P4's sweep. `.superpowers/` holds six tracked owner-requirement and peer-handoff documents, **four of which carry old paths** (`comfyui-memory-seams-contract.md`, `comfyui-memory-seams-plan-summary.md`, `memory-governance.md`, `peer-handoff-worker-token-race.md`) — they are the owner's own words and another session's handoff, recorded at a moment in time, and rewriting them would falsify a record exactly as rewriting an ADR quotation would. Verify before the first sed: `git ls-files .superpowers | wc -l` → 6.
2. **`grep -I`** skips binaries; `grep -l` means `sed` only opens files that actually match, so an empty match list runs `sed` zero times (verified on this machine: `printf '' | xargs -0 cmd` does not execute `cmd`).
3. **`sed -i ''`** is the BSD/macOS in-place form. On GNU `sed` it is `sed -i` with no argument — check `sed --version` before running if the executing machine is not macOS.
4. **Every rewrite is verified by a zero-hit grep immediately afterwards.** The per-task step lists the exact expected occurrence count *before* the rewrite (measured at `3f60776`) so a wrong count is caught before the commit, not after.

### Per-package old → new prefix table (measured at HEAD `3f60776`, excluding `docs/` and `.superpowers/`)

| Task | Old dotted | New dotted | occ / files | Old slash | New slash | occ / files |
|---|---|---|---|---|---|---|
| 2 | `core.inference` | `models.contracts` | 748 / 116 | `core/inference` | `models/contracts` | 52 / 30 |
| 3 | `core.format` | `foundation.format` | 36 / 18 | `core/format` | `foundation/format` | 5 / 5 |
| 3 | `core.files` | `foundation.files` | 10 / 9 | `core/files` | `foundation/files` | 2 / 2 |
| 3 | *(none)* | — | 0 / 0 | `core/tests` | `foundation/tests` | 2 / 2 |
| 4 | `console.inference` | `models.registry` | 464 / 68 | `console/inference` | `models/registry` | 59 / 38 |
| 5 | `console.jobs` | `models.queue` | 181 / 45 | `console/jobs` | `models/queue` | 72 / 33 |
| 6 | `console.ops` | `foundation.ops` | 27 / 10 | `console/ops` | `foundation/ops` | 8 / 6 |
| 7 | `console.setup` | `foundation.setup` | 7 / 7 | `console/setup` | `foundation/setup` | 4 / 3 |
| 8 | `modules.rag` | `tools.rag` | 1184 / 88 | `modules/rag` | `tools/rag` | 129 / 70 |
| 9 | `modules.vision` | `tools.vision` | 162 / 44 | `modules/vision` | `tools/vision` | 38 / 22 |
| 9 | *(none)* | — | 0 / 0 | `modules/home` | `tools/home` | 1 / 1 |

Cross-checks against the spec's own counts, both confirmed at `3f60776`:
`grep -rn "^\s*from \(core\|console\|modules\)\.\|^\s*import \(core\|console\|modules\)\." --include="*.py" . | wc -l` → **594** import lines;
`grep -rhoE 'patch\(\s*"(core|console|modules)\.' --include="*.py" . | wc -l` → **1116** `mock.patch` targets.

### `pytest.ini` `testpaths` timeline

`testpaths` is a **silent** failure class in *both* directions, and the direction matters for how this plan sequences its edits.

**Measured on this machine, pytest 9.1.1, at HEAD `3f60776`:** a `testpaths` entry naming a directory that does not exist is **silently ignored** — `pytest -q --collect-only -o testpaths="modules nosuchdir scripts"` exits 0 and collects 1736 tests from the two real roots, with no warning. Only a path given as a **command-line argument** errors (`pytest -q --collect-only modules nosuchdir scripts` → collects nothing). So the two rules are:

- A `testpaths` entry may be added **before** its directory exists without breaking anything — but it buys nothing and hides a typo, so this plan still adds each root in the task that creates it.
- A `testpaths` entry that goes **stale** — the directory moved out from under it — silently stops collecting an entire tree, and the suite still exits 0. **That** is the failure this plan's incremental timeline exists to prevent, and it is why `testpaths` moves in the same commit as the directory, never in one late edit.
- **Because both failure modes are silent, the collected count is the only gate.** Every task that edits `testpaths` must run `pytest -q --collect-only | tail -1` and compare against the number the previous task recorded. A count that drops is a tree that stopped being collected.

The timeline:

| After task | `testpaths =` |
|---|---|
| (today) | `modules console scripts` |
| 2 | `modules console models scripts` |
| 3 | `modules console models foundation scripts` |
| 7 | `modules models foundation scripts` |
| 8 | `modules tools models foundation scripts` |
| 9 | `tools models foundation scripts` |

`agents` is **not** added in P0 even though Task 10 creates `agents/__init__.py`: there are no tests under `agents/` until P1 creates `agents/contracts/tests/`, and P1 §12.2 owns that edit.

---

### Task 1: Pin the move — app-label and dotted-path resolution guards

Two permanent structural guards, written and proven green against the **pre-move** tree. They are the pins every later task is checked against: Task 1's tests must pass unchanged (modulo their own import prefixes) at the end of Tasks 2-13.

**Files**
- Create: `console/ops/tests/test_app_labels.py` (moves to `foundation/ops/tests/` in Task 6)
- Create: `console/inference/tests/test_registry_paths.py` (moves to `models/registry/tests/` in Task 4)
- Test: both files ARE the tests.

**Interfaces**
- Consumes: `django.apps.apps.get_app_configs()`, `django.apps.apps.get_models()`, `django.core.management.call_command`; `core.inference.jobkinds.all_job_kinds` (`core/inference/jobkinds.py:253`), `core.inference.jobkinds.resolve_dotted_path` (`jobkinds.py:273`), `core.inference.roles.all_roles` (`core/inference/roles.py:90`).
- Produces: no importable API — test modules only.

**Why `console/ops/tests/` for the label guard:** `console.ops` is the one console app that already reaches across every other app's data by design (`console/ops/backup.py:24,56-66` hardcodes seven `f"{app_label}_{model_name.lower()}"` table names rather than importing the models) and it already hosts a cross-cutting structural test (`console/ops/tests/test_docs_sync.py`, a pure file-text comparison). A repo-wide app-label pin belongs beside it.

**Why `console/inference/tests/` for the path guard:** five modules in that package already test `core/inference` code rather than console code (`test_roles.py`, `test_jobkinds.py`, `test_queue_seam.py`, `test_engines.py`, `test_catalog.py`) precisely because `pytest.ini:4` excludes `core/`. This guard is the same shape and rides along into `models/registry/tests/`.

**Steps**

- [ ] Write the failing app-label guard. Create `console/ops/tests/test_app_labels.py`:

  ```python
  """The regroup's DB-free pin (spec section 3.5).

  Renaming a package changes `AppConfig.name`; it must change NOTHING
  else. Django derives a label from the module path ONLY when the
  subclass has not set one (`django/apps/config.py:34-35`), and every
  AppConfig in this project sets `label` explicitly -- so table names
  (`f"{label}_{model}"`, no `Meta.db_table` anywhere in the repo),
  `django_content_type` rows, and `django_migrations` rows (keyed
  `(app_label, name)`) are all untouched by a package move. This module
  is the thing that actually enforces that, rather than trusting the
  comment next to each `label = "..."` line.

  No `conftest.py` (the repo forbids them anywhere) and no
  `FARABUNKER_FEATURES` override: the app registry is populated at
  startup from whatever flags the run was launched with, and this file
  asserts against the vision-enabled set both supported gate states share.
  """
  from __future__ import annotations

  from io import StringIO

  import pytest
  from django.apps import apps
  from django.core.management import call_command

  # Every label this project owns, frozen. A move that changes one of
  # these renames a database table; that is a migration, and P0 has none.
  EXPECTED_LABELS = frozenset({"rag", "vision", "inference", "jobs", "setup", "ops"})


  def _project_app_configs():
      """Every installed AppConfig this repository authored -- Django's own
      contrib apps and the third-party `rest_framework` excluded by name,
      since their labels are not ours to pin."""
      return [
          cfg
          for cfg in apps.get_app_configs()
          if not cfg.name.startswith("django.") and cfg.name != "rest_framework"
      ]


  def test_every_project_app_label_is_exactly_what_it_was_before_the_regroup():
      assert {cfg.label for cfg in _project_app_configs()} == EXPECTED_LABELS


  def test_every_app_config_sets_its_label_explicitly_on_the_subclass():
      """The mechanism, not just the outcome: if a subclass stopped setting
      `label`, Django would start deriving it from `name` -- and the very
      next package move would silently rename a table."""
      for cfg in _project_app_configs():
          assert "label" in type(cfg).__dict__, (
              f"{cfg.name} does not set `label` on its AppConfig subclass; "
              f"Django would derive it from the module path."
          )


  def test_every_table_name_stays_label_derived():
      """No model sets `Meta.db_table` (grep: zero occurrences), so every
      table is `f"{app_label}_{model_name}"`. Pinning the derivation is
      what proves `inference_modelconnection` does not become
      `registry_modelconnection` when `console/inference` moves."""
      for model in apps.get_models():
          meta = model._meta
          if meta.app_label not in EXPECTED_LABELS:
              continue
          assert meta.db_table == f"{meta.app_label}_{meta.model_name}"


  @pytest.mark.django_db
  def test_makemigrations_check_reports_no_missing_migrations():
      """P0 contains zero migrations. `--check` exits non-zero (SystemExit)
      when Django wants one, so an un-raised call IS the assertion."""
      out = StringIO()
      try:
          call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
      except SystemExit:
          pytest.fail("makemigrations --check found unmade migrations:\n" + out.getvalue())


  @pytest.mark.django_db
  def test_showmigrations_reports_every_migration_applied():
      """`django_migrations` needs no new rows: every migration the test
      database was built with is applied, and a package move adds none."""
      out = StringIO()
      call_command("showmigrations", "--list", stdout=out)
      unapplied = [
          line.strip()
          for line in out.getvalue().splitlines()
          if line.strip().startswith("[ ]")
      ]
      assert unapplied == []
  ```

- [ ] **Preflight the database before running anything.** The two `@pytest.mark.django_db` tests below create a test database, which needs the branch preview Postgres up *and* a reachable `postgres` maintenance database. Verify both first — a run that fails here fails with a connection error that looks nothing like a test failure (measured on 2026-08-25: `psycopg.OperationalError: connection refused` after 5m46s, with the three non-DB tests passing and only the two DB-backed ones erroring):

  ```bash
  scripts/preview status   # or: docker compose -f compose.preview.yaml ps
  .venv/bin/python -c "
  import psycopg
  psycopg.connect('postgres://farabunker:farabunker@localhost:5433/postgres').close()
  print('preview postgres reachable, maintenance db present')
  "
  # EXPECTED: "preview postgres reachable, maintenance db present"
  ```

- [ ] Run it and expect **collection failure**, because the file is under `console/ops/tests/` which IS inside `testpaths` but the module does not exist yet — so first confirm the negative by running before creating the file:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q console/ops/tests/test_app_labels.py
  # EXPECTED (before the file exists):
  #   ERROR: file or directory not found: console/ops/tests/test_app_labels.py
  ```

  Then create the file and run again:

  ```bash
  .venv/bin/pytest -q console/ops/tests/test_app_labels.py
  # EXPECTED: 5 passed
  ```

- [ ] Prove the label pin actually bites (a guard that cannot fail is not a guard). Temporarily edit `console/jobs/apps.py:20` from `label = "jobs"` to `label = "queue"`, re-run, and confirm **red**:

  ```bash
  .venv/bin/pytest -q console/ops/tests/test_app_labels.py
  # EXPECTED: FAILED test_every_project_app_label_is_exactly_what_it_was_before_the_regroup
  #   assert {'inference', 'ops', 'queue', 'rag', 'setup', 'vision'} == frozenset({... 'jobs' ...})
  # ALSO EXPECTED red: test_every_table_name_stays_label_derived
  #   (jobs_inferencejob vs queue_inferencejob)
  ```

  Revert the edit (`git checkout -- console/jobs/apps.py`) and re-run to green before continuing.

- [ ] Write the dotted-path resolution guard. Create `console/inference/tests/test_registry_paths.py`:

  ```python
  """Every registered dotted-path string actually resolves (spec section 3.6.2).

  The 14 registration strings in this repo fail LAZILY: `register_role`/
  `register_job_kind` store the string without importing it, and
  `core.inference.jobkinds.resolve_dotted_path` (a thin wrapper over
  Django's `import_string`, `jobkinds.py:273-288`) re-imports fresh at
  execution time with no caching. A package move that misses one boots
  cleanly, renders every page, and passes every test that mocks the
  handler -- and then throws ImportError the first time a real background
  job of that kind runs, on a worker, in production.

  The existing string-EQUALITY assertions (`modules/rag/tests/
  test_apps.py:42,101-103,119-121`, `modules/vision/tests/test_apps.py`,
  `console/inference/tests/test_jobkinds.py`) prove the string is what the
  author typed. This module proves it is a string that RESOLVES. Both are
  wanted: the first catches a typo, the second catches a stale path.

  Lives in `console/inference/tests/` for the same reason `test_roles.py`,
  `test_jobkinds.py`, `test_queue_seam.py`, `test_engines.py`, and
  `test_catalog.py` do -- `pytest.ini`'s `testpaths` excludes `core/`, so
  this package is where a `core/inference` registry gets exercised.

  Reads whatever the run's own `FARABUNKER_FEATURES` registered and
  overrides nothing: this file makes no HTTP request and calls no
  `reverse()`, so the VISION-FLAG RULE does not apply to it, and asserting
  against a fixed set of kinds would make it a second, drifting copy of
  `test_apps.py`'s registration assertions.
  """
  from __future__ import annotations

  from core.inference.jobkinds import all_job_kinds, resolve_dotted_path
  from core.inference.roles import all_roles

  _JOB_KIND_PATH_FIELDS = ("planner", "handler", "summarizer", "on_terminal")


  def _registered_paths() -> list[tuple[str, str]]:
      """`(where, dotted_path)` for every registered path string. `where` is
      an operator-readable locator so a failure names the registration, not
      just the path."""
      found: list[tuple[str, str]] = []
      for kind in all_job_kinds():
          for field in _JOB_KIND_PATH_FIELDS:
              path = getattr(kind, field)
              if path:
                  found.append((f"JobKind({kind.key!r}).{field}", path))
      for role in all_roles():
          if role.rematerialize:
              found.append((f"RoleSpec({role.key!r}).rematerialize", role.rematerialize))
      return found


  def test_the_registries_are_populated_at_all():
      """Anti-vacuous-pass guard: an empty registry would make the test
      below green while proving nothing."""
      paths = _registered_paths()
      assert len(paths) >= 8, paths


  def test_every_registered_dotted_path_resolves():
      unresolved: dict[str, str] = {}
      for where, path in _registered_paths():
          try:
              resolve_dotted_path(path)
          except Exception as exc:  # noqa: BLE001 -- report every failure, not the first
              unresolved[where] = f"{path} -> {type(exc).__name__}: {exc}"
      assert unresolved == {}, (
          "registered dotted paths that do not resolve (a package moved and a "
          f"registration string did not follow it): {unresolved}"
      )
  ```

- [ ] Run it under both flag states and confirm green:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q console/inference/tests/test_registry_paths.py
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q console/inference/tests/test_registry_paths.py
  # EXPECTED, both: 2 passed
  ```

- [ ] Prove the path guard bites. Temporarily edit `modules/rag/apps.py:66` from `handler="modules.rag.jobs.run_ask"` to `handler="modules.gar.jobs.run_ask"` and re-run:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q console/inference/tests/test_registry_paths.py
  # EXPECTED: FAILED test_every_registered_dotted_path_resolves
  #   {"JobKind('rag.ask').handler": "modules.gar.jobs.run_ask -> ImportError: ..."}
  ```

  Revert (`git checkout -- modules/rag/apps.py`) and re-run to green.

- [ ] Run the full gate matrix and record the numbers:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts console modules
  .venv/bin/pytest -q core/tests
  # EXPECTED: 2711 collected in the first three (2704 baseline + 7 new); 29 in core/tests.
  ```

- [ ] Commit:

  ```bash
  git add console/ops/tests/test_app_labels.py console/inference/tests/test_registry_paths.py
  git commit -m "$(cat <<'EOF'
  test(regroup): pin app labels and registered dotted-path resolution before the move

  Two permanent structural guards, green on the pre-move tree, that the
  whole P0 regroup is checked against:

  - console/ops/tests/test_app_labels.py freezes the six app labels, the
    explicit-`label`-on-the-subclass mechanism behind them, the
    label-derived table names, `makemigrations --check`, and
    `showmigrations` having nothing unapplied. Spec section 3.5.
  - console/inference/tests/test_registry_paths.py resolves every
    registered `planner`/`handler`/`summarizer`/`on_terminal`/
    `rematerialize` string through `resolve_dotted_path`. These 14 strings
    fail LAZILY -- a missed one boots cleanly and throws on a worker.
    Spec section 3.6.2.

  Both were proven to bite by a deliberate red run (a changed label, a
  broken handler path) before being committed green.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 2: `core/inference/` → `models/contracts/`

The 21-file pure contracts package, its `engines/` adapters, and its `comfyui_workflows/` graph-template registry. The largest single rewrite in the plan (748 dotted occurrences across 116 files).

**Files**
- Create: `models/__init__.py` (new)
- Move: `core/inference/` → `models/contracts/` (21 `.py`: `roles.py`, `operations.py`, `jobkinds.py`, `queue.py`, `bindings.py`, `gateway.py`, `catalog.py`, `__init__.py`, `engines/{__init__,base,ollama,comfyui,whisper}.py`, `engines/comfyui_workflows/{__init__,_fragments,txt2img,img2img,inpaint,upscale,flux2_edit,qwen_edit}.py`)
- Move: `core/README.md` → `models/contracts/README.md`
- Modify: every tracked non-`docs/` file matching `core.inference` (116) or `core/inference` (31), mechanically
- Modify: `pytest.ini:4` — `testpaths = modules console models scripts`
- Modify (hand): `console/inference/views.py:291-296` — verify `_engine_source_path`'s worked-example docstring reads `models/contracts/engines/ollama.py` after the slash pass
- Test: existing suite; `console/inference/tests/test_views.py:5768`'s `source_path` assertion is the loud proof that `_engine_source_path` still computes a real relative path

**Interfaces**
- Consumes: nothing new.
- Produces: `models.contracts.roles`, `models.contracts.operations`, `models.contracts.jobkinds`, `models.contracts.queue`, `models.contracts.bindings`, `models.contracts.gateway`, `models.contracts.catalog`, `models.contracts.engines`, `models.contracts.engines.comfyui_workflows` — every public name, signature, and dataclass field unchanged. In particular, `_TEMPLATES`'s six `(family, operation_key)` keys and `_VARIANTS`'s one `(family, variant)` key keep their exact values, and the four public readers keep their exact signatures: `get_template(operation_key, family="")`, `template_keys(family="")`, `families()`, `variant_defaults(operation_key, family="", variant="")` (spec §3.8 / ruling R1 — any change to this keying is out of scope for every phase).

**Steps**

- [ ] Create the column package. `models/__init__.py` is not empty — it carries the §3.7 convention:

  ```bash
  mkdir -p models
  cat > models/__init__.py <<'EOF'
  """The `models/` column: model handling AND execution.

  `models/contracts/` is pure, Django-free platform contract code (the
  role/operation/job-kind/engine registries, the queue seam, the gateway).
  `models/registry/` and `models/queue/` are the Django apps that store and
  drive them.

  CONVENTION, and there is no exception to it anywhere in this codebase:
  never write a bare `import models`. Always `from models.<sub> import ...`.
  Every Django `models.py` in the repo opens `from django.db import models`;
  the two forms never conflict because `from X.Y import Z` resolves through
  `sys.modules` and never through a module-level name -- but a bare
  `import models` followed by `models.contracts....` WOULD be shadowed by
  that local name. See the spec's section 3.7.
  """
  EOF
  ```

- [ ] Move the package and its README with `git mv` so every path is a detected rename:

  ```bash
  git mv core/inference models/contracts
  git mv core/README.md models/contracts/README.md
  git status --short | head
  # EXPECTED: a block of `R  core/inference/... -> models/contracts/...` rows.
  ```

- [ ] Count the references BEFORE rewriting, and record the numbers:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core\.inference' | wc -l   # EXPECTED 116
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il  'core/inference' | wc -l    # EXPECTED  31
  ```

  If either number differs, STOP: the tree is not at `3f60776` and every count in this plan must be re-measured before continuing.

- [ ] Rewrite, dotted pass then slash pass:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core\.inference' | xargs sed -i '' 's/core\.inference/models.contracts/g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core/inference'  | xargs sed -i '' 's|core/inference|models/contracts|g'
  ```

- [ ] Verify zero residue and no accidental damage:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'core\.inference\|core/inference'
  # EXPECTED: no output.
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'models\.contracts\.contracts\|models/contracts/contracts'
  # EXPECTED: no output (a double-applied sed would show here).
  ```

- [ ] Hand-review the diff for verbatim quotations. A `sed` cannot tell a live path from a quoted one. Find the candidates and restore any block that reproduces another document's words:

  ```bash
  git diff -M -- '*.py' '*.html' | grep -n '^[-+].*models\.contracts' | grep -i 'adr\|verbatim\|quote\|>' | head -40
  ```

  Known outcome at `3f60776`: no `.py`/`.html` file in the repo quotes an ADR verbatim in a block that names `core.inference`; every hit is the module's own prose describing its own dependency. If a genuine quotation appears, restore that hunk by hand and record it in the PR body as a deliberate historical reference (spec §3.6.5's gate explicitly permits these).

- [ ] Extend `testpaths` for the new root. `models/contracts` has no tests today, and pytest 9.1.1 would *silently ignore* the entry if the directory were missing — so this edit cannot fail loudly either way, which is exactly why it is paired with a collected-count check below rather than trusted:

  ```bash
  sed -i '' 's|^testpaths = modules console scripts$|testpaths = modules console models scripts|' pytest.ini
  cat pytest.ini
  # EXPECTED line 4: testpaths = modules console models scripts
  .venv/bin/pytest -q --collect-only | tail -1
  # EXPECTED: 2711 tests collected -- UNCHANGED from Task 1. `models/contracts`
  #   holds no tests, so adding the root must move nothing. A different number
  #   here means a tree started or stopped being collected; chase it.
  ```

- [ ] Run the guards from Task 1 first — they are the fastest signal:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q console/ops/tests/test_app_labels.py console/inference/tests/test_registry_paths.py
  # EXPECTED: 7 passed
  ```

- [ ] Run the full matrix:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts console modules
  .venv/bin/pytest -q core/tests
  # EXPECTED: 2711 passed in the first three; 29 passed in core/tests.
  ```

- [ ] Confirm Django itself is happy, including the stdlib-shadowing check the `models/` name makes worth running:

  ```bash
  .venv/bin/python manage.py check
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "NO MIGRATIONS"
  .venv/bin/python manage.py collectstatic --noinput --dry-run
  # EXPECTED: System check identified no issues; "NO MIGRATIONS"; collectstatic completes.
  ```

- [ ] Commit:

  ```bash
  git add -A
  git commit -m "$(cat <<'EOF'
  refactor(regroup): core/inference -> models/contracts

  Pure `git mv` of the 21-file contracts package (roles, operations, job
  kinds, queue seam, bindings, gateway, catalog, engines, and the ComfyUI
  graph-template registry) plus the mechanical rewrite of every reference
  to it: 748 dotted occurrences across 116 files, 54 slash occurrences
  across 31 files. `docs/` deliberately untouched -- P4 owns that sweep.

  `_TEMPLATES`, `_VARIANTS`, and the four public readers move byte-for-byte
  (spec section 3.8, ruling R1). `core/README.md` becomes
  `models/contracts/README.md`; its format/files paragraph splits out in
  the next task.

  `pytest.ini` gains `models` so the root is collectable before Task 4 puts
  tests under it. App labels, table names, and `django_migrations` all
  pinned unchanged by Task 1's guards.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 3: `core/format.py`, `core/files.py`, `core/tests/` → `foundation/`; `core/` ceases to exist

**Files**
- Create: `foundation/__init__.py` (new), `foundation/README.md` (new — carries the format/files paragraph split out of the old `core/README.md`)
- Move: `core/format.py` → `foundation/format.py`; `core/files.py` → `foundation/files.py`; `core/tests/` → `foundation/tests/` (`__init__.py`, `test_format.py`, `test_files.py`)
- Delete: `core/__init__.py` (and with it the `core/` directory)
- Modify: every tracked non-`docs/` file matching `core.format` (18), `core.files` (9), `core/format` (5), `core/files` (2), `core/tests` (2)
- Modify: `pytest.ini:4` — `testpaths = modules console models foundation scripts`
- Modify: `modules/rag/tests/test_flag_hygiene.py:82-84` — replace the bare `("modules", "console")` tree tuple with a transitional, existence-guarded `_APP_TREES` spanning old and new roots, plus a new anti-vacuous pin (moved here from Task 11 so the sweep is never vacuous for Tasks 4-11; Task 11 narrows it and proves it still guards)
- Modify: `models/contracts/README.md` — drop the format/files bullet that now lives in `foundation/README.md`
- Test: `foundation/tests/test_format.py` + `foundation/tests/test_files.py` (29 tests) enter the default run for the first time

**Interfaces**
- Consumes: nothing new.
- Produces: `foundation.format.format_timecode` and its siblings; `foundation.files` — identical signatures. 12 production import sites and 3 respectively (spec §3.3), all rewritten mechanically.

**Steps**

- [ ] Create the column package with the §3.7-style docstring:

  ```bash
  mkdir -p foundation
  cat > foundation/__init__.py <<'EOF'
  """The `foundation/` column: shared, feature-agnostic platform code.

  `format.py` and `files.py` are rule-1 PURE LEAVES -- no Django models, no
  views, no database, no import of any non-pure module -- so any column may
  import them in any direction. `ops/` and `setup/` are Django apps and are
  column-private (rule 2).

  Named `foundation/` rather than `platform/` because `platform` is a
  stdlib module name and `manage.py` puts the repo root at `sys.path[0]`;
  a `platform/` package here would shadow it and break `collectstatic`.
  ADR 0010 section 2 already ruled on this once. See the spec's section 2.1.
  """
  EOF
  ```

- [ ] Move the three leaves:

  ```bash
  git mv core/format.py foundation/format.py
  git mv core/files.py  foundation/files.py
  git mv core/tests     foundation/tests
  ```

- [ ] Count, then rewrite all five prefixes:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core\.format' | wc -l   # EXPECTED 18
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core\.files'  | wc -l   # EXPECTED  9
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core/format'  | wc -l   # EXPECTED  5
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core/files'   | wc -l   # EXPECTED  2
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core/tests'   | wc -l   # EXPECTED  2

  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core\.format' | xargs sed -i '' 's/core\.format/foundation.format/g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core\.files'  | xargs sed -i '' 's/core\.files/foundation.files/g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core/format'  | xargs sed -i '' 's|core/format|foundation/format|g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core/files'   | xargs sed -i '' 's|core/files|foundation/files|g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'core/tests'   | xargs sed -i '' 's|core/tests|foundation/tests|g'
  ```

- [ ] Delete the now-empty `core/` package and confirm it is gone:

  ```bash
  git rm core/__init__.py
  ls core 2>&1
  # EXPECTED: ls: core: No such file or directory
  ```

- [ ] Split the README. Create `foundation/README.md`:

  ```markdown
  # foundation/ — shared, feature-agnostic platform code

  What every column may reach for, and the two Django apps that belong to
  no feature.

  - **`format.py`** — display formatting shared across module boundaries
    (`format_timecode` and siblings). A rule-1 **pure leaf**: no Django
    models, no views, no database, no import of any non-pure module.
    ADR 0014 states the criterion for a shared leaf — four modules across a
    module boundary call it, "which is precisely the condition for a shared
    leaf" (`docs/adr/0014-media-ingestion.md:623-634`). It has 12
    production import sites across 6 packages.
  - **`files.py`** — the same, for filesystem helpers (3 production import
    sites).
  - **[`ops/`](ops/)** — operator tooling: `manage.py backup` / `restore`.
    A Django app, label `ops`, and **column-private** under rule 2.
  - **[`setup/`](setup/README.md)** — the universal engine-install page. A
    Django app, label `setup`, column-private under rule 2.
  - **`templates/_shell.html`** — the shared page shell every app's base
    template extends, reached through `TEMPLATES[0]["DIRS"]`.

  ## The import law

  - **Rule 1 — pure leaves are universally importable.** `format.py`,
    `files.py`, everything under `models/contracts/`, and everything under
    `agents/contracts/`. Any column, any direction.
  - **Rule 2 — Django apps are column-private.** `ops/` and `setup/` are
    not importable from `tools/`, `models/`, or `agents/`. The one
    sanctioned cross-column import in the whole repo is
    `models.registry.bindings`, and it is not in this column.
  - **Rule 3 — cross-column *work* goes through a seam, never an import:**
    the queue (`models.contracts.queue`), the gateway
    (`models.contracts.gateway`), and the tool registry
    (`agents.contracts.tools`).

  Named `foundation/` and not `platform/`: `platform` is a stdlib module
  name, `manage.py` puts the repo root on `sys.path[0]`, and a `platform/`
  package here breaks `manage.py collectstatic`. See ADR 0010 §2 and the
  spec's §2.1.
  ```

- [ ] Remove the now-duplicated bullet from `models/contracts/README.md`. Open it and delete the Storage/format-and-files line that describes what `foundation/README.md` now owns, replacing the file's opening heading `# core/ — The platform` with `# models/contracts/ — the platform contracts` and its first paragraph with a sentence naming what actually lives there (roles, operations, job kinds, the queue seam, bindings, the gateway, the catalog, and the engine adapters). Add a closing line pointing at `../../foundation/README.md` for the pure leaves that used to sit beside them.

- [ ] **Un-vacuum the flag-hygiene sweep, here and not in Task 11.** `modules/rag/tests/test_flag_hygiene.py:82-84` hardcodes `for tree in ("modules", "console")` — **bare** tree names that no prefix pass can reach, and a `Path.rglob` on a missing directory yields nothing rather than raising. From the moment `foundation/` exists, that sweep must know about it, or Tasks 4-11 run with a guard that silently covers less and less of the tree. Replace `_iter_repo_test_files`'s tuple with a transitional, existence-guarded list that spans both the old roots and the new:

  ```python
  # The repo's app trees. Named explicitly (not a bare repo-root rglob) so
  # `.venv/`, `data/`, `docs/`, and `.git/` are never walked. TRANSITIONAL
  # during the P0 regroup: it spans the old roots and the new ones at once,
  # so the sweep never narrows mid-move. Task 11 narrows it to the final
  # four and proves it still guards with a deliberate red run.
  #
  # This is a FILESYSTEM-PATH LITERAL and it fails SILENTLY -- a stale entry
  # makes the whole sweep a no-op that passes. `test_the_sweep_actually_
  # finds_files` below is what makes that impossible.
  _APP_TREES = ("modules", "console", "tools", "models", "foundation", "agents")


  def _iter_repo_test_files():
      repo_root = Path(settings.BASE_DIR)
      for tree in _APP_TREES:
          tree_root = repo_root / tree
          if not tree_root.is_dir():
              continue
          for path in tree_root.rglob("test_*.py"):
              if EXCLUDED_DIR_NAMES & set(path.parts):
                  continue
              yield path
  ```

  and add the anti-vacuous pin in the same commit:

  ```python
  def test_the_sweep_actually_finds_files():
      """`_APP_TREES` is a filesystem-path literal: a stale entry turns this
      whole module into a no-op that passes. This is the assertion that makes
      that impossible -- the sweep must find, at minimum, this file itself,
      and enough files that a silently-emptied tree is visible."""
      found = list(_iter_repo_test_files())
      assert "test_flag_hygiene.py" in {path.name for path in found}
      assert len(found) >= 50, len(found)
  ```

  ```bash
  .venv/bin/pytest -q tools/rag/tests/test_flag_hygiene.py
  # EXPECTED: green, including the new pin. (Path is still
  #   modules/rag/tests/test_flag_hygiene.py at this task -- it moves in Task 8.)
  ```

- [ ] Extend `testpaths` — this is the edit that finally brings the 29 previously-uncollected tests into the default run (`docs/DEV.md:259-266` documents them as excluded today):

  ```bash
  sed -i '' 's|^testpaths = modules console models scripts$|testpaths = modules console models foundation scripts|' pytest.ini
  ```

- [ ] Verify residue and count:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'core\.format\|core\.files\|core/format\|core/files\|core/tests'
  # EXPECTED: no output.
  .venv/bin/pytest -q --collect-only | tail -1
  # EXPECTED: 2741 tests collected  (2711 after Task 1, + the 29 that just joined,
  #   + the flag-hygiene anti-vacuous pin added above). Record it: from here to
  #   Task 10 every task must report this same number.
  ```

- [ ] Run the full matrix. `pytest -q core/tests` retires here — the path no longer exists and its tests are inside `testpaths`:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts console modules foundation
  .venv/bin/python manage.py check
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "NO MIGRATIONS"
  # EXPECTED: 2741 passed in the first three; no issues; "NO MIGRATIONS".
  ```

- [ ] Commit:

  ```bash
  git add -A
  git commit -m "$(cat <<'EOF'
  refactor(regroup): core/{format,files,tests} -> foundation/; core/ is gone

  The two rule-1 pure leaves and their 29 tests move to the new
  `foundation/` column, and `core/` ceases to exist. `core/README.md`'s
  format/files paragraph splits into a new `foundation/README.md` that
  states the three-rule import law; the remainder stays in
  `models/contracts/README.md`.

  `pytest.ini` gains `foundation`, which is what finally brings those 29
  previously-uncollected tests into the default run (docs/DEV.md:259-266
  documents them as excluded today). Collected count: 2711 -> 2740.

  Named `foundation/` and not `platform/`: `platform` is a stdlib module
  name and `manage.py` puts the repo root on sys.path[0]. ADR 0010 section 2
  ruled on this once already; spec section 2.1 re-verifies it three ways.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 4: `console/inference/` → `models/registry/`

36 `.py` (7 migrations, 16 tests, 2 management-command files), 9 templates, 1 README. The app whose label is `inference` and whose tables are `inference_modelconnection` / `inference_rolebinding` — the single loudest proof that a package move renames nothing in the database.

**Files**
- Move: `console/inference/` → `models/registry/`
- Modify: `models/registry/apps.py:21` — `name = "models.registry"` (**`label = "inference"` at `:22` is NOT touched**)
- Modify: `config/settings.py:140` — `"console.inference.bindings.db_provider"` → `"models.registry.bindings.db_provider"`
- Modify: `config/settings.py:233` — `INSTALLED_APPS` entry `"console.inference"` → `"models.registry"`
- Modify: `config/urls.py:9` — `include("console.inference.urls")` → `include("models.registry.urls")`
- Modify: `models/registry/apps.py:39,40,41` — the three `rag.reencode` registration strings
- Modify: every other tracked non-`docs/` file matching `console.inference` (68) or `console/inference` (38)
- Test: existing 16 test modules in `models/registry/tests/` plus Task 1's `test_registry_paths.py` (which lives in this package and moves with it)

**Interfaces**
- Consumes: nothing new.
- Produces: `models.registry.bindings` — the **one sanctioned cross-column import** under rule 2. Its four current importers (`modules/rag/jobs.py:74`, `modules/rag/views.py:41`, `modules/vision/jobs.py:101`, `modules/vision/views.py:25`) are rewritten by the same mechanical pass. `role_primary(role_key) -> tuple[str, int | None]` (`bindings.py:192`), `resolve_connection_named(pk, capability) -> tuple[ResolvedModel, str]` (`bindings.py:317`), `db_provider`, `env_provider` — all unchanged.

**Steps**

- [ ] Move the package:

  ```bash
  git mv console/inference models/registry
  ```

- [ ] Count, then rewrite:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console\.inference' | wc -l   # EXPECTED 68
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console/inference' | wc -l    # EXPECTED 38

  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console\.inference' | xargs sed -i '' 's/console\.inference/models.registry/g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console/inference' | xargs sed -i '' 's|console/inference|models/registry|g'
  ```

  The dotted pass alone handles `config/settings.py:140` (the `INFERENCE_BINDING_PROVIDER` default), `config/settings.py:233` (`INSTALLED_APPS`), `config/urls.py:9` (`include(...)`), `models/registry/apps.py:21` (`AppConfig.name`), and `models/registry/apps.py:39-41` (the three `rag.reencode` registration strings) in one sweep — they are all the same literal prefix.

- [ ] **Prove `label` survived.** This is the assertion the whole PR turns on:

  ```bash
  grep -n 'name = \|label = ' models/registry/apps.py
  # EXPECTED:
  #   21:    name = "models.registry"
  #   22:    label = "inference"
  ```

  If `label` changed, revert the file and re-apply the sed with a narrower pattern — `label` must read `"inference"` verbatim.

- [ ] Verify residue and the four settings/urls edits landed:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'console\.inference\|console/inference'
  # EXPECTED: no output.
  grep -n 'models.registry' config/settings.py config/urls.py
  # EXPECTED: settings.py:140 (db_provider), settings.py:233 (INSTALLED_APPS), urls.py:9 (include)
  ```

- [ ] Run the guards, then the matrix:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  # The path guard moved with this package; the label guard is still at
  # console/ops/tests/ until Task 6.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q \
      models/registry/tests/test_registry_paths.py \
      console/ops/tests/test_app_labels.py
  # EXPECTED: 7 passed
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts console modules models foundation
  .venv/bin/python manage.py check
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "NO MIGRATIONS"
  # EXPECTED: 2741 passed; no issues; "NO MIGRATIONS".
  ```

- [ ] Prove the tables really did not move, against a real database:

  ```bash
  .venv/bin/python manage.py shell -c "from django.apps import apps; m = apps.get_model('inference','ModelConnection'); print(m._meta.db_table, m._meta.app_label)"
  # EXPECTED: inference_modelconnection inference
  ```

- [ ] Commit:

  ```bash
  git add -A
  git commit -m "$(cat <<'EOF'
  refactor(regroup): console/inference -> models/registry

  36 .py (7 migrations, 16 test modules, 2 management commands), 9
  templates, and the README move; 464 dotted occurrences across 68 files
  and 59 slash occurrences across 38 files rewritten mechanically.

  `AppConfig.name` becomes "models.registry"; `label` stays "inference"
  and is deliberately untouched, which is why `inference_modelconnection`
  and `inference_rolebinding` keep their names, `django_migrations` needs
  no rows, `django_content_type` is unchanged, and this PR has no
  migration. Django only derives a label from the module path when the
  subclass has not set one (django/apps/config.py:34-35), and this one
  always has.

  `models.registry.bindings` is now the one sanctioned cross-column import
  under the new rule 2; its four existing importers were rewritten by the
  same pass.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 5: `console/jobs/` → `models/queue/`

22 `.py` (9 top-level, 3 migrations, 7 tests including 6 test modules, 3 management), 2 templates. Contains the **compose worker entrypoint**.

**Files**
- Move: `console/jobs/` → `models/queue/`
- Modify: `models/queue/apps.py:19` — `name = "models.queue"` (**`label = "jobs"` at `:20` NOT touched**)
- Modify: `config/settings.py:149` — `INFERENCE_QUEUE_BACKEND` default `"console.jobs.backend"` → `"models.queue.backend"`
- Modify: `config/settings.py:234` — `INSTALLED_APPS` `"console.jobs"` → `"models.queue"`
- Modify: `config/urls.py:10` — `include("console.jobs.urls")` → `include("models.queue.urls")`
- Modify: `compose.yaml:113` and `compose.preview.yaml:162` — the `console/jobs/worker.py` comments (slash pass)
- Modify: every other tracked non-`docs/` file matching `console.jobs` (45) or `console/jobs` (37)
- Test: existing 6 test modules in `models/queue/tests/`

**Interfaces**
- Consumes: nothing new.
- Produces: `models.queue.backend` (the `INFERENCE_QUEUE_BACKEND` module — `enqueue`/`get_job`/`cancel_job`), `models.queue.models.InferenceJob`/`JobSettings`, `models.queue.worker.Worker`, `models.queue.scheduler.plan_admissions`, `models.queue.claim.claim_and_admit`. Index names `"jobs_claim_scan"` and `"jobs_orphan_sweep"` (`console/jobs/models.py:140,144`) are **string literals** and do not move.

**The compose command does not change.** `compose.yaml:112` is `command: python manage.py run_jobs` — a *command name*, which Django resolves by walking every installed app's `management/commands/` directory. The file `console/jobs/management/commands/run_jobs.py` moves to `models/queue/management/commands/run_jobs.py`; the command stays `run_jobs`. Same for `compose.yaml:86`'s `ingest_watch` (which lives in the rag app) and `Dockerfile:32`'s `manage.py migrate` / `config.asgi`.

**Steps**

- [ ] Move the package:

  ```bash
  git mv console/jobs models/queue
  ```

- [ ] Count, then rewrite:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console\.jobs' | wc -l   # EXPECTED 45
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console/jobs' | wc -l    # EXPECTED 37

  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console\.jobs' | xargs sed -i '' 's/console\.jobs/models.queue/g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console/jobs' | xargs sed -i '' 's|console/jobs|models/queue|g'
  ```

- [ ] Prove `label` and the index literals survived:

  ```bash
  grep -n 'name = \|label = ' models/queue/apps.py
  # EXPECTED: 19: name = "models.queue"   20: label = "jobs"
  grep -n 'jobs_claim_scan\|jobs_orphan_sweep' models/queue/models.py
  # EXPECTED: both literals still read "jobs_..." (they are index names in the DB)
  ```

- [ ] Prove the worker entrypoint is still discoverable by name:

  ```bash
  .venv/bin/python manage.py run_jobs --help | head -3
  # EXPECTED: usage text for run_jobs -- Django found it under models/queue/management/commands/.
  grep -n 'run_jobs\|ingest_watch' compose.yaml
  # EXPECTED: :86 ingest_watch, :112 run_jobs -- both UNCHANGED command names.
  ```

- [ ] Verify residue, then run the matrix:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'console\.jobs\|console/jobs'
  # EXPECTED: no output.
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts console modules models foundation
  .venv/bin/python manage.py check
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "NO MIGRATIONS"
  # EXPECTED: 2741 passed; no issues; "NO MIGRATIONS".
  ```

- [ ] Commit:

  ```bash
  git add -A
  git commit -m "$(cat <<'EOF'
  refactor(regroup): console/jobs -> models/queue

  22 .py (9 top-level, 3 migrations, 6 test modules, 3 management
  commands) and 2 templates move; 181 dotted occurrences across 45 files
  and 78 slash occurrences across 37 files rewritten.

  `label = "jobs"` untouched, so `jobs_inferencejob` and the two literal
  index names ("jobs_claim_scan", "jobs_orphan_sweep") are unchanged and
  this PR still has no migration.

  compose.yaml's worker command is `manage.py run_jobs` -- a command NAME,
  which Django resolves by walking each installed app's
  management/commands/ directory -- so the package move does not touch it.
  The file moves; the compose command does not. Verified with
  `manage.py run_jobs --help`.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 6: `console/ops/` → `foundation/ops/`

12 `.py` including 4 test modules and 3 management-command files. Carries Task 1's app-label guard with it.

**Files**
- Move: `console/ops/` → `foundation/ops/` (including `tests/test_app_labels.py` from Task 1, `tests/test_docs_sync.py`, `tests/test_backup.py`, `tests/test_restore.py`)
- Modify: `foundation/ops/apps.py:25` — `name = "foundation.ops"` (**`label = "ops"` at `:26` NOT touched**)
- Modify: `config/settings.py:236` — `INSTALLED_APPS` `"console.ops"` → `"foundation.ops"`
- Modify: every other tracked non-`docs/` file matching `console.ops` (10) or `console/ops` (6)
- Test: `foundation/ops/tests/` (4 modules); `test_docs_sync.py` must stay green — its four pinned constants (`DOCUMENTED_BACKUP_SEQUENCE`, `RESTORE_DB_SEQUENCE`, `RECOVERY_SEQUENCE`, `LIVE_POSTGRES_WARNING`) contain only `docker compose` / `./data/...` strings and no repository Python path, so the move cannot desync them from `docs/OPERATIONS.md`

**Interfaces**
- Consumes: nothing new.
- Produces: `foundation.ops.backup` / `foundation.ops.restore` and the `backup`/`restore` management commands (resolved by *name*, unchanged).

**Note on the hardcoded table names.** `console/ops/backup.py:56-66` hardcodes seven table names (`"rag_document"`, `"rag_category"`, `"rag_askrecord"`, `"vision_generationjob"`, `"inference_modelconnection"`, `"inference_rolebinding"`, `"data_rag_chunks"`) with the rationale at `backup.py:24` — "`f"{app_label}_{model_name.lower()}"` rather than importing the models". They are label-derived, every label is frozen by Task 1's guard, so **none of them changes** and none may be edited by this task.

**Steps**

- [ ] Move the package:

  ```bash
  git mv console/ops foundation/ops
  ```

- [ ] Count, then rewrite:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console\.ops' | wc -l   # EXPECTED 10
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console/ops' | wc -l    # EXPECTED  6

  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console\.ops' | xargs sed -i '' 's/console\.ops/foundation.ops/g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console/ops' | xargs sed -i '' 's|console/ops|foundation/ops|g'
  ```

- [ ] Prove `label` and the seven hardcoded table names survived:

  ```bash
  grep -n 'name = \|label = ' foundation/ops/apps.py
  # EXPECTED: 25: name = "foundation.ops"   26: label = "ops"
  grep -n '"rag_document"\|"vision_generationjob"\|"inference_modelconnection"\|"data_rag_chunks"' foundation/ops/backup.py
  # EXPECTED: all four literals unchanged.
  ```

- [ ] Verify residue, then run the matrix (the docs-sync test is the one to watch):

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'console\.ops\|console/ops'
  # EXPECTED: no output.
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q foundation/ops/tests/
  # EXPECTED: test_docs_sync (4), test_app_labels (5), test_backup, test_restore -- all passed.
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts console modules models foundation
  .venv/bin/python manage.py check
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "NO MIGRATIONS"
  # EXPECTED: 2741 passed; no issues; "NO MIGRATIONS".
  ```

- [ ] Commit:

  ```bash
  git add -A
  git commit -m "$(cat <<'EOF'
  refactor(regroup): console/ops -> foundation/ops

  12 .py (4 test modules, 3 management commands) move; 27 dotted
  occurrences across 10 files and 8 slash occurrences across 6 files
  rewritten. `label = "ops"` untouched.

  backup.py's seven hardcoded table names are label-derived and therefore
  unchanged -- that is the point of hardcoding them rather than importing
  the models (backup.py:24). test_docs_sync stays green because its four
  pinned constants carry only `docker compose`/`./data/...` strings and no
  repository Python path.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 7: `console/setup/` → `foundation/setup/`; `console/` ceases to exist

4 `.py`, 1 template, 1 test module, 1 README — the smallest move, and the one that lets `console/` be deleted.

**Files**
- Move: `console/setup/` → `foundation/setup/`
- Delete: `console/__init__.py` (and with it the `console/` directory)
- Modify: `foundation/setup/apps.py:13` — `name = "foundation.setup"` (**`label = "setup"` at `:14` NOT touched**)
- Modify: `config/settings.py:235` — `INSTALLED_APPS` `"console.setup"` → `"foundation.setup"`
- Modify: `config/urls.py:14` — `include("console.setup.urls")` → `include("foundation.setup.urls")`
- Modify: `pytest.ini:4` — drop `console` → `testpaths = modules models foundation scripts`
- Modify: every other tracked non-`docs/` file matching `console.setup` (7) or `console/setup` (3)
- Test: `foundation/setup/tests/test_views.py`

**Interfaces**
- Consumes: nothing new.
- Produces: `foundation.setup.urls` (URL names `"setup-index"` etc. are literals and unchanged).

**Steps**

- [ ] Move, then delete the empty column:

  ```bash
  git mv console/setup foundation/setup
  git rm console/__init__.py
  ls console 2>&1
  # EXPECTED: ls: console: No such file or directory
  ```

- [ ] Count, then rewrite:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console\.setup' | wc -l   # EXPECTED 7
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console/setup' | wc -l    # EXPECTED 3

  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console\.setup' | xargs sed -i '' 's/console\.setup/foundation.setup/g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'console/setup' | xargs sed -i '' 's|console/setup|foundation/setup|g'
  ```

- [ ] Drop `console` from `testpaths` — it must go in the same commit that deletes the directory. Not because it would error: pytest 9.1.1 **silently ignores** a `testpaths` entry whose directory is gone. That is the danger. A stale entry would leave the suite exiting 0 while an entire tree stopped being collected, so the collected-count check below is the only thing that can catch it:

  ```bash
  sed -i '' 's|^testpaths = modules console models foundation scripts$|testpaths = modules models foundation scripts|' pytest.ini
  cat pytest.ini
  # EXPECTED line 4: testpaths = modules models foundation scripts
  ```

- [ ] Prove `label` survived and residue is zero:

  ```bash
  grep -n 'name = \|label = ' foundation/setup/apps.py
  # EXPECTED: 13: name = "foundation.setup"   14: label = "setup"
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'console\.setup\|console/setup'
  # EXPECTED: no output.
  ```

- [ ] Run the matrix. **The reversed-order command loses `console`:**

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts modules models foundation
  .venv/bin/python manage.py check
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "NO MIGRATIONS"
  # EXPECTED: 2741 passed; no issues; "NO MIGRATIONS".
  ```

- [ ] Commit:

  ```bash
  git add -A
  git commit -m "$(cat <<'EOF'
  refactor(regroup): console/setup -> foundation/setup; console/ is gone

  4 .py, 1 template, 1 test module, and the README move; 7 dotted and 4
  slash occurrences rewritten. `label = "setup"` untouched.

  `console/__init__.py` deleted -- the column no longer exists. `pytest.ini`
  drops `console` from testpaths in the same commit, because pytest errors
  on a testpaths entry that is not there.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 8: `modules/rag/` → `tools/rag/`, and the migration-import fix

61 `.py` (20 top-level, 13 migrations, 24 tests including 23 test modules, 4 management), 6 templates, 1 README. The largest reference surface in the repo (1184 dotted occurrences across 88 files) and the one file in the whole move whose content must genuinely change.

**Files**
- Create: `tools/__init__.py` (new)
- Move: `modules/rag/` → `tools/rag/`
- **Modify (content, deliberate): `modules/rag/migrations/0006_merge_duplicate_categories.py:8`** — delete `from modules.rag.categories import choose_canonical` and inline the function
- Modify: `tools/rag/apps.py:12` — `name = "tools.rag"` (**`label = "rag"` at `:13` NOT touched**)
- Modify: `tools/rag/apps.py:34` — `rematerialize="tools.rag.services.reencode_all"`
- Modify: `tools/rag/apps.py:65,66,67` — `plan_ask`/`run_ask`/`summarize_ask`
- Modify: `tools/rag/apps.py:96,97,98,100` — `plan_ingest`/`run_ingest`/`summarize_ingest`/`on_ingest_terminal`
- Modify: `tools/rag/apps.py:81,86,91` — the comment block naming `modules.rag.jobs.on_ingest_terminal` and `modules.rag.ingest.enqueue_ingest`/`enqueue_reingest` (dotted pass handles these; §3.6.2 counts one of them as "a comment naming a dotted path — update for truth")
- Modify: `config/settings.py:174,184` — the `modules.rag.views.document_upload` / `modules.rag.store.store_file` comments
- Modify: `config/settings.py:231` — `INSTALLED_APPS` `"modules.rag"` → `"tools.rag"`
- Modify: `config/urls.py:8` — `include("modules.rag.urls")` → `include("tools.rag.urls")`
- Modify: `Dockerfile:15` — the `modules/rag/transcode.py::probe_duration` comment (slash pass)
- Modify: `pytest.ini:4` — add `tools` → `testpaths = modules tools models foundation scripts`
- Modify: every other tracked non-`docs/` file matching `modules.rag` (88) or `modules/rag` (70)
- Test: existing 23 test modules; the migration inlining is pinned by `makemigrations --check` (Django imports every migration module when building the graph)

**Interfaces**
- Consumes: `models.registry.bindings` (rule-2 exception), `models.contracts.*` (rule 1).
- Produces: `tools.rag.retrieval.retrieve_nodes(question, category, settings_row, *, embed_resolved) -> tuple[list, bool, index]` (`modules/rag/retrieval.py:344`), `tools.rag.retrieval.apply_score_floor(nodes, score_floor, *, hybrid) -> list` (`retrieval.py:435`), `tools.rag.retrieval.answer_question(question, session_id=None, *, category=None, answer_resolved, embed_resolved) -> dict` (`retrieval.py:461`), `tools.rag.ingest.enqueue_reingest(doc) -> int | None` (`ingest.py:1229`), `tools.rag.models.RagSettings.get_solo()` (`models.py:444`) — **all signatures unchanged**; P1 consumes exactly these.

**Steps**

- [ ] Create the column package:

  ```bash
  mkdir -p tools
  cat > tools/__init__.py <<'EOF'
  """The `tools/` column: one folder per tool-owning feature.

  A future tool is one new folder here, not a new top-level directory --
  and the fact that every folder here is the SAME KIND OF THING (a feature
  that registers capability-scoped tools an agent may call) is the thing
  this column exists to keep visible in the tree.

  Each folder is a Django app and is COLUMN-PRIVATE (import law rule 2):
  `tools.rag` does not import `tools.vision`, and neither is importable
  from `models/`, `agents/`, or `foundation/`. The one sanctioned
  cross-column import a `tools/*` app may make is
  `models.registry.bindings` -- and only that module.
  """
  EOF
  ```

- [ ] **Inline `choose_canonical` into the migration BEFORE any rewrite.** Django imports every migration module when building the graph, so a stale import here breaks `migrate` and `makemigrations` outright — not just that one migration. Rewriting the import to `tools.rag.categories` would keep it working *and* keep the real defect: a migration must be frozen against the app code it ran with, and this one is coupled to a pure function that can change under it. Replace `modules/rag/migrations/0006_merge_duplicate_categories.py:6-10` with:

  ```python
  from django.db import migrations

  SEEDED_NAMES = {"Medical", "Engineering", "Reference & Manuals", "Business"}


  def choose_canonical(members, seeded_names):
      """INLINED from `modules.rag.categories.choose_canonical` (spec
      section 3.6.2, the one deliberate content change in the P0 regroup).

      A migration must be frozen against the app code it ran with, and this
      one was importing a live project module -- which Django imports at
      GRAPH-BUILD time, so a stale path there breaks `migrate` and
      `makemigrations` outright rather than only this one migration. The
      function is small and pure, so the honest fix is a copy that can
      never drift under the historical data it rewrote, not a corrected
      import.

      From >=1 Category-like objects sharing a normalized name, pick the
      row to keep: prefer one whose `.name` is a seeded default (its
      "pretty" casing), else the earliest by (created_at, id).
      """
      seeded = [c for c in members if c.name in seeded_names]
      pool = seeded or list(members)
      return min(pool, key=lambda c: (c.created_at, c.id))
  ```

  Leave `merge_dupes`, `noop`, and the `Migration` class exactly as they are — `merge_dupes` already calls `choose_canonical(members, SEEDED_NAMES)` at `:24` and now finds the module-level copy.

- [ ] **Sweep the docstring the inlining just falsified, in this same commit.** `modules/rag/categories.py:7` reads "Top-level imports stay dependency-free so data migrations can import `choose_canonical`." After the inlining, `choose_canonical` has **zero** production callers — grep confirms only `categories.py:25` (the definition) and `modules/rag/tests/test_categories.py:148,152` remain. Rewrite `:6-7` to:

  ```python
  upload/rename endpoints cannot reintroduce casing duplicates. Top-level
  imports stay dependency-free as a matter of hygiene -- migration 0006 used
  to import `choose_canonical` from here and no longer does (it carries its
  own frozen copy, per the P0 regroup), so nothing outside this module and
  its tests depends on that property today.
  ```

  Verify:

  ```bash
  grep -rn "choose_canonical" --include="*.py" . | grep -v '\.venv'
  # EXPECTED exactly 4 hits: categories.py:25 (def), the migration's own def and
  #   call, and two in tools/rag/tests/test_categories.py. No import anywhere.
  ```

- [ ] Prove the migration graph still builds and the inlined copy is faithful:

  ```bash
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "GRAPH OK, NO MIGRATIONS"
  ```

  Then prove the inlined copy is faithful at the moment of the copy — the module name starts with a digit, so it has to be loaded by file path, not by `import`:

  ```bash
  .venv/bin/python - <<'PY'
  import importlib.util, inspect
  from modules.rag.categories import choose_canonical as live

  spec = importlib.util.spec_from_file_location(
      "m0006", "modules/rag/migrations/0006_merge_duplicate_categories.py"
  )
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)

  def body(fn):
      src = inspect.getsource(fn)
      return src.split('"""')[-1].split()

  assert body(live) == body(mod.choose_canonical), (body(live), body(mod.choose_canonical))
  assert mod.SEEDED_NAMES == {"Medical", "Engineering", "Reference & Manuals", "Business"}
  print("inlined copy matches the live function body")
  PY
  # EXPECTED: "GRAPH OK, NO MIGRATIONS" then "inlined copy matches the live function body"
  ```

- [ ] Commit the migration fix on its own, so the one deliberate content change is a reviewable hunk separate from the rename:

  ```bash
  git add modules/rag/migrations/0006_merge_duplicate_categories.py
  git commit -m "$(cat <<'EOF'
  fix(rag): inline choose_canonical into migration 0006

  0006 imported `modules.rag.categories.choose_canonical` at module scope.
  Django imports EVERY migration module when building the graph, so that
  import is load-bearing for `migrate` and `makemigrations` as a whole, not
  just for this one migration -- and a migration must be frozen against the
  app code it ran with, which an import into live project code is not.

  The function is small and pure, so it is copied in with a docstring
  saying where it came from and why. Asserted identical to the live body
  at the moment of the copy. This is the ONE deliberate content change in
  the P0 regroup (spec section 3.6.2).

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

- [ ] Move the package:

  ```bash
  git mv modules/rag tools/rag
  ```

- [ ] Count, then rewrite:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules\.rag' | wc -l   # EXPECTED 87  (88 before the migration-inlining commit above, which removed 0006's only `modules.rag` line)
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules/rag' | wc -l    # EXPECTED 70

  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules\.rag' | xargs sed -i '' 's/modules\.rag/tools.rag/g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules/rag' | xargs sed -i '' 's|modules/rag|tools/rag|g'
  ```

- [ ] Add `tools` to `testpaths`:

  ```bash
  sed -i '' 's|^testpaths = modules models foundation scripts$|testpaths = modules tools models foundation scripts|' pytest.ini
  ```

- [ ] Prove `label`, the 8 registration strings, and residue:

  ```bash
  grep -n 'name = \|label = ' tools/rag/apps.py
  # EXPECTED: 12: name = "tools.rag"   13: label = "rag"
  grep -c 'tools\.rag\.' tools/rag/apps.py
  # EXPECTED: 12 -- the 8 LIVE registration strings (:34 services.reencode_all;
  #   :65/:66/:67 plan_ask/run_ask/summarize_ask; :96/:97/:98 plan_ingest/
  #   run_ingest/summarize_ingest; :100 on_ingest_terminal) plus 4 DOCSTRING and
  #   COMMENT mentions (the ready() docstring at :20, and the on_terminal
  #   rationale block at :81/:86/:91). Measured as 12 for `modules.rag.` at
  #   3f60776, so 12 is the number that must survive the rewrite.
  grep -n 'tools\.rag\.' tools/rag/apps.py
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'modules\.rag\|modules/rag'
  # EXPECTED: no output.
  ```

- [ ] Run the guards — Task 1's `test_registry_paths.py` is the direct proof that all eight registration strings still resolve — then the full matrix:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/registry/tests/test_registry_paths.py foundation/ops/tests/test_app_labels.py
  # EXPECTED: 7 passed
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts modules tools models foundation
  .venv/bin/python manage.py check
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "NO MIGRATIONS"
  # EXPECTED: 2741 passed; no issues; "NO MIGRATIONS".
  ```

- [ ] Commit:

  ```bash
  git add -A
  git commit -m "$(cat <<'EOF'
  refactor(regroup): modules/rag -> tools/rag

  61 .py (20 top-level, 13 migrations, 23 test modules, 4 management
  commands), 6 templates, and the README move; 1184 dotted occurrences
  across 88 files and 129 slash occurrences across 70 files rewritten --
  the largest reference surface in the repo.

  `label = "rag"` untouched, so rag_document/rag_category/rag_askrecord and
  the label-keyed `apps.get_model("rag", ...)` calls inside migrations 0004
  and 0006 all keep working with no migration.

  The eight `tools.rag.*` registration strings (one rematerialize, four
  ask/ingest planners+handlers+summarizers, on_ingest_terminal) fail
  LAZILY, so they are proven by Task 1's resolve-every-registered-path
  guard rather than by the suite alone.

  `pytest.ini` gains `tools`.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 9: `modules/vision/` → `tools/vision/`, `modules/home/` → `tools/home/`; `modules/` ceases to exist

37 `.py` (10 top-level, 6 migrations, 20 tests including 19 test modules), 10 templates, 1 README, plus the placeholder README that is all `modules/home/` contains.

**OWNER DECISION, open (spec §2.2).** The owner marked the vision package move VISION-OWNED and delegated it to a separate session. The spec **recommends against splitting it**: this move touches `config/settings.py:232`, `config/settings.py:268`, and `config/urls.py:20` — lines the rest of P0 also touches — and two PRs editing the same handful of lines is a guaranteed conflict on a repo whose live deploy merges main SHAs only (`docs/DEV.md:302-316`). The move itself is a pure rename plus four string edits and contains **no vision logic**; the genuinely VISION-OWNED work is P1's `tools/vision/tools.py`. **This plan assumes the recommendation is accepted and executes the move here.** If the owner keeps the split, run Tasks 1-8 + 10-13 as P0a and this task alone as P0b, back-to-back on the same day, with the vision session rebasing on P0a — and note that P0a cannot delete `modules/__init__.py` or drop `modules` from `testpaths` until P0b lands.

**Files**
- Move: `modules/vision/` → `tools/vision/`
- Move: `modules/home/README.md` → `tools/home/README.md`
- Delete: `modules/__init__.py` (and with it the `modules/` directory)
- Modify: `tools/vision/apps.py:17` — `name = "tools.vision"` (**`label = "vision"` at `:18` NOT touched**)
- Modify: `tools/vision/apps.py:58,59,60` — `plan_generate`/`run_generate`/`summarize_generate`
- Modify: `config/settings.py:232` — `INSTALLED_APPS` `"modules.vision"` → `"tools.vision"`
- Modify: `config/settings.py:268` — context processor `"modules.vision.context_processors.features"` → `"tools.vision.context_processors.features"`
- Modify: `config/urls.py:20` — `include("modules.vision.urls")` → `include("tools.vision.urls")`
- Modify: `pytest.ini:4` — drop `modules` → `testpaths = tools models foundation scripts` (**final form**)
- Modify: every other tracked non-`docs/` file matching `modules.vision` (44), `modules/vision` (22), `modules/home` (1)
- Test: existing 19 test modules, including `tools/vision/tests/test_comfyui_workflows.py`, which must stay green with no edit beyond its patch targets (spec §3.8 / ruling R1)

**Interfaces**
- Consumes: `models.registry.bindings`, `models.contracts.*`.
- Produces (P1's VISION-OWNED tasks consume exactly these, unchanged): `tools.vision.services.operation_catalog(resolved: ResolvedModel | None = None) -> list[dict]` (`modules/vision/services.py:304`), `tools.vision.services.submit_job(operation_key, raw_params, files=None, resolved=None) -> GenerationJob` (`services.py:560`), `tools.vision.services.wait_for(job, timeout, interval=1.0, on_poll=None) -> GenerationJob` (`services.py:801`), `tools.vision.services.job_json(job) -> dict` (`services.py:742`), `tools.vision.services.parse_input_reference(reference) -> tuple[str, int]` (`services.py:379`), `tools.vision.services.resolve_inputs(operation, references) -> dict[str, StoredFile]` (`services.py:452`), `tools.vision.services.preflight(resolved=None) -> PreflightResult` (`services.py:150`), `INPUT_REFERENCE_KINDS = ("output", "input")` (`services.py:375`). URL names `vision-output-file` and `vision-input-file` (`modules/vision/urls.py:23-24`) are literals and unchanged.

**Steps**

- [ ] Move both packages, then delete the empty column:

  ```bash
  git mv modules/vision tools/vision
  mkdir -p tools/home
  git mv modules/home/README.md tools/home/README.md
  git rm modules/__init__.py
  ls modules 2>&1
  # EXPECTED: ls: modules: No such file or directory
  ```

- [ ] Count, then rewrite:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules\.vision' | wc -l   # EXPECTED 44
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules/vision' | wc -l    # EXPECTED 22
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules/home'   | wc -l    # EXPECTED  1

  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules\.vision' | xargs sed -i '' 's/modules\.vision/tools.vision/g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules/vision' | xargs sed -i '' 's|modules/vision|tools/vision|g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'modules/home'   | xargs sed -i '' 's|modules/home|tools/home|g'
  ```

- [ ] Take `testpaths` to its final P0 form, in the same commit that deletes `modules/`:

  ```bash
  sed -i '' 's|^testpaths = modules tools models foundation scripts$|testpaths = tools models foundation scripts|' pytest.ini
  cat pytest.ini
  # EXPECTED line 4: testpaths = tools models foundation scripts
  ```

- [ ] Prove `label`, the ComfyUI template registry, and residue:

  ```bash
  grep -n 'name = \|label = ' tools/vision/apps.py
  # EXPECTED: 17: name = "tools.vision"   18: label = "vision"
  grep -n 'tools\.vision\.jobs\.' tools/vision/apps.py
  # EXPECTED :58 plan_generate, :59 run_generate, :60 summarize_generate
  grep -n '("", "txt2img")\|("flux2", "edit")\|("qwen_image", "edit")' models/contracts/engines/comfyui_workflows/__init__.py
  # EXPECTED: all three keys byte-identical -- the registry moved in Task 2 and
  #   ruling R1 forbids any change to its keying in every phase.
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'modules\.vision\|modules/vision\|modules/home\|modules\.rag\|modules/rag'
  # EXPECTED: no output.
  ```

- [ ] Run the matrix. **The reversed-order command reaches its final P0 form here:**

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts foundation models tools
  .venv/bin/pytest -q tools/vision/tests/test_comfyui_workflows.py
  .venv/bin/python manage.py check
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "NO MIGRATIONS"
  # EXPECTED: 2741 passed; the workflows module green with no edit beyond patch
  #   targets; no issues; "NO MIGRATIONS".
  ```

- [ ] Prove the feature gate still gates. With the flag off, there is no vision role, no operations, no job kind, and no `/vision/` route:

  ```bash
  FARABUNKER_FEATURES='' .venv/bin/python manage.py check
  FARABUNKER_FEATURES='' .venv/bin/python -c "
  import django, os
  os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
  from models.contracts.jobkinds import all_job_kinds
  from models.contracts.roles import all_roles
  assert not [k for k in all_job_kinds() if k.key == 'vision.generate']
  assert not [r for r in all_roles() if r.key == 'vision.generate']
  print('vision flag off: no role, no job kind')
  "
  # EXPECTED: no issues; "vision flag off: no role, no job kind".
  # NOTE: FARABUNKER_FEATURES='' is NOT a supported state to run the SUITE under
  # (docs/DEV.md:238-244) -- this is a `manage.py`-level probe, not a pytest run.
  ```

- [ ] Commit:

  ```bash
  git add -A
  git commit -m "$(cat <<'EOF'
  refactor(regroup): modules/vision -> tools/vision; modules/ is gone

  37 .py (10 top-level, 6 migrations, 19 test modules), 10 templates, and
  the README move, plus modules/home's placeholder README; 162 dotted
  occurrences across 44 files and 38 slash occurrences across 22 files
  rewritten.

  `label = "vision"` untouched (vision_generationjob, vision_generatedoutput,
  vision_jobinput unchanged). The three `tools.vision.jobs.*` registration
  strings are proven by Task 1's resolve guard. config/settings.py:268's
  context processor and config/urls.py:20's feature-gated mount both move
  with the same pass.

  The ComfyUI graph-template registry stays exactly what it is -- its six
  (family, operation) keys, its one (family, variant) key, and its four
  public readers are byte-identical, per spec section 3.8 / ruling R1.

  `modules/__init__.py` deleted; pytest.ini reaches its final P0 form,
  `testpaths = tools models foundation scripts`.

  Sequencing note: per spec section 2.2 this move is in the same PR as the
  rest of P0 rather than split into a VISION-OWNED PR, because it touches
  config/settings.py:232,268 and config/urls.py:20 -- lines the rest of the
  move also touches -- and it contains no vision logic. The VISION-OWNED
  work is P1's tools/vision/tools.py.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 10: the shared shell template, the `agents/` skeleton, and the four column READMEs

**Files**
- Move: `templates/_shell.html` → `foundation/templates/_shell.html` (and delete the now-empty `templates/`)
- Modify: `config/settings.py:254` — `"DIRS": [BASE_DIR / "templates"]` → `"DIRS": [BASE_DIR / "foundation" / "templates"]`
- Create: `agents/__init__.py`, `agents/README.md`
- Create: `tools/README.md`, `models/README.md` (`foundation/README.md` already written in Task 3; `models/contracts/README.md` already rewritten there)
- Modify: `tools/rag/README.md`, `tools/vision/README.md`, `tools/home/README.md`, `models/registry/README.md`, `foundation/setup/README.md`, top-level `README.md` — verify each reads truthfully after the mechanical passes; fix any remaining prose by hand
- Test: existing suite. Every page renders through `_shell.html`; a broken `DIRS` is an immediate `TemplateDoesNotExist` across `tools/rag/tests/test_views.py`, `tools/vision/tests/test_views_create.py`, `models/registry/tests/test_views.py`, `models/queue/tests/test_views.py`, and `foundation/setup/tests/test_views.py`

**Interfaces**
- Consumes: nothing.
- Produces: the template *name* `"_shell.html"` — unchanged. Every `{% extends "_shell.html" %}` (in `tools/rag/templates/rag/base.html:1`, `tools/vision/templates/vision/base.html:1`, `models/registry/templates/inference/base.html:1`, `models/queue/templates/jobs/base.html:1`, `foundation/setup/templates/setup/index.html:1`) keeps resolving, because the name is looked up through `TEMPLATES[0]["DIRS"]`, not through an app.

**Why `DIRS` and not an app `templates/` dir:** `foundation` itself is not an installed app (only `foundation.ops` and `foundation.setup` are), so `APP_DIRS: True` will never discover `foundation/templates/`. The `DIRS` entry is the mechanism, exactly as it is today.

**Steps**

- [ ] Move the shell and repoint `DIRS`:

  ```bash
  mkdir -p foundation/templates
  git mv templates/_shell.html foundation/templates/_shell.html
  rmdir templates
  sed -i '' 's|"DIRS": \[BASE_DIR / "templates"\]|"DIRS": [BASE_DIR / "foundation" / "templates"]|' config/settings.py
  grep -n '"DIRS"' config/settings.py
  # EXPECTED: "DIRS": [BASE_DIR / "foundation" / "templates"],
  ```

- [ ] Sweep the prose references to the shell's old location — **13 occurrences across 12 files** at `3f60776` (`_shell.html` names itself twice on one line):

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il 'templates/_shell\.html' | wc -l   # EXPECTED 12 files
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Iho 'templates/_shell\.html' | wc -l  # EXPECTED 13 occurrences
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -Il  'templates/_shell\.html' | xargs sed -i '' 's|templates/_shell\.html|foundation/templates/_shell.html|g'
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In 'templates/_shell\.html' | grep -v 'foundation/templates/_shell'
  # EXPECTED: no output.
  ```

  Then hand-check `config/settings.py:263` — the context-processor comment reads "the shared shell (foundation/templates/_shell.html) can gate its nav link", which is now true.

- [ ] Create the `agents/` skeleton. P0 creates the package and its README so the column exists in the tree and the import law is stated in one place; **P1 creates `agents/contracts/` and only P1 adds `agents` to `testpaths`** (there are no tests under `agents/` until then):

  ```bash
  mkdir -p agents
  cat > agents/__init__.py <<'EOF'
  """The `agents/` column: the central brain.

  Empty in P0 -- the physical regroup creates the column so the tree shows
  where the agent layer lands, and nothing more. P1 adds
  `agents/contracts/` (the pure, Django-free TOOL CONTRACT: ToolSpec,
  ToolResult, ToolContext, StepBudget, the registry, the LLM tool-schema
  renderer, and the artifact-reference vocabulary). P2 adds
  `agents/runtime/`; P3 adds `agents/chat/`.

  `agents/contracts/` is a rule-1 PURE LEAF: universally importable, in any
  direction. `agents/runtime` and `agents/chat` are Django apps and are
  column-private (rule 2), with the single sanctioned exception that they
  may import `models.registry.bindings` -- and only that module.

  `agents/runtime` reaches a tool's implementation through a dotted-path
  STRING resolved at call time (rule 3), never a module-scope import of
  `tools/*` -- exactly as an AppConfig.ready() today registers a job kind
  without ever importing its handler.
  """
  EOF
  ```

- [ ] Write `agents/README.md`:

  ```markdown
  # agents/ — the central brain

  Empty in P0. The physical regroup creates this column; the code arrives
  in P1–P3.

  | Sub-package | Phase | What it is |
  |---|---|---|
  | `contracts/` | P1 | Pure, Django-free **tool contract**: `ToolSpec`, `ToolResult`, `ToolContext`, `StepBudget`, the module-level tool registry, the LLM tool-schema renderer, and the artifact-reference vocabulary. A **rule-1 pure leaf** — any column may import it, in any direction. |
  | `runtime/` | P2 | Django app, label `agents`. The `agent.turn` job kind, the bounded ReAct loop, flows, resident-agent sync. |
  | `chat/` | P3 | Django app, label `chat`. The permanent chat product at `/chat/`. |

  ## The two structural rules this column exists to hold

  1. **`agents/` never imports `tools/` at module scope.** A `ToolSpec`'s
     `runner` is a dotted-path string resolved at call time by
     `models.contracts.jobkinds.resolve_dotted_path` — the same mechanism
     an `AppConfig.ready()` already uses to register a job kind without
     importing its handler. `tools/*` imports `agents.contracts` (pure,
     rule 1); `agents/runtime` reaches `tools/*` only through a string.
     There is no cycle.
  2. **A tool runner never blocks on a queue job.** It may enqueue one and
     return its id; it must never call `get_job` in a loop, and it must
     never call `enqueue` for work whose result it needs. On a default
     install `JobSettings.memory_budget_bytes` is `null`, which means
     sequential mode — at most one job on the whole machine — so a job that
     waits on a job it enqueued deadlocks with certainty, not probability.

  Design: `docs/superpowers/specs/2026-08-25-agents-and-tools-design.md`.
  ```

- [ ] Write `tools/README.md` and `models/README.md` — each a short column README stating what the column is, what lives in it, and the three-rule import law as it applies to that column (mirroring `foundation/README.md` from Task 3). `models/README.md` must state the never-`import models` convention (spec §3.7) prominently, since it is the one naming hazard the regroup introduces.

- [ ] Read every in-tree README that survived the mechanical passes and fix what is now merely *technically* correct but reads wrong — `tools/rag/README.md`, `tools/vision/README.md`, `tools/home/README.md`, `models/registry/README.md`, `models/contracts/README.md`, `foundation/setup/README.md`, and the top-level `README.md`. The mechanical passes rewrote paths; they did not rewrite sentences like "Platform (console) code, not a §5 module", which is now a lie about a column that no longer exists:

  ```bash
  git ls-files -z -- ':!docs/' '*.md' | xargs -0 grep -In 'console\b\|§5 module\|modules/\b' | head -40
  ```

  Rewrite each hit to name the real column. This is prose work, one file at a time; there is no `sed` for it.

- [ ] Run the matrix. A broken `DIRS` shows up instantly as `TemplateDoesNotExist: _shell.html` across five apps' view tests:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts foundation models tools
  .venv/bin/python manage.py check
  .venv/bin/python manage.py collectstatic --noinput --dry-run
  # EXPECTED: 2741 passed; no issues; collectstatic completes (the
  #   stdlib-shadowing check that the `models/` name makes worth running).
  ```

- [ ] Commit:

  ```bash
  git add -A
  git commit -m "$(cat <<'EOF'
  refactor(regroup): shared shell to foundation/templates; agents/ skeleton; column READMEs

  templates/_shell.html -> foundation/templates/_shell.html, with
  TEMPLATES[0]["DIRS"] repointed at BASE_DIR / "foundation" / "templates".
  The template NAME "_shell.html" is unchanged, so every
  `{% extends "_shell.html" %}` keeps resolving; `foundation` is not an
  installed app, so DIRS (not APP_DIRS) is the mechanism, exactly as today.
  13 prose references to the old location swept.

  agents/ created as an empty column with a README stating the two rules
  it exists to hold: agents/ never imports tools/ at module scope (a
  runner is a dotted-path string), and a tool runner never blocks on a
  queue job. `agents` is deliberately NOT added to pytest.ini's testpaths
  -- there are no tests under it until P1 creates agents/contracts/tests/.

  New tools/README.md and models/README.md; models/ carries the
  never-`import models` convention (spec section 3.7). Every in-tree
  README re-read and rewritten where a mechanical path rewrite left a
  sentence describing a column that no longer exists.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 11: the three silent filesystem-path literals, proven still-guarding

Three literals in the repo name a directory tree as *data*. When the tree moves they do not fail — they become no-ops, and the guard they implement silently stops guarding. Two were carried by the mechanical slash passes. The third uses **bare** tree names no prefix pass could reach; Task 3 already gave it a transitional list spanning the old roots and the new, precisely so it never narrowed mid-move, and this task **narrows** that list to its final form. All three then get a deliberate red run — the point of this task, and the thing a green suite cannot substitute for.

**Files**
- Modify: `tools/rag/tests/test_flag_hygiene.py` — **narrow** Task 3's transitional `_APP_TREES` to `("tools", "models", "foundation", "agents")`, and tighten Task 3's anti-vacuous pin to assert one file per column
- Modify: `tools/rag/tests/test_retrieval.py:1046-1049` — **widen** the no-global-`Settings` guard to all six trees the regroup creates (spec §11.3), not merely relocate it
- Verify (already rewritten, no edit expected): `models/registry/tests/test_views.py:5768` — `assert ollama["source_path"] == "models/contracts/engines/ollama.py"`
- Test: the three guards themselves, each proven by reintroducing the thing it guards against

**Interfaces**
- Consumes: `django.conf.settings.BASE_DIR`.
- Produces: nothing importable.

**Steps**

- [ ] **Narrow** the transitional tree list Task 3 installed, and tighten its pin. `modules/` and `console/` no longer exist, so the two dead entries must go; and the pin graduates from "the sweep finds files at all" to "the sweep finds files in every column it claims to walk". In `tools/rag/tests/test_flag_hygiene.py`:

  ```python
  # The repo's app trees. Named explicitly (not a bare repo-root rglob) so
  # `.venv/`, `data/`, `docs/`, and `.git/` are never walked. This tuple is a
  # FILESYSTEM-PATH LITERAL and it fails SILENTLY: a stale entry makes this
  # whole sweep a no-op instead of an error, which is why
  # `test_the_sweep_actually_finds_files` below exists. `agents` is listed
  # ahead of P1 putting tests there -- a tree with no test files is skipped,
  # not an error, so this needs no edit when they appear.
  _APP_TREES = ("tools", "models", "foundation", "agents")
  ```

  `_iter_repo_test_files` itself is unchanged — Task 3 already gave it the
  `is_dir()` guard and the loop over `_APP_TREES`. Only the tuple narrows.

- [ ] Tighten the anti-vacuous pin Task 3 added, from "finds files" to "finds files in each column":

  ```python
  def test_the_sweep_actually_finds_files():
      """`_APP_TREES` is a filesystem-path literal: a stale entry turns this
      whole module into a no-op that passes. This is the assertion that
      makes that impossible -- the sweep must find, at minimum, this file
      and one test module from each column it claims to walk."""
      found = list(_iter_repo_test_files())
      names = {path.name for path in found}
      assert "test_flag_hygiene.py" in names
      trees = {path.relative_to(Path(settings.BASE_DIR)).parts[0] for path in found}
      assert {"tools", "models", "foundation"} <= trees, sorted(trees)
      assert len(found) >= 50, len(found)
  ```

- [ ] Run and confirm green:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q tools/rag/tests/test_flag_hygiene.py
  # EXPECTED: all passed, including test_the_sweep_actually_finds_files.
  ```

- [ ] **Red run 1 — the flag-hygiene sweep.** Reintroduce exactly what it guards against: in `tools/vision/tests/test_views_create.py`, add a throwaway test that overrides `FARABUNKER_FEATURES` to a literal `frozenset({"media"})` and calls `reverse("vision-create")` in the same test. Re-run:

  ```bash
  .venv/bin/pytest -q tools/rag/tests/test_flag_hygiene.py
  # EXPECTED: FAILED test_every_farabunker_features_override_in_a_client_touching_file_keeps_vision
  #   violations naming tools/vision/tests/test_views_create.py
  ```

  Remove the throwaway (`git checkout -- tools/vision/tests/test_views_create.py`) and re-run to green. Also confirm the anti-vacuous pin bites: temporarily set `_APP_TREES = ("modules", "console")`, re-run, expect `test_the_sweep_actually_finds_files` red; restore.

- [ ] **Red run 2 — the no-global-`Settings` guard, widened to the new columns.** The mechanical slash passes already carried `tools/rag/tests/test_retrieval.py:1046-1049` from `("modules/rag", "core/inference")` to `("tools/rag", "models/contracts")` — but spec §11.3 requires the guard be *extended* to the columns the regroup creates, not merely relocated. Nothing outside those two directories is checked today, so `tools/vision`, `models/registry`, `models/queue`, and `agents/` could each write to LlamaIndex's global `Settings` unnoticed. Widen it:

  ```python
          # Every tree that may construct an LLM or an embed model. Widened
          # from ("modules/rag", "core/inference") by the P0 regroup (spec
          # section 11.3): the two original directories became `tools/rag` and
          # `models/contracts`, and the four columns the regroup created are
          # added here because each can reach the gateway. `agents` is listed
          # ahead of P1 filling it -- `rglob` on a directory with no .py files
          # yields nothing, so an empty tree costs nothing and needs no edit
          # later.
          for app_dir in (
              "tools/rag", "tools/vision", "models/contracts",
              "models/registry", "models/queue", "agents",
          ):
  ```

  Then reintroduce the forbidden import: add `from llama_index.core import Settings` at the top of `models/contracts/gateway.py` and re-run:

  ```bash
  grep -n -A 4 'for app_dir in' tools/rag/tests/test_retrieval.py
  # EXPECTED: all six trees listed.
  .venv/bin/pytest -q tools/rag/tests/test_retrieval.py -k NoDirectGlobalSettingsImport
  # EXPECTED: FAILED -- offenders == ['models/contracts/gateway.py']
  ```

  Revert (`git checkout -- models/contracts/gateway.py`) and re-run to green.

- [ ] **Red run 3 — the engine source-path disclosure.** This one fails *loudly* by construction (`_engine_source_path` computes a real path relative to `BASE_DIR`, `models/registry/views.py:291-304`), so the red run is a formality that also proves the docstring's worked example matches the assertion:

  ```bash
  grep -n 'models/contracts/engines/ollama.py' models/registry/views.py models/registry/tests/test_views.py
  # EXPECTED: one hit in the docstring at views.py:291-296, one in the
  #   assertion at test_views.py:5768 -- the two must read identically.
  .venv/bin/pytest -q models/registry/tests/test_views.py -k derives_name_description_path_and_ports
  # EXPECTED: 1 passed
  ```

- [ ] Run the full matrix, then commit:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts foundation models tools
  # EXPECTED: 2741 passed -- UNCHANGED (Task 3's anti-vacuous pin is already in;
  #   this task narrows `_APP_TREES` and tightens that pin, adding no new test).

  git add -A
  git commit -m "$(cat <<'EOF'
  test(regroup): re-point and re-prove the three silently-failing path literals

  Three literals name a directory tree as DATA, so a move turns them into
  no-ops rather than errors:

  - tools/rag/tests/test_flag_hygiene.py's tree tuple used BARE names
    ("modules", "console") that no prefix rewrite could reach. Now an
    explicit, existence-guarded `_APP_TREES`, plus a new
    `test_the_sweep_actually_finds_files` so a stale entry fails loudly
    instead of silently. `agents` is listed ahead of P1 creating it -- a
    missing tree is skipped, not an error.
  - tools/rag/tests/test_retrieval.py's no-global-Settings guard and
    models/registry/tests/test_views.py's engine source-path assertion were
    both carried by the mechanical slash passes; verified here.

  All three proven still-guarding by a deliberate red run: a flag override
  that drops "vision" while calling reverse(), a `from llama_index.core
  import Settings` planted in models/contracts/gateway.py, and the
  source-path disclosure asserted against its own docstring example.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 12: prose sweep, the permanent grep gates, and the deploy note

Everything the prefix passes could not reach: sentences that name a column by a bare word, comments that describe a boundary that has been redrawn, and the two `AppConfig` docstrings that are now actively wrong.

**Files**
- Modify: `models/registry/apps.py:7-18` — `InferenceConfig`'s class docstring ("Platform (console) code, not a §5 module…") and its `ready()` docstring
- Modify: `models/queue/apps.py:7-16` — `JobsConfig`'s class docstring ("Platform (console) code… the queue-side twin of `models.registry` one rung up the app list")
- Modify: `foundation/ops/apps.py:9-22` — `OpsConfig`'s docstring ("`core/` is not an installed app…", "the same console-must-not-import-modules boundary ADR 0010 draws")
- Modify: `foundation/setup/apps.py:8-10`, `tools/rag/apps.py:8-9`, `tools/vision/apps.py:11-14` — the same class of sentence
- Modify: `config/settings.py:2` ("Django settings for the Farabunker core"), `:51` (a comment naming `console`)
- Modify: `models/registry/views.py:291-296`, `models/queue/views.py`, `tools/rag/views.py`, `tools/vision/views.py` — any remaining prose naming a dead column
- Modify: `compose.yaml:113`, `compose.preview.yaml:162`, `Dockerfile:15` — verify the slash passes landed
- Create: a permanent grep gate in `foundation/ops/tests/test_column_boundaries.py` (needles built by concatenation so the gate never matches its own source; `ALLOWED` seeded with its own path; `.superpowers/` excluded alongside `docs/`)
- Test: `foundation/ops/tests/test_column_boundaries.py` (new)

**Interfaces**
- Consumes: `django.conf.settings.BASE_DIR`.
- Produces: nothing importable.

**Steps**

- [ ] Find every remaining lie. These are bare-word hits no prefix rewrite could classify:

  ```bash
  git ls-files -z -- ':!docs/' ':!.superpowers/' | xargs -0 grep -In '\bconsole\b\|\bmodules/\|§5 module\|the console'"'"'s\|core/ is not' | grep -v '^docs/'
  ```

  Work the list one file at a time. The recurring rewrites:
  - "Platform (console) code, not a §5 module" → "A `models/` column app, not a tool" (`models/registry/apps.py:9-10`, `models/queue/apps.py:12`).
  - "the console's queue implementation (`models/queue/backend.py`)" → "the `models/` column's queue implementation" (`config/settings.py:150-151`).
  - "`core/` is not an installed app so it cannot host commands" → "`models/contracts/` and `foundation/format.py`/`files.py` are not installed apps, so they cannot host commands" (`foundation/ops/apps.py:11-12`).
  - "the same console-must-not-import-modules boundary ADR 0010 draws in the other direction (a §5 module may only reach into `models.registry.bindings`)" → "the same column-privacy boundary the import law draws in the other direction (a `tools/*` or `agents/*` app may only reach into `models.registry.bindings`)" (`foundation/ops/apps.py:18-20`).
  - "The offline RAG module" → "The offline RAG tool" (`tools/rag/apps.py:8`).
  - "Django settings for the Farabunker core" → "Django settings for Farabunker" (`config/settings.py:2`).

  Every rewrite states the **new** law from the Global Constraints above, verbatim in substance. Do not invent a fourth rule.

- [ ] Verify the three deploy-file comments landed and that **no deploy file names a Python path any more**:

  ```bash
  grep -n 'models/queue/worker\.py' compose.yaml compose.preview.yaml
  # EXPECTED: compose.yaml:113 and compose.preview.yaml:162
  grep -n 'tools/rag/transcode\.py' Dockerfile
  # EXPECTED: Dockerfile:15
  grep -n 'core/\|console/\|modules/' compose.yaml compose.preview.yaml compose.override.yaml Dockerfile
  # EXPECTED: no output.
  ```

- [ ] Write the permanent grep gate. Create `foundation/ops/tests/test_column_boundaries.py`:

  ```python
  """The permanent grep gate on the four-column regroup (spec section 3.6.5).

  `core/`, `console/`, and `modules/` no longer exist. A reference to one
  of them in tracked source is either a stale path (which will fail
  lazily, on a worker, months from now) or a sentence that is simply no
  longer true. Neither is acceptable in a codebase whose docstrings are
  load bearing.

  `docs/` is deliberately NOT swept here: P4 owns that, and an ADR
  reproduced verbatim must keep saying what it said.

  Lives beside `test_docs_sync.py` for the same reason that one does --
  `foundation.ops` is the app that already reaches across every column by
  design, and a repo-wide structural assertion belongs with it. Pure file
  text; no Django ORM, no database.
  """
  from __future__ import annotations

  import subprocess
  from pathlib import Path

  from django.conf import settings

  REPO_ROOT = Path(settings.BASE_DIR)

  # A dead column named as a PACKAGE (dotted) or a PATH (slash). Bare-word
  # "console" in prose is deliberately not matched -- an operator console
  # is still a real thing this product has; a `console.` import is not.
  #
  # BUILT BY CONCATENATION, and that is not stylistic. Writing any needle
  # out as a literal in this file would make THIS FILE a hit for its own
  # needle: the scan below reads every tracked file's text, including its
  # own, and the test would fail the moment it was written -- which is also
  # why this comment does not spell one out. Splitting each needle across
  # the `+` keeps the literal out of the file's text while the runtime
  # string is identical.
  _DEAD_PACKAGES = ("core", "console", "modules")
  _DEAD_LEAVES = (
      "inference", "format", "files", "tests", "jobs", "ops", "setup",
      "rag", "vision", "home",
  )
  DEAD_REFERENCES = tuple(
      package + separator + leaf
      for package in _DEAD_PACKAGES
      for leaf in _DEAD_LEAVES
      for separator in (".", "/")
  )

  # A file may carry a dead reference ONLY if it is listed here, with a
  # reason. This module is seeded in as belt-and-braces: the concatenation
  # above already keeps every needle out of this file's own text, and this
  # entry is what stops a future editor from reintroducing one by writing a
  # plain literal in a comment and quietly turning the gate red on itself.
  ALLOWED: dict[str, str] = {
      "foundation/ops/tests/test_column_boundaries.py":
          "this gate's own source -- see the concatenation note above",
  }


  def _tracked_files() -> list[str]:
      """Every tracked file except the two frozen-record trees.

      `docs/` is P4's sweep. `.superpowers/` holds six owner-requirement and
      peer-handoff documents -- four of which name old paths -- and they are
      the owner's own words and another session's handoff, recorded at a
      moment in time. Rewriting either would falsify a record exactly as
      rewriting an ADR quotation would.
      """
      out = subprocess.run(
          ["git", "ls-files", "--", ":!docs/", ":!.superpowers/"],
          cwd=REPO_ROOT, capture_output=True, text=True, check=True,
      )
      return [line for line in out.stdout.splitlines() if line]


  def test_no_tracked_source_file_outside_docs_names_a_dead_column():
      offenders: dict[str, list[str]] = {}
      for relative in _tracked_files():
          if relative in ALLOWED:
              continue
          path = REPO_ROOT / relative
          try:
              text = path.read_text(encoding="utf-8")
          except (UnicodeDecodeError, OSError):
              continue  # binary or unreadable: not source we can lie in
          hits = [needle for needle in DEAD_REFERENCES if needle in text]
          if hits:
              offenders[relative] = hits
      assert offenders == {}, (
          "these files still name core/, console/, or modules/ -- a package "
          f"that no longer exists: {offenders}"
      )


  def test_the_gate_is_reading_real_files():
      """Anti-vacuous pin: a broken `git ls-files` call would make the test
      above pass by looking at nothing."""
      tracked = _tracked_files()
      assert len(tracked) > 200, len(tracked)
      assert "config/settings.py" in tracked
      assert not any(name.startswith("docs/") for name in tracked)
      assert not any(name.startswith(".superpowers/") for name in tracked)


  def test_the_needles_are_the_ones_we_meant():
      """The concatenation above is easy to get subtly wrong. This pins the
      runtime strings without ever writing one as a literal in this file."""
      assert ("core" + "." + "inference") in DEAD_REFERENCES
      assert ("modules" + "/" + "vision") in DEAD_REFERENCES
      assert ("console" + "." + "jobs") in DEAD_REFERENCES
      assert len(DEAD_REFERENCES) == 60


  def test_the_gate_would_actually_catch_a_dead_reference():
      """Anti-vacuous pin: plant a needle in a string this test owns and
      confirm the matcher sees it. A gate that cannot fail proves nothing."""
      planted = "from " + "modules" + ".rag" + " import ingest"
      assert [n for n in DEAD_REFERENCES if n in planted] == ["modules" + "." + "rag"]


  def test_the_four_columns_exist_and_the_three_old_ones_do_not():
      for column in ("tools", "models", "agents", "foundation"):
          assert (REPO_ROOT / column / "__init__.py").is_file(), column
      for dead in ("core", "console", "modules", "templates"):
          assert not (REPO_ROOT / dead).exists(), dead
  ```

- [ ] Run it and expect **red first** if any prose remains, then fix and re-run:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  .venv/bin/pytest -q foundation/ops/tests/test_column_boundaries.py
  # EXPECTED first run: red, naming whatever prose is left.
  # EXPECTED after the sweep: 5 passed.
  ```

- [ ] Record the live-deploy answer in the PR body, and verify it rather than asserting it. **An image rebuild is NOT required for this PR.** Evidence, all at `3f60776`:
  - `compose.yaml:65`, `:85`, `:111` each bind-mount `.:/app`, so `web`, `watcher`, and `worker` all run the checkout, not the image's baked copy.
  - `Dockerfile` copies only `requirements.txt` (`:25`) and then `COPY . .` (`:28`); its `CMD` (`:32`) names `manage.py migrate` and `config.asgi:application` — neither a moved path.
  - `docs/DEV.md:292-299` states the rule directly: "A change to the image, not just the code, needs a rebuild. Anything in `Dockerfile` or `requirements.txt` — a new apt package, a new Python dependency — is baked into the image, and the bind mount does not carry it." P0 changes neither file's dependency content (`Dockerfile:15` is a comment).
  - **What IS required:** `docker compose restart watcher worker`. `web` runs under Django's dev auto-reloader and picks up the bind mount on the next request; `watcher` and `worker` each run a single management command with no reload machinery, and this PR moves job-kind code (`docs/DEV.md:279-290`). Forgetting this is the single most common way a change appears not to work when it does.

  Verify both claims before writing them down:

  ```bash
  grep -n ':/app' compose.yaml
  # EXPECTED: three `- .:/app` lines at :65, :85, :111.
  grep -n 'COPY\|CMD' Dockerfile
  # EXPECTED: :25 COPY requirements.txt ., :28 COPY . ., :32 CMD ... config.asgi
  ```

- [ ] Run the full matrix and commit:

  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q scripts foundation models tools
  # EXPECTED: 2746 passed (2741 + 5 boundary tests).

  git add -A
  git commit -m "$(cat <<'EOF'
  docs(regroup): prose sweep and a permanent column-boundary gate

  Every sentence the mechanical prefix passes could not classify: six
  AppConfig class docstrings that described a "console" column or a "§5
  module", config/settings.py's module docstring and its console comment,
  and the ops app's account of a boundary that has been redrawn. Each now
  states the new three-rule import law, verbatim in substance.

  New foundation/ops/tests/test_column_boundaries.py makes the sweep
  permanent: no tracked file outside docs/ may name core/, console/, or
  modules/ (dotted or slashed), the four columns must exist, and the three
  old ones must not. Two anti-vacuous pins so a broken `git ls-files` call
  cannot make it pass by looking at nothing. `docs/` is excluded by design
  -- P4 owns that sweep, and a verbatim ADR quotation must keep saying what
  it said.

  Deploy note, verified not asserted: an image rebuild is NOT required.
  compose.yaml:65,85,111 bind-mount `.:/app` on web/watcher/worker; the
  Dockerfile copies only requirements.txt and the tree, and its CMD names
  `config.asgi`, not a moved path; docs/DEV.md:292-299 scopes rebuilds to
  Dockerfile/requirements.txt changes, and this PR changes neither's
  dependency content. `docker compose restart watcher worker` IS required
  -- this PR moves job-kind code (docs/DEV.md:279-290).

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 13: the P0 gate matrix and the deploy ladder

Every gate the spec §12.1 names, run in order, on the finished branch. Nothing new is written here; this task is evidence.

**Files**
- Modify: none (unless a gate fails, in which case the fix belongs in the task that caused it)
- Test: the whole suite, plus five Django-level checks and the four-rung deploy ladder

**Interfaces**
- Consumes: everything.
- Produces: the PR body.

**Steps**

- [ ] **Gate 1 — every path is a detected rename, and every content delta is classifiable.** Produce the rename report and read it:

  ```bash
  git diff -M --stat main...HEAD | tail -5
  git diff -M --summary main...HEAD | grep -c '^ rename '
  # EXPECTED: ~230 renames detected.
  git diff -M --numstat main...HEAD | awk '$1 != 0 || $2 != 0' | wc -l
  ```

  Then classify. Every content hunk must be (a) an import line, (b) an enumerated dotted-path literal, (c) a filesystem-path literal, (d) a `mock.patch` target, (e) prose/comment/config path text, or (f) the `choose_canonical` inlining. **This is a human read, not a command.** A hunk that is none of those is scope creep and comes out.

  ```bash
  git diff -M main...HEAD -- '*.py' | grep '^[-+]' | grep -v '^[-+][-+]' | grep -vE 'core\.|console\.|modules\.|core/|console/|modules/|models\.|tools\.|foundation\.|agents\.|models/|tools/|foundation/|agents/' | head -60
  # EXPECTED: only the choose_canonical inlining, Task 1's two guard files,
  #   Task 11's anti-vacuous pin, and Task 12's boundary gate.
  ```

- [ ] **Gate 2 — the import grep returns empty:**

  ```bash
  grep -rn "from \(core\|console\|modules\)\." --include="*.py" . ; echo "exit=$?"
  grep -rn "import \(core\|console\|modules\)\." --include="*.py" . ; echo "exit=$?"
  # EXPECTED: no output, exit=1 for both.
  ```

- [ ] **Gate 3 — no migrations:**

  ```bash
  .venv/bin/python manage.py makemigrations --check --dry-run && echo "GATE 3 PASS: NO MIGRATIONS"
  git diff --name-only main...HEAD | grep 'migrations/' | grep -v '0006_merge_duplicate_categories'
  # EXPECTED: "GATE 3 PASS"; the only migration file in the diff is 0006, and
  #   its diff is the choose_canonical inlining and nothing else.
  ```

- [ ] **Gate 4 — `collectstatic` runs.** This is the stdlib-shadowing check: 77 modules in `site-packages` do a bare `import platform`, including `django/contrib/staticfiles/management/commands/collectstatic.py`, and `manage.py` puts the repo root at `sys.path[0]`. `models`, `tools`, `agents`, and `foundation` are all absent from `sys.stdlib_module_names` and from every `site-packages` bare import — this gate proves it empirically:

  ```bash
  .venv/bin/python -c "import sys; print({n: n in sys.stdlib_module_names for n in ('models','tools','agents','foundation','platform')})"
  # EXPECTED: {'models': False, 'tools': False, 'agents': False, 'foundation': False, 'platform': True}
  .venv/bin/python manage.py collectstatic --noinput --dry-run
  # EXPECTED: completes without error.
  ```

- [ ] **Gate 5 — `migrate --plan` against a restored production backup shows zero operations.** Restore a backup into a scratch database, point `DATABASE_URL` at it, and plan:

  ```bash
  # (restore per docs/OPERATIONS.md into a scratch DB, e.g. farabunker_p0_check)
  DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_p0_check' \
    .venv/bin/python manage.py migrate --plan
  # EXPECTED: "Planned operations:" followed by nothing, or the explicit
  #   "No planned migration operations." -- ZERO operations. Any planned
  #   operation means a label moved, and that is a stop-the-line failure.
  ```

- [ ] **Gate 6 — the full suite, both flag states, both collection orders:**

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q
  .venv/bin/pytest -q scripts foundation models tools
  # EXPECTED: 2746 passed in every one of the four runs. Any difference in the
  #   collected count between the configured and reversed orders is a
  #   registration-leakage bug and must be chased, not accepted.
  ```

- [ ] **Gate 7 — the three silent guards are still guarding.** Already proven by Task 11's red runs; re-state the evidence in the PR body with the three commands and their expected red output, so a reviewer can reproduce them without re-deriving them.

- [ ] **Gate 8 — the deploy ladder** (`docs/DEV.md:190-324`), in order, with no success language before Rung 4:

  - **Rung 1** — the suite, above. Done.
  - **Rung 2** — the branch's own preview stack. `scripts/preview up <branch>`, then `docker compose restart watcher worker` (job-kind code moved). Drive the smoke checklist below by hand, in a browser.
  - **Rung 3** — live, at `:8000`. Merge `main` into the branch first, resolve nothing by force, then deploy per `docs/DEV.md:302-316` (the live stack merges main SHAs only; serialize container git ops across sessions). **No image rebuild** (Task 12's verified note); `docker compose restart watcher worker` after the merge.
  - **Rung 4** — fresh pixels on the owner's live system. Only after Rung 4 may this work be described as done.

- [ ] Drive the **Smoke Checklist** at Rung 2 and again at Rung 3. Every step is browser-level and every expected result is spelled out:

  1. **`/rag/`** loads; the shared shell's nav renders links to RAG, Vision, Models, Queue, and Setup. *(Proves `foundation/templates/_shell.html` resolves through the new `DIRS`.)*
  2. **`/rag/search/?q=<a word you know is in the library>`** returns result cards with titles, locators, and scores. *(Proves `tools.rag.retrieval.retrieve_nodes` + `apply_score_floor` still run.)*
  3. **`/rag/`** — ask a question; the queued placeholder appears, then the answer with citations. *(Proves the `rag.ask` job kind's three registration strings resolve **on a worker** — the lazy-failure class §3.6.2 names. This step is the whole reason Task 1's guard exists, and it is the one a green suite cannot substitute for.)*
  4. **`/rag/`** — upload a small document; the library row goes PENDING → READY. *(Proves `rag.ingest`'s four strings, including `on_ingest_terminal`, and `manage.py ingest_watch` under its unchanged command name.)*
  5. **`/rag/documents/<id>/`** — hit Retry on a READY document; it goes PENDING → READY again. *(Proves `enqueue_reingest` and the migration-0006-era `Category` rows are intact.)*
  6. **`/inference/`** — the model console lists registered connections; "Supported APIs" shows an engine's source path reading `models/contracts/engines/ollama.py`. *(Proves `_engine_source_path` computes a real relative path against the new tree — the loud literal.)*
  7. **`/queue/`** — the queue page lists the jobs from steps 3-5 with their summaries. *(Proves each kind's `summarizer` string resolves, and that `jobs_claim_scan`/`jobs_orphan_sweep` indexes are still the ones the scanner uses.)*
  8. **`/vision/`** — the create page renders its operation chooser and form. *(Proves `tools.vision` registered its role, five operations, and job kind, and that `config/settings.py:268`'s context processor resolves.)*
  9. **`/vision/`** — run one generation through the queue; the placeholder polls and resolves to an image. *(Proves `vision.generate`'s three registration strings resolve on a worker and that the ComfyUI graph-template registry is byte-identical.)*
  10. **`/setup/`** — every registered engine renders its install guide and a live reachability check. *(Proves `foundation.setup` mounted at its unchanged URL name.)*
  11. **`manage.py backup /app/data/backups/<name>`** inside the web container completes and its manifest lists the seven expected tables. *(Proves `foundation.ops`'s management commands are discoverable by name and its hardcoded, label-derived table names still match reality.)*
  12. **Disable JavaScript** and repeat steps 3 and 9. Both must fully work via plain POST + redirect. *(Offline-first: JS is progressive enhancement only.)*

- [ ] Write the PR body. It must state, with its evidence: the eight gates and their outputs; the one deliberate content change (`choose_canonical`); the classification of every content-delta class; the deploy note (no rebuild, restart `watcher worker`); the owner-decision status of the vision move (spec §2.2); and the fact that `docs/**` is deliberately untouched and belongs to P4.

- [ ] **This PR alone is a PR.** Nothing else lands with it.

---

## Coverage against the spec

| Spec item | Task |
|---|---|
| §3.1 target tree (`tools/`, `models/`, `agents/`, `foundation/`) | 2, 3, 8, 9, 10 |
| §3.2 move map — `core/inference` → `models/contracts` (incl. `engines/`, `comfyui_workflows/`) | 2 |
| §3.2 — `core/format.py`, `core/files.py`, `core/tests/`, `core/README.md` | 3 |
| §3.2 — `console/inference` → `models/registry` | 4 |
| §3.2 — `console/jobs` → `models/queue` (incl. the `run_jobs` compose-entrypoint note) | 5 |
| §3.2 — `console/ops` → `foundation/ops` | 6 |
| §3.2 — `console/setup` → `foundation/setup` | 7 |
| §3.2 — `modules/rag` → `tools/rag` | 8 |
| §3.2 — `modules/vision` → `tools/vision`, `modules/home` → `tools/home` | 9 |
| §3.2 — `templates/_shell.html` → `foundation/templates/` | 10 |
| §3.2 — deleting `core/`, `console/`, `modules/` `__init__.py` | 3, 7, 9 |
| §3.2 — four new top-level `__init__.py` | 2, 3, 8, 10 |
| §3.3 import law stated in READMEs and docstrings | 3, 10, 12 |
| §3.5 app labels / tables / migrations / content types unchanged | 1 (guard), 4-9 (per-task `label` check), 13 (gates 3, 5) |
| §3.6.1 the 594 import lines | 2-9 (dotted passes) |
| §3.6.2 the 35 dotted-path literals + the 14 registration strings | 4, 5, 7, 8, 9; guarded by Task 1's resolver |
| §3.6.2 the migration import (`0006`) | 8 |
| §3.6.3 `pytest.ini` `testpaths` | 2, 3, 7, 8, 9 |
| §3.6.3 the three silent filesystem-path literals | 11 |
| §3.6.4 the ~1116 `mock.patch` targets | 2-9 (dotted passes) |
| §3.6.5 prose/comment/config sweep + grep gate | 10, 12 |
| §3.7 the `models/` naming hazard | 2 (docstring), 10 (README), 13 (gate 4) |
| §3.8 / R1 ComfyUI template registry preserved verbatim | 2 (move), 9 (assertion) |
| §12.1 gate 1 (renames + classified deltas) | 13 |
| §12.1 gate 2 (import grep empty) | 13 |
| §12.1 gate 3 (`makemigrations --check`) | 1 (guard), 13 |
| §12.1 gate 4 (`collectstatic`) | 13 |
| §12.1 gate 5 (`migrate --plan` on a restored backup) | 13 |
| §12.1 gate 6 (both flag states, both orders) | every task; 13 |
| §12.1 gate 7 (three silent guards red-run-proven) | 11 |
| §12.1 gate 8 (Rungs 2→3→4) | 13 |
| §12.1 VISION-OWNED sequencing decision | 9 (recorded as an open owner decision) |


---

## Plan review

### Round 1 — AMEND (4 MAJOR / 7 minor). Author applied all findings.

**MAJOR**

| # | Finding | Applied in |
|---|---|---|
| M1 | Task 12's column-boundary gate scanned every tracked file's text for literal needles — **including its own source**, where those needles are written as string literals. It would have gone red the moment it was written, and `ALLOWED` was empty. | `DEAD_REFERENCES` is now built by concatenation (`package + separator + leaf`), so no needle appears as a literal in the file; `ALLOWED` is seeded with the gate's own path as belt-and-braces against a future editor writing one in a comment; two new tests pin the runtime needle set (`len == 60`) and prove the matcher catches a planted reference. |
| M2 | `.superpowers/` is **tracked** and **four** of its six files carry old paths (`comfyui-memory-seams-contract.md`, `comfyui-memory-seams-plan-summary.md`, `memory-governance.md`, `peer-handoff-worker-token-race.md`). Every `sed` would have rewritten the owner's own requirement documents and another session's handoff — falsifying a record exactly as rewriting an ADR quotation would. | `':!.superpowers/'` added to all 55 `git ls-files` pathspecs and to the gate's `_tracked_files()`, with the reason stated once in the rewrite-idiom section. The per-package prefix table was re-measured under the new exclusion: `core/inference` slash 54/31 → **52/30**, `console/jobs` slash 78/37 → **72/33**; every other count unchanged. |
| M3 | Task 2's pre-count used `grep -Ilc`, which prints `path:count` for *every* file, so `wc -l` would have reported 386, not 116, and the implementer would have stopped on a correct tree. | `-Ilc` → `-Il`. |
| M4 | The stated `testpaths` mechanism was backwards. **Measured on pytest 9.1.1:** a `testpaths` entry naming a missing directory is **silently ignored** (`-o testpaths="modules nosuchdir scripts"` → exit 0, 1736 collected); only a **command-line** path argument errors. The plan claimed the opposite and used it to justify the incremental timeline. | The mechanism is corrected at the timeline, in Task 2, and in Task 7, with the measurement recorded. The incremental timeline is **kept** — for the opposite and stronger reason: a *stale* entry silently stops collecting a whole tree while the suite still exits 0. A collected-count check is now the gate on every `testpaths` edit. |

**minor (all 7 applied)** — m1 the post-Task-3 collected count was 2733, should be **2740** (2704 + Task 1's 7 + the 29 joining), and every downstream expectation was re-based to 2741/2746; m2 the `_shell.html` sweep is **12 files / 13 occurrences** (the shell names itself twice on one line), so the count step now measures both with `-Il` and `-Io`; m3 the `_APP_TREES` fix moved from Task 11 into **Task 3** with a transitional list spanning old and new roots — otherwise the flag-hygiene sweep silently covers less and less of the tree across Tasks 4–11 — and Task 11 now *narrows* it and keeps the red-run proof; m4 the no-global-`Settings` guard is **widened** to `tools/vision`, `models/registry`, `models/queue`, and `agents` per spec §11.3, rather than merely relocated; m5 inlining `choose_canonical` leaves it with **zero** production callers, so `categories.py:6-7`'s docstring claim ("so data migrations can import `choose_canonical`") is swept in the same commit; m6 `grep 'tools.rag.' tools/rag/apps.py` returns **12**, not 8 — the 8 live registration strings plus 4 docstring/comment mentions; m7 the `modules.rag` file count at the Task 8 rewrite step is **87**, not 88, because the migration-inlining commit runs first and removes `0006`'s only hit.

### Orchestrator ruling

**Accepted all.** No further review round: the executor re-verifies every `file:line` reference against the tree before running each task, per the spec's own standing instruction that citations go stale as the tree moves.
