# Queue Memory Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The execution queue stops planning against numbers that can silently fall, stops being blind to memory held on an engine it is not currently using, stops orphaning a job whose own cold load starved the process that was supposed to keep it alive, and stops leaving a waiting surface to tick forever with nothing honest to say. Fifteen open findings (Q1–Q6, Q8–Q16), one branch.

**Architecture:** Four mechanisms, each landing on machinery that already exists.
*What a model costs* becomes a four-rung ladder on `ModelConnection` (`override ?? measured ?? engine_reported ?? None`) whose recorder refuses every lowering, with its own label vocabulary beside — never inside — the capability-source one.
*What is actually resident* turns `Worker._evict_to_match_plan`'s four phases into a pass that reads two new optional engine declarations (`unload_scope`, `residency_authority`) through the existing `getattr`/degrade idiom, protects every `RUNNING` key and every in-flight attempt's key from any unload call, barriers an exclusive launch across the whole swept set before the memory it was promised is released, and runs whether or not a memory budget is set.
*What keeps a job alive* moves the heartbeat onto its own daemon thread, gives `JobKind` a code-declared staleness, teaches the worker to notice a host that slept, and makes a duplicate submit restore the row to the live attempt instead of requeueing it.
*What a waiting surface says* makes the unset budget loud, reconciles a turn whose job row vanished, and makes a poll response the page cannot act on count against the bounded retry counter instead of re-ticking in silence.
Ordering (Q3) rides on top: a new pure `affinity_order` helper inside `models/queue/scheduler.py` sorts model-affine candidates first *within* a priority, bounded by a durable pass-over count.

**Tech Stack:** Python 3 / Django, pytest (+ `pytest.mark.django_db`), PostgreSQL, Django templates with inline `<style>`/`<script>` (no static pipeline).

**Spec:** `docs/superpowers/specs/2026-09-21-queue-memory-governance-design.md` — **revision 5, FINAL**. Its §10 decisions are all owner-ruled ("accept all recommendations", closing section); its §13 review record (rounds 1–3 plus the 2026-09-21 steward amendment) is adjudicated history. **Do not relitigate any of it.** Where this plan makes a call the spec left to the author, it says so in `## Self-review` under "Resolved ambiguities".

**Sequencing (load-bearing, from spec §7):** registry first, then **liveness before the cross-engine eviction widening**. The dependency is not stylistic: the phase the wide sweep lengthens — `_residency_snapshot` — contains no heartbeat call at all, and each `list_installed` can cost a full discovery timeout. Tasks 5–9 land the heartbeat thread and its friends; Task 12 widens the sweep. Never reorder those two blocks.

**Stewardship (before merge):**
- **Task 11 is a cross-column task in the engine adapters' column** — two one-line class attributes on the image adapter, **pre-cleared by the engine steward** (spec §13's steward-amendment table, finding S-1). Its diff goes to that steward as its own hunk. The text adapter's equivalent lines are **that steward's call and are not written here**.
- Every other task stays inside `models/queue`, `models/registry`, `models/contracts` (the job-kind registry and the engine-seam docstrings only), `agents/chat`, `agents/runtime`, `foundation/ops/tests` (one gate read, not edited) and `docs/`.
- **`tools/` is not touched by this branch at all** — not `tools/vision`, not `tools/rag`. The spec consumes engine seams as-is.
- Registry console test modules (`models/registry/tests/test_views_*.py`) are a stewarded six-module split: **new** registry tests go in a **new** module (Task 3 adds `test_footprint_provenance.py`); the only edits to the existing six are the four deliberately re-pinned assertions Task 3 names by line content.

---

## Global Constraints

Every task's requirements implicitly include all of these.

- **Tests and documentation ship in the same commit as the code.** Not a follow-up, not a separate PR (AGENTS.md non-negotiable 1).
- **The four runs are the gate**, on a **private** database:
  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
  ```
  **`farabunker_impl` on port 5433 is this executor's database name and nobody else's.** Never run two pytest processes against one database name; never point a run at a bare `test_farabunker`. One pytest process per session, in the foreground, natively — never inside a container.
- **Flag-dependent behaviour pins its own feature state.** Nothing in this track is gated on `FARABUNKER_FEATURES` (the queue and `JobSettings` are core, and `JobSettingsView` is deliberately not flag-gated), but any test that touches a chat URL and overrides `FARABUNKER_FEATURES` must keep `"vision"` in the overridden set, and a test that asserts flag-dependent behaviour sets the flag itself rather than assuming the ambient one.
- **Zero-JS doctrine.** Every page this track touches works with JavaScript off. The Queue page's new readings (pass-over count, hold-off state) and the budget callout are **server-rendered from rows the page already loads** — no new query, no polling, no script. The chat page's poll loop is the one sanctioned hand-rolled script in the repository, held under a dated exemption in `foundation/ops/tests/test_shared_poller.py::_EXEMPT`; Task 20's change stays *inside* that existing loop and must leave the exemption's companion assertion (the loop still exists) green, and the repo-wide half (`test_no_template_outside_the_helper_rolls_its_own_poll_loop`) green too.
- **Never-500.** Every page and endpoint this track touches degrades rather than raising: `QueueView`, `JobSettingsView`, the registry console, `turn_status`. A settings read that fails falls back to documented defaults.
- **Query-count pins: this track AUTHORS two and re-pins none.** Verified against the tree on 2026-09-21 (`grep -rn 'num_queries\|CaptureQueriesContext' models/queue/tests/` hits `test_worker.py` only): **`models/queue/tests/test_claim.py` carries no query-count assertion at all**, and the one queue-side pin, `test_worker.py::TestTheSingleSettingsReadPerTick`, counts statements containing `jobs_jobsettings` alone and is untouched by anything here. The real arithmetic: `not_before` is an extra `WHERE` on the existing candidate `SELECT` and adds **zero** queries (Task 12, commit 2); the pass-over bulk `UPDATE` adds **exactly one**, inside the transaction already open (Task 14); the widened sweep adds **exactly one** — `_eviction_targets` calls `registered_endpoints()` **without** `connections=`, so it fetches the connection rows itself (`ModelConnection.objects.all()`), once per exclusive-admitting tick, **regardless of how many endpoints come back**. (The console passes its own rows in and pays nothing; the worker has none to pass, and re-reading them per endpoint inside the loop is exactly what the pin exists to prevent.) Its other cost — `list_installed` per endpoint — is HTTP, not SQL. So two pins are **newly authored** — the widened sweep's (Task 12, commit 3) and the pass-over `UPDATE`'s (Task 14) — while the registry console's two existing pins stay at 10 (Task 4). Every pin asserts a **literal** number, proven red first; never a value the test computes, never a range. **No commit message in this plan may claim a re-pin that did not happen.**
- **No AI model or vendor names in committed prose.** Engine names (`ollama`, `comfyui`, "the image engine", "the text engine") are fine; checkpoint filenames and vendor model names are not. `foundation/ops/tests/test_docs_model_names.py` walks `docs/superpowers/` too — run it in any task that touches a `.md`.
- **No absolute machine paths** in tracked files (`<repo>`, `<worktree>`, `<home>`); tests build paths from `tmp_path`.
- **No `conftest.py`.** Shared fixtures live in each app's `tests/_helpers.py` and are imported by name.
- **Import law.** `agents/` never imports `models.queue` (only `models.contracts`). `models/queue` never imports `agents`. `models/contracts` imports neither. The stranded-turn reconciliation therefore lives in `agents/runtime/jobs.py` and is reached by `agents/chat`, never by the queue. *(Corrected 2026-09-21 as shipped, Task 19: it lives in NEW `agents/reconcile.py`, at the column root — not in `agents/runtime/jobs.py`. `reconcile_stranded_turn` must call `models.contracts.queue.get_job`, and `foundation/ops/tests/test_column_boundaries.py::test_no_runtime_module_blocks_on_a_queue_job` forbids that call anywhere under `agents/runtime/`. The import law itself is unchanged; only the module that satisfies it moved.)*
- **Cross-column law.** No file under `tools/` is edited. The only adapter edit in this branch is Task 11's two pre-cleared one-liners.
- **Two migrations, two apps, seven columns — no more.** `models/registry/migrations/0009_*` (2 columns, Task 1) and `models/queue/migrations/0005_*` (5 columns, Task 10). Verified against the tree on 2026-09-21: registry's highest is `0008_modelset.py`, the queue's is `0004_jobsettings_response_timeout_seconds.py`. Every task after 10 consumes columns that already exist; **no task may add a third migration**. `python manage.py makemigrations --check --dry-run` is part of the final gate.
- **Engine optional seams are read with `getattr`, never `hasattr` branching, and always degrade** — the idiom `_residency_snapshot`/`_evict_*` already use for `list_installed`/`unload`, extended to `unload_scope` and `residency_authority`.
- **Docstrings on new code.** Match the file you are editing; there is no formatter and no linter — the guard tests are the style law.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `models/registry/models.py` | + `engine_reported_footprint_bytes`/`_at`; four-rung `effective_footprint_bytes`; four-value `footprint_source` | 1 |
| `models/registry/migrations/0009_modelconnection_engine_reported_footprint.py` | **new** — migration 1 (2 columns) | 1 |
| `models/registry/bindings.py` | keep-the-maximum recorder; + `record_engine_reported_footprint`; + `registered_endpoints` | 2, 4 |
| `models/registry/views.py` | + `_FOOTPRINT_SOURCE_LABELS`; `_connection_views` resolves `footprint_source_label`; `_engine_endpoints` delegates to `registered_endpoints` | 3, 4 |
| `models/registry/templates/inference/_registered_connection.html` | footprint rows render one resolved label | 3 |
| `models/registry/tests/test_db_bindings.py` | re-pin `TestFootprintPrecedence` onto the four-rung vocabulary | 1 |
| `models/registry/tests/test_footprint_provenance.py` | **new** — ladder, recorder rule, label vocabulary, `registered_endpoints` | 1, 2, 3, 4 |
| `models/registry/tests/test_views_tables_and_picker.py` | re-pin the **four** footprint-disclosure value assertions; its four absence assertions and every capability-source assertion stay byte-identical | 3 |
| ~~`models/registry/tests/test_views_connection_edit.py`~~ | **not edited** — verified 2026-09-21: it carries no footprint-disclosure assertion, only override-validation error strings | — |
| `models/registry/README.md` | the three stored facts, keep-the-maximum, the two label vocabularies | 19 |
| `models/contracts/jobkinds.py` | + `JobKind.stale_after_seconds`, `JobKind.default_wait_seconds`; + `JobContext.wait_seconds` | 6, 16 |
| `models/contracts/engines/base.py` | `unload_scope` / `residency_authority` documented on the optional seam | 12 |
| `models/contracts/engines/comfyui.py` | **cross-column, pre-cleared** — two class attributes | 11 |
| `models/queue/worker.py` | heartbeat thread, sleep detection, duplicate-submit refusal, boot tolerance, the whole eviction rewrite (reach, protection, barrier, logging), affinity snapshot cache, detected-memory write, wait ceiling | 5–9, 12, 14, 15, 16 |
| `models/queue/claim.py` | kind-aware sweep, `sweep_orphans`, `not_before` filter, affinity ordering + pass-over increment | 6, 12, 14 |
| `models/queue/scheduler.py` | + `affinity_order`, `MAX_PASSOVERS`, `SchedCandidate.passed_over`; `plan_admissions` takes `resident_keys`/`max_passovers` | 14 |
| `models/queue/models.py` | + `not_before`, `passed_over`, `kind_wait_seconds`, `detected_memory_bytes`, `detected_memory_at` | 10 |
| `models/queue/migrations/0005_queue_memory_governance.py` | **new** — migration 2 (5 columns) | 10 |
| `models/queue/backend.py` | `QueueRow` carries `not_before`/`passed_over` | 10, 13 |
| `models/queue/views.py` | hold-off + passed-over row readings; loud unset budget; detected-memory prefill; per-kind wait form | 13, 14, 15, 16 |
| `models/queue/templates/jobs/queue.html` | waiting-row readings; budget callout | 13, 14, 15 |
| `models/queue/templates/jobs/settings.html` | budget prefill note; per-kind wait rows | 15, 16 |
| `models/queue/README.md` | **new** — the ladder, eviction's reach, the log vocabulary, the unset budget | 19 |
| `models/queue/tests/test_worker.py` | heartbeat thread, sleep detection, duplicate submit, boot tolerance, the eviction suite, the widened-sweep query pin | 5–9, 12, 15, 16 |
| `models/queue/tests/test_claim.py` | kind-aware sweep, `not_before`, pass-over increment (+ its **newly authored** query pin) | 6, 12, 14 |
| `models/queue/tests/test_scheduler.py` | `affinity_order` table tests; invariants re-pinned | 14 |
| `models/queue/tests/test_views.py` | hold-off/passed-over readings, loud budget, wait-ceiling form, never-500 | 13, 14, 15, 16 |
| `agents/reconcile.py` | **new** (2026-09-21 correction, Task 19 — this row read `agents/runtime/jobs.py`) — `STRANDED_TURN_GRACE_SECONDS`, `reconcile_stranded_turn`, `reconcile_stranded_turns`, `count_stranded_turns`. Outside `agents/runtime/`, because `test_no_runtime_module_blocks_on_a_queue_job` forbids the `get_job` call this module IS. `agents/runtime/jobs.py` is **not edited by this plan**. | 17 |
| `agents/management/commands/reconcile_turns.py` | **new** — the operator sweep, with `--dry-run` | 17 |
| `agents/chat/views/turns.py` | `turn_status` reconciles a missing job row; retryable `503` | 17, 18 |
| `agents/chat/templates/chat/conversation.html` | poll loop counts an unactionable body | 18 |
| `agents/chat/README.md`, `agents/runtime/README.md` | reconciliation, the poll loop's failure vocabulary, the carry-forward obligation | 19 |
| `docs/adr/0013-inference-execution-queue.md` | the dated amendment (spec §4, seven items) | 19 |
| `docs/OPERATIONS.md`, `docs/EXTENDING.md` | the log lines, the failure error, `reconcile_turns`; a kind declaring staleness/wait | 19 |

---

## Task 1: The third footprint rung and its two columns

**Spec:** §3.1 (rungs 1–4, the `footprint_source` vocabulary), §7 task 1 / migration 1. Owner decision 2 (accepted).

**Files:**
- Modify: `models/registry/models.py` (the footprint block at `measured_footprint_bytes` … `footprint_source`)
- Create: `models/registry/migrations/0009_modelconnection_engine_reported_footprint.py` (generated)
- Create: `models/registry/tests/test_footprint_provenance.py`
- Modify: `models/registry/tests/test_db_bindings.py` (`TestFootprintPrecedence`, re-pinned)

**Interfaces:**
- Produces:
  - `ModelConnection.engine_reported_footprint_bytes: int | None` (BigIntegerField, null/blank)
  - `ModelConnection.engine_reported_footprint_at: datetime | None` (DateTimeField, null/blank)
  - `ModelConnection.effective_footprint_bytes -> int | None` — `override ?? measured ?? engine_reported ?? None`
  - `ModelConnection.footprint_source -> str | None` — `"override" | "measured" | "engine_reported" | None`
- Consumes: nothing new. `models.registry.bindings.footprint_for` already reads `effective_footprint_bytes` and needs no edit.

**Migration number verified against the tree (2026-09-21):** `models/registry/migrations/` ends at `0008_modelset.py`, so this is `0009`.

- [ ] **Step 1: Write the failing tests**

Create `models/registry/tests/test_footprint_provenance.py`:

```python
"""The footprint provenance ladder and its own label vocabulary (Q1/Q9).

A NEW module, not an addition to one of the six stewarded
`test_views_*.py` files: those are another session's split, and the
convention this repository already follows (see
`test_console_override_query_count.py`'s own module docstring) is that a
task needing registry tests adds a module rather than editing one of the
six. The only edits this track makes inside those six are the
deliberately re-pinned footprint assertions named in the plan's Task 3.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

import pytest

from models.registry.models import ModelConnection


@pytest.mark.django_db
class TestTheFourRungLadder:
    """`override ?? measured ?? engine_reported ?? None`, and a
    `footprint_source` that names which rung answered. The rung-3
    columns exist so an engine-reported reading is never folded into the
    measured column: they are facts of different quality, and the
    console's label would be a lie again the moment they shared storage
    (spec §9.1)."""

    def _conn(self, **kwargs) -> ModelConnection:
        return ModelConnection.objects.create(
            name="c", engine="ollama", endpoint="http://e:1", model_id="m", **kwargs,
        )

    def test_nothing_set_is_unknown_and_has_no_source(self):
        connection = self._conn()

        assert connection.effective_footprint_bytes is None
        assert connection.footprint_source is None

    def test_engine_reported_alone_answers_and_names_itself(self):
        connection = self._conn(engine_reported_footprint_bytes=7)

        assert connection.effective_footprint_bytes == 7
        assert connection.footprint_source == "engine_reported"

    def test_measured_outranks_engine_reported(self):
        connection = self._conn(
            measured_footprint_bytes=11, engine_reported_footprint_bytes=7,
        )

        assert connection.effective_footprint_bytes == 11
        assert connection.footprint_source == "measured"

    def test_the_operators_word_outranks_both(self):
        connection = self._conn(
            footprint_override_bytes=3,
            measured_footprint_bytes=11,
            engine_reported_footprint_bytes=7,
        )

        assert connection.effective_footprint_bytes == 3
        assert connection.footprint_source == "override"

    def test_a_zero_engine_reported_reading_is_still_an_answer_not_unknown(self):
        """Zero is a value, `None` is the absence of one -- the ladder
        walks on `is not None`, never on truthiness, so a genuinely
        zero-byte reading cannot silently read as "unknown, run alone".

        THE SCHEDULER'S INTENT WINS OVER THE CONSOLE'S HERE, and the two
        genuinely differ: `models.registry.views._human_size(0)` returns
        `None` by its own documented falsy contract, so Task 3's
        `{% if item.footprint_display %}` renders NO footprint row for such
        a connection. That is accepted rather than worked around -- a
        zero-byte model is a theoretical shape no live engine reports, and
        the cost of getting it wrong in the SCHEDULER (treating it as
        unknown and running it alone for ever) is real while the cost in
        the CONSOLE (one missing disclosure row) is not. Recorded so a
        later reader does not "fix" the ladder to match the renderer."""
        connection = self._conn(engine_reported_footprint_bytes=0)

        assert connection.effective_footprint_bytes == 0
        assert connection.footprint_source == "engine_reported"

    def test_the_reported_timestamp_rides_beside_the_bytes(self):
        at = datetime(2026, 3, 4, tzinfo=dt_timezone.utc)
        connection = self._conn(
            engine_reported_footprint_bytes=7, engine_reported_footprint_at=at,
        )
        connection.refresh_from_db()

        assert connection.engine_reported_footprint_at == at
```

Re-pin `models/registry/tests/test_db_bindings.py::TestFootprintPrecedence` — three assertions change vocabulary and one test is replaced outright, because `footprint_source`'s values are no longer the `_SOURCE_LABELS` keys:

```python
    def test_measured_only_is_measured_sourced(self):
        connection = ModelConnection.objects.create(
            name="measured", engine="ollama", endpoint="http://e:1", model_id="m",
            measured_footprint_bytes=123,
        )

        assert connection.effective_footprint_bytes == 123
        assert connection.footprint_source == "measured"

    def test_override_only_is_override_sourced(self):
        connection = ModelConnection.objects.create(
            name="overridden", engine="ollama", endpoint="http://e:1", model_id="m",
            footprint_override_bytes=456,
        )

        assert connection.effective_footprint_bytes == 456
        assert connection.footprint_source == "override"

    def test_override_wins_over_measured_when_both_set(self):
        """Owner ruling: the operator's declared value always wins,
        regardless of what was last measured."""
        connection = ModelConnection.objects.create(
            name="both set", engine="ollama", endpoint="http://e:1", model_id="m",
            measured_footprint_bytes=123,
            footprint_override_bytes=456,
        )

        assert connection.effective_footprint_bytes == 456
        assert connection.footprint_source == "override"

    def test_footprint_source_keys_match_the_footprint_label_vocabulary(self):
        """`footprint_source`'s three non-None values are exactly the keys
        of `_FOOTPRINT_SOURCE_LABELS` -- the footprint vocabulary, NOT
        `_SOURCE_LABELS`, which stays the capability/embed-dim disclosure's
        own map and is untouched by this track (spec §3.1).

        RE-PINNED by the queue memory-governance track: this used to
        assert `"connection"`/`"engine"` against `_SOURCE_LABELS`."""
        from models.registry.views import _FOOTPRINT_SOURCE_LABELS

        assert set(_FOOTPRINT_SOURCE_LABELS) >= {"override", "measured", "engine_reported"}
```

> The `_FOOTPRINT_SOURCE_LABELS` import above fails until Task 3. Write the assertion now and let it fail; Task 3's step 2 turns it green. Every other test in this task passes at the end of step 2 below.

- [ ] **Step 2: Implement**

In `models/registry/models.py`, after `footprint_override_bytes`, add the third rung's columns:

```python
    # THE THIRD RUNG (queue memory governance, 2026-09-21, ADR 0013's
    # dated amendment §4). The size an engine reports for a model it
    # says is LOADED, harvested from the residency snapshot the queue
    # worker is taking anyway (`models.queue.worker.Worker.
    # _residency_snapshot` reads `InstalledModel.loaded_size`) -- NEVER
    # a new HTTP call. It exists because those numbers were previously
    # read and thrown away, and because a model that has never completed
    # a run under this queue has no `measured_footprint_bytes` at all and
    # is therefore treated as unknown-footprint, i.e. exclusive, for ever.
    #
    # A SEPARATE COLUMN, not folded into `measured_footprint_bytes`: the
    # two are facts of different quality (ours-after-a-run vs the
    # engine's-while-loaded), the recorder rule compares a reading only
    # against its OWN standing value, and the console's label would be
    # untrue again the moment one column had to answer for both.
    engine_reported_footprint_bytes = models.BigIntegerField(null=True, blank=True)
    # When `engine_reported_footprint_bytes` was last written -- paired so
    # the console can date the reading, exactly as `measured_footprint_at`
    # dates its own.
    engine_reported_footprint_at = models.DateTimeField(null=True, blank=True)
```

Replace the two properties:

```python
    @property
    def effective_footprint_bytes(self) -> int | None:
        """The footprint the scheduler should reserve for this
        connection's model, on a FOUR-rung ladder (spec §3.1):
        `footprint_override_bytes` (the operator's word, always wins),
        else `measured_footprint_bytes` (what we observed after a run
        this queue executed), else `engine_reported_footprint_bytes`
        (what the engine said a loaded copy occupied, harvested from a
        residency snapshot), else `None` -- "unknown", which
        `models.registry.bindings.footprint_for` documents as "this job
        runs alone".

        Walks on `is not None`, never on truthiness: a genuine zero is a
        value, and reading it as "unknown" would silently make a
        zero-cost model exclusive.
        """
        if self.footprint_override_bytes is not None:
            return self.footprint_override_bytes
        if self.measured_footprint_bytes is not None:
            return self.measured_footprint_bytes
        return self.engine_reported_footprint_bytes

    @property
    def footprint_source(self) -> str | None:
        """Which rung backs `effective_footprint_bytes` -- one of
        `"override"` / `"measured"` / `"engine_reported"` / `None`, keyed
        EXACTLY to `models.registry.views._FOOTPRINT_SOURCE_LABELS`.

        NOT keyed to `_SOURCE_LABELS`, which is and stays the label map
        for `DiscoveryRow.capability_source` (where "detected from the
        model server" is true of a capability probe and would be a lie
        about a post-run delta measurement). The two vocabularies are
        deliberately separate -- see that module and spec §3.1.
        """
        if self.footprint_override_bytes is not None:
            return "override"
        if self.measured_footprint_bytes is not None:
            return "measured"
        if self.engine_reported_footprint_bytes is not None:
            return "engine_reported"
        return None
```

Generate migration 1:

```bash
.venv/bin/python manage.py makemigrations registry -n modelconnection_engine_reported_footprint
```

Expected: `Migrations for 'registry': models/registry/migrations/0009_modelconnection_engine_reported_footprint.py` naming two `AddField` operations and nothing else. If it names anything else, stop — another session's model edit is uncommitted in this worktree.

- [ ] **Step 3: Verify**

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
.venv/bin/pytest -q models/registry/tests/test_footprint_provenance.py models/registry/tests/test_db_bindings.py
```

Expected: every `TestTheFourRungLadder` test passes; `test_footprint_source_keys_match_the_footprint_label_vocabulary` fails on `ImportError: cannot import name '_FOOTPRINT_SOURCE_LABELS'` until Task 3. Everything else green.

- [ ] **Step 4: Commit**

```bash
git add models/registry/models.py models/registry/migrations/0009_modelconnection_engine_reported_footprint.py models/registry/tests/test_footprint_provenance.py models/registry/tests/test_db_bindings.py
git commit -m "feat(registry): a third footprint rung, reported by the engine

A model that has never completed a run under this queue has no measured
footprint, so the scheduler treats it as unknown and runs it alone -- for
ever. The residency snapshot the worker already takes carries a loaded
size for exactly those models, and it was being discarded.

effective_footprint_bytes becomes override ?? measured ?? engine_reported
?? None, on 'is not None' rather than truthiness so a real zero stays a
value. footprint_source answers four values instead of three.

Re-pins these existing tests by name:
models/registry/tests/test_db_bindings.py::TestFootprintPrecedence::
test_measured_only_is_engine_sourced (now
test_measured_only_is_measured_sourced),
::test_override_only_is_connection_sourced (now
test_override_only_is_override_sourced),
::test_override_wins_over_measured_when_both_set (vocabulary only), and
::test_footprint_source_keys_match_source_labels_exactly (now
test_footprint_source_keys_match_the_footprint_label_vocabulary -- the
footprint vocabulary is its own map, not the capability one)."
```

---

## Task 2: The recorder keeps the maximum

**Spec:** §3.2 (the rule, and the reasoning for having no tolerance band), §9.2, owner decision 3 (accepted: keep-the-maximum, zero tolerance).

**Files:**
- Modify: `models/registry/bindings.py` (`record_measured_footprint`, + `record_engine_reported_footprint`, + one shared private writer, + one constant, + a module-level logger)
- Modify: `models/registry/tests/test_footprint_provenance.py` (append)

**Interfaces:**
- Produces:
  - `record_measured_footprint(engine: str, endpoint: str, model_id: str, size_bytes: int) -> None` — unchanged signature, new refusal rule.
  - `record_engine_reported_footprint(engine: str, endpoint: str, model_id: str, size_bytes: int) -> None` — same contract, writes the rung-3 pair.
  - `FOOTPRINT_DIP_WARNING_RATIO: float = 0.75` — log level only; no reading below the standing value is ever written, at any ratio.
- Consumes: `ModelConnection`, `models.registry.discovery.norm_endpoint` (both already imported lazily inside the functions).

- [ ] **Step 1: Write the failing tests**

Append to `models/registry/tests/test_footprint_provenance.py` (and add the import line at the top of the module):

```python
from models.registry.bindings import (
    FOOTPRINT_DIP_WARNING_RATIO, record_engine_reported_footprint,
    record_measured_footprint,
)


@pytest.mark.django_db
class TestTheRecorderKeepsTheMaximum:
    """Live evidence, twice: a measurement taken while another model was
    resident, and one taken after a restart lost the residency memo, each
    LOWERED a standing footprint (26.4 -> 6.4 GB; 15.2 -> 11.1 GB). The
    next admission planned against the lowered number, evicted nothing,
    and the machine went down.

    No tolerance band, deliberately (spec §3.2): a percentage band
    against the STANDING value ratchets geometrically, and the condition
    is recurring rather than adversarial -- that model under-measures
    every time it is warm. Keeping the maximum makes the standing value a
    high-water mark by construction, with no extra column and no constant
    to tune."""

    def _conn(self, **kwargs) -> ModelConnection:
        return ModelConnection.objects.create(
            name="c", engine="ollama", endpoint="http://e:1/", model_id="m", **kwargs,
        )

    def test_a_first_reading_is_written(self):
        connection = self._conn()

        record_measured_footprint("ollama", "http://e:1", "m", 100)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 100
        assert connection.measured_footprint_at is not None

    def test_a_larger_reading_is_believed(self):
        connection = self._conn(measured_footprint_bytes=100)

        record_measured_footprint("ollama", "http://e:1", "m", 250)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 250

    def test_an_equal_reading_is_written_and_refreshes_the_timestamp(self):
        connection = self._conn(measured_footprint_bytes=100)

        record_measured_footprint("ollama", "http://e:1", "m", 100)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 100
        assert connection.measured_footprint_at is not None

    def test_a_lower_reading_writes_nothing_at_all(self):
        """Not the bytes, and not the timestamp: a refused reading must
        not leave a fresher date standing beside an older number, which
        would read on the console as "we measured this recently"."""
        at = datetime(2026, 3, 4, tzinfo=dt_timezone.utc)
        connection = self._conn(measured_footprint_bytes=100, measured_footprint_at=at)

        record_measured_footprint("ollama", "http://e:1", "m", 40)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 100
        assert connection.measured_footprint_at == at

    def test_three_successive_08x_readings_do_not_walk_the_standing_value_down(self):
        """The review's own pinned test (round 1, M5), adopted verbatim:
        this is the exact shape a tolerance band would have permitted."""
        connection = self._conn(measured_footprint_bytes=1000)

        for reading in (800, 640, 512):
            record_measured_footprint("ollama", "http://e:1", "m", reading)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 1000

    def test_a_badly_lower_reading_warns_and_names_the_override(self, caplog):
        connection = self._conn(measured_footprint_bytes=1000)

        with caplog.at_level("INFO", logger="models.registry.bindings"):
            record_measured_footprint("ollama", "http://e:1", "m", 100)

        line = "".join(record.getMessage() for record in caplog.records)
        assert "1000" in line and "100" in line
        assert str(connection.pk) in line
        assert "override" in line
        assert any(record.levelname == "WARNING" for record in caplog.records)

    def test_a_small_dip_is_informational_not_a_warning(self, caplog):
        self._conn(measured_footprint_bytes=1000)
        just_above = int(1000 * FOOTPRINT_DIP_WARNING_RATIO) + 1

        with caplog.at_level("INFO", logger="models.registry.bindings"):
            record_measured_footprint("ollama", "http://e:1", "m", just_above)

        assert caplog.records
        assert all(record.levelname == "INFO" for record in caplog.records)

    def test_the_engine_reported_column_obeys_the_same_rule(self):
        connection = self._conn(engine_reported_footprint_bytes=1000)

        record_engine_reported_footprint("ollama", "http://e:1", "m", 400)
        record_engine_reported_footprint("ollama", "http://e:1", "m", 1500)

        connection.refresh_from_db()
        assert connection.engine_reported_footprint_bytes == 1500

    def test_the_two_columns_do_not_compare_against_each_other(self):
        """A rung-3 reading is measured against rung 3's own standing
        value, never against rung 2's -- they are facts of different
        quality and a cross-rung comparison would refuse honest writes."""
        connection = self._conn(measured_footprint_bytes=1000)

        record_engine_reported_footprint("ollama", "http://e:1", "m", 40)

        connection.refresh_from_db()
        assert connection.engine_reported_footprint_bytes == 40
        assert connection.measured_footprint_bytes == 1000

    def test_an_ambiguous_ref_is_still_a_silent_no_op(self):
        self._conn()
        ModelConnection.objects.create(
            name="twin", engine="ollama", endpoint="http://e:1", model_id="m",
        )

        record_measured_footprint("ollama", "http://e:1", "m", 500)

        assert not ModelConnection.objects.filter(
            measured_footprint_bytes__isnull=False).exists()
```

- [ ] **Step 2: Implement**

In `models/registry/bindings.py`, add near the top (after the existing imports, before the re-export block):

```python
import logging

logger = logging.getLogger(__name__)

# Below this ratio, a refused lowering is an OPERATOR-ACTIONABLE event
# (WARNING) rather than a note (INFO): a reading under three quarters of
# the standing value is the shape both live incidents took (26.4 -> 6.4
# GB, 15.2 -> 11.1 GB), and the operator's remedy -- set
# `footprint_override_bytes` -- is named in the line itself. A smaller dip
# is ordinary measurement noise and is recorded at INFO, so the refusal is
# still visible without teaching an operator to ignore warnings.
#
# A THRESHOLD FOR LOG LEVEL ONLY. No reading below the standing value is
# ever written, at any ratio: a tolerance band measured against the
# standing value ratchets geometrically and reproduces the exact incident
# this rule exists to refuse (spec §3.2).
FOOTPRINT_DIP_WARNING_RATIO = 0.75
```

Replace `record_measured_footprint`'s body with a thin wrapper over one shared writer, and add its rung-3 twin:

```python
def record_measured_footprint(engine: str, endpoint: str, model_id: str, size_bytes: int) -> None:
    """Persist `size_bytes` as the MEASURED footprint (rung 2) for the ONE
    `ModelConnection` matching `(engine, endpoint, model_id)` -- see the
    module docstring for why this uses `footprint_for`'s exact match rule,
    and `_record_footprint` below for the keep-the-maximum rule every
    write now obeys.
    """
    _record_footprint(
        engine, endpoint, model_id, size_bytes,
        bytes_field="measured_footprint_bytes", at_field="measured_footprint_at",
    )


def record_engine_reported_footprint(
    engine: str, endpoint: str, model_id: str, size_bytes: int,
) -> None:
    """Persist `size_bytes` as the ENGINE-REPORTED footprint (rung 3, spec
    §3.1) for the ONE matching `ModelConnection`.

    Called by the queue worker from inside the residency snapshot it is
    taking anyway (`models.queue.worker.Worker._residency_snapshot`) --
    never from a call made for this purpose. Obeys exactly the same
    opportunistic contract and the same keep-the-maximum rule as its
    rung-2 twin above, measured against rung 3's OWN standing value: the
    two columns are facts of different quality and never compare against
    each other.
    """
    _record_footprint(
        engine, endpoint, model_id, size_bytes,
        bytes_field="engine_reported_footprint_bytes",
        at_field="engine_reported_footprint_at",
    )


def _record_footprint(
    engine: str, endpoint: str, model_id: str, size_bytes: int, *,
    bytes_field: str, at_field: str,
) -> None:
    """THE ONE WRITER behind both recorders above.

    KEEP THE MAXIMUM (Q9, spec §3.2). No standing value -> write. A
    reading GREATER THAN OR EQUAL to the standing value -> write (a
    footprint going up is always believed; under-counting is the
    direction that crashes hosts). A reading BELOW it -> refuse
    entirely: not the bytes, not the timestamp, and one line records the
    connection, the standing value and the refused value.

    There is deliberately NO tolerance band. A percentage tolerance
    measured against the standing value ratchets geometrically -- five
    successive "within tolerance" 0.75x writes walk 26.4 GB down to 6.2
    GB and reproduce the exact shape this rule exists to refuse -- and
    the Q9 condition is RECURRING (a model that under-measures whenever
    it is warm), so repeated warm runs are the normal path, not an
    adversarial one. Keeping the maximum makes the standing value a
    high-water mark by construction, with no extra column and no constant
    to tune. The accepted cost -- a genuinely shrunk model stays high
    until an operator sets an override -- is the safe direction and is
    already the documented correction path.

    Opportunistic and silent-on-ambiguity in every other respect, exactly
    as before: never raises, no-ops on no match or an ambiguous match,
    tolerates a `ProgrammingError`/`OperationalError` from either the
    SELECT or the write, and tells no caller whether the write landed.
    """
    from django.db import OperationalError, ProgrammingError
    from django.utils import timezone

    from models.registry.discovery import norm_endpoint
    from models.registry.models import ModelConnection

    try:
        candidates = list(ModelConnection.objects.filter(engine=engine, model_id=model_id))
    except (ProgrammingError, OperationalError):
        return

    endpoint_key = norm_endpoint(endpoint)
    matches = [c for c in candidates if norm_endpoint(c.endpoint) == endpoint_key]
    if len(matches) != 1:
        return

    connection = matches[0]
    standing = getattr(connection, bytes_field)
    if standing is not None and size_bytes < standing:
        ratio = size_bytes / standing if standing else 1.0
        message = (
            "registry: refused to lower %s on connection %s (%r): standing %s bytes, "
            "refused reading %s bytes. A footprint is kept at its high-water mark; set "
            "footprint_override_bytes on this connection if the model really did get smaller"
        )
        args = (bytes_field, connection.pk, connection.name, standing, size_bytes)
        if ratio < FOOTPRINT_DIP_WARNING_RATIO:
            logger.warning(message, *args)
        else:
            logger.info(message, *args)
        return

    setattr(connection, bytes_field, size_bytes)
    setattr(connection, at_field, timezone.now())
    try:
        connection.save(update_fields=[bytes_field, at_field])
    except (ProgrammingError, OperationalError):
        return
```

Update the module docstring's `record_measured_footprint()` paragraph to name the keep-the-maximum rule and its rung-3 twin.

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/registry/tests/test_footprint_provenance.py models/registry/tests/test_queue_seam.py models/registry/tests/test_bindings.py
```

Expected: green, except the still-unwritten `_FOOTPRINT_SOURCE_LABELS` assertion from Task 1.

- [ ] **Step 4: Commit**

```bash
git add models/registry/bindings.py models/registry/tests/test_footprint_provenance.py
git commit -m "feat(registry): a footprint is kept at its high-water mark

Twice in the field a measurement taken while another model was resident
lowered a standing footprint; the next admission planned against the
lowered number, evicted nothing, and the host went down.

The recorder now refuses any reading below the standing value outright --
neither bytes nor timestamp -- and logs the refusal with both numbers,
WARNING below FOOTPRINT_DIP_WARNING_RATIO (naming the override as the
correction path), INFO above it. No tolerance band: measured against the
standing value a band ratchets geometrically and reproduces the incident
it exists to refuse. record_engine_reported_footprint is the rung-3 twin,
sharing the rule and comparing only against its own column."
```

---

## Task 3: The footprint label vocabulary, and the template that renders it

**Spec:** §3.1 ("A separate vocabulary, not the capability one"; "Named template edit"; the rung-3 label paragraph added by the 2026-09-21 steward amendment, finding S-2).

**Files:**
- Modify: `models/registry/views.py` (+ `_FOOTPRINT_SOURCE_LABELS`, `_footprint_source_label`, `_footprint_source_at`; `_connection_views`'s dict; `_build_context` drops the raw map)
- Modify: `models/registry/templates/inference/_registered_connection.html` (the footprint block and its `{% comment %}`)
- Modify: `models/registry/tests/test_views_tables_and_picker.py` (footprint assertions only)
- Modify: `models/registry/tests/test_views_connection_edit.py` (**only if** it asserts footprint strings — see step 1)
- Modify: `models/registry/tests/test_footprint_provenance.py` (append)

**Interfaces:**
- Produces:
  - `models.registry.views._FOOTPRINT_SOURCE_LABELS: dict[str, str]` = `{"override": "set by the operator", "measured": "measured after a run", "engine_reported": "from the engine's residency snapshot", "": "not measured yet — this model runs alone"}`
  - `_footprint_source_label(source: str | None, at) -> str` — the vocabulary string plus `" on <date>"` when the rung carries a timestamp.
  - Per-connection view keys: `footprint_display`, `footprint_source_label`, `measured_footprint_display`, `measured_source_label`.
- Consumes: `ModelConnection.footprint_source` / `.effective_footprint_bytes` (Task 1).

**The rung-3 wording is the steward's, and it is load-bearing:** "from the engine's residency snapshot on *date*", **not** "reported by the model server". The image engine reports no per-model residency or size at all (`list_installed` carries `size=None` by design) — its loaded size is the adapter's own post-run delta out of the same TTL'd memo, so the stronger phrasing would be untrue there. One label has to be honest on both engines; this is it.

**`_SOURCE_LABELS` is not touched.** It is the label map for `DiscoveryRow.capability_source`, where "detected from the model server" is true of a capability probe. Every capability-source assertion in the edited test modules keeps its current wording — check that explicitly before committing.

- [ ] **Step 1: Write the failing tests**

First, find the footprint assertions to re-pin (capability-source assertions must NOT be in this list):

```bash
grep -rn "dt>Memory footprint</dt>\|dt>Last measured</dt>" models/registry/tests/
```

**Grep the rendered `<dt>`, not the bare words.** `grep -rn "Memory footprint\|Last measured"` returns about twenty hits, most of them **override-validation error strings** ("Memory footprint override must be a number of GB.") in `test_views_reencode_and_sections.py` and `test_views_connection_edit.py` — none of which this task touches.

Expected today, all eight in `test_views_tables_and_picker.py` and nowhere else: four **absence** assertions (`"<dt>Last measured</dt>" not in …`, `"<dt>Memory footprint</dt>" not in …`), which stay byte-identical, and four **value** assertions, which are the ones re-pinned below — `8.5 GB — manual` twice, and `4.7 GB — detected from the model server on March 4, 2026` as "Memory footprint" and again as "Last measured". **`test_views_connection_edit.py` carries no footprint-disclosure assertion, so this task does not edit it** — the File Structure table says the same.

Re-pin them to the new vocabulary, leaving the surrounding test bodies byte-identical:

```python
        assert "<dt>Memory footprint</dt><dd>8.5 GB — set by the operator</dd>" in disclosure_html
```

```python
        assert (
            "<dt>Memory footprint</dt><dd>4.7 GB — measured after a run "
            "on March 4, 2026</dd>" in disclosure_html
        )
```

```python
        assert (
            "<dt>Last measured</dt><dd>4.7 GB — measured after a run "
            "on March 4, 2026</dd>" in disclosure_html
        )
```

Append to `models/registry/tests/test_footprint_provenance.py`:

```python
@pytest.mark.django_db
class TestTheFootprintLabelVocabulary:
    """The footprint labels are their OWN map. `_SOURCE_LABELS` keeps
    labelling `DiscoveryRow.capability_source`, where "detected from the
    model server" is true; reusing it for a post-run delta measurement was
    the one place the console said something untrue (Q1)."""

    def test_every_footprint_source_value_has_a_label(self):
        from models.registry.views import _FOOTPRINT_SOURCE_LABELS

        assert _FOOTPRINT_SOURCE_LABELS["override"] == "set by the operator"
        assert _FOOTPRINT_SOURCE_LABELS["measured"] == "measured after a run"
        assert (
            _FOOTPRINT_SOURCE_LABELS["engine_reported"]
            == "from the engine's residency snapshot"
        )

    def test_the_capability_vocabulary_is_untouched(self):
        """A shipped, unrelated disclosure: relabelling it here would
        change what the console says about capability detection."""
        from models.registry.views import _SOURCE_LABELS

        assert _SOURCE_LABELS["engine"] == "detected from the model server"
        assert _SOURCE_LABELS["connection"] == "manual"
        assert "override" not in _SOURCE_LABELS

    def test_the_two_maps_are_not_the_same_object(self):
        from models.registry.views import _FOOTPRINT_SOURCE_LABELS, _SOURCE_LABELS

        assert _FOOTPRINT_SOURCE_LABELS is not _SOURCE_LABELS
```

And one end-to-end rendering test for the rung the console has never shown before (the live proof later mirrors it). Copy this module's `client` / `_get` / `clear_seeded_rows` / `clean_probe_cache` / `ENDPOINT` scaffolding from `models/registry/tests/test_views_tables_and_picker.py`'s own header — the six-module split means each module carries its own copy rather than importing another's:

```python
@pytest.mark.django_db
class TestTheEngineReportedRowRenders:
    def test_an_engine_reported_only_connection_says_where_the_number_came_from(self, client):
        at = datetime(2026, 3, 4, tzinfo=dt_timezone.utc)
        connection = ModelConnection.objects.create(
            name="reported conn", engine="ollama", endpoint=ENDPOINT,
            model_id="a-chat-model", capabilities=["chat"],
            engine_reported_footprint_bytes=round(4.7 * 1024**3),
            engine_reported_footprint_at=at,
        )

        body = self._get(client).content.decode()
        start = body.index(f'id="conn-{connection.pk}"')
        disclosure_html = body[start:body.index("</details>", start)]

        assert (
            "<dt>Memory footprint</dt><dd>4.7 GB — from the engine's residency "
            "snapshot on March 4, 2026</dd>" in disclosure_html
        )
```

- [ ] **Step 2: Implement**

In `models/registry/views.py`, immediately after `_SOURCE_LABELS`:

```python
# Operator-facing labels for `ModelConnection.footprint_source` -- a
# SEPARATE vocabulary from `_SOURCE_LABELS` above, deliberately (spec
# §3.1). `_SOURCE_LABELS`'s first job is labelling
# `DiscoveryRow.capability_source`, where "detected from the model
# server" is TRUE: a capability really was read off the engine. A
# footprint's rung-2 value is OUR OWN post-run delta observation, not the
# engine's declaration, so reusing that phrase for it was the one place
# this console said something untrue -- and retiring the phrase where it
# IS true would relabel a shipped, unrelated disclosure.
#
# Rung 3 reads "from the engine's residency snapshot", NOT "reported by
# the model server" (engine steward's amendment, 2026-09-21): one of the
# two live engines reports no per-model residency or size at all -- its
# loaded size is the adapter's own post-run delta out of a TTL'd memo --
# so the stronger phrasing would be untrue there. This one is honest on
# both.
#
# The empty-string key is the "no rung answered" case, kept in the map
# rather than as a template `{% if %}` so every state this vocabulary can
# be in is declared in one place.
_FOOTPRINT_SOURCE_LABELS = {
    "override": "set by the operator",
    "measured": "measured after a run",
    "engine_reported": "from the engine's residency snapshot",
    "": "not measured yet — this model runs alone",
}


def _footprint_source_label(source: str | None, at) -> str:
    """One resolved label for a footprint rung: the vocabulary string
    above, plus " on <date>" for the two rungs that carry a timestamp.

    Resolved HERE, in the view, rather than in the template, so the label
    and the fact can no longer disagree: the template used to render
    `footprint_source_labels.connection`/`.engine` directly off a raw map,
    keyed by hand, beside a value it had chosen with its own `{% if %}` --
    two independent decisions about the same row (spec §3.1's named
    template edit).
    """
    label = _FOOTPRINT_SOURCE_LABELS.get(source or "", _FOOTPRINT_SOURCE_LABELS[""])
    if at is None:
        return label
    # `timezone.localtime` FIRST, and it is not optional: the template's
    # `{{ value|date:"N j, Y" }}` localizes to `settings.TIME_ZONE` before
    # formatting and `date_format` does not. `config/settings.py` reads
    # `TIME_ZONE` from the environment with `USE_TZ = True`, so formatting
    # the raw UTC value would silently move the rendered DATE by a day on
    # any non-UTC box -- while the shipped assertions, and the operator,
    # expect the date the template has always rendered.
    local_at = timezone.localtime(at) if timezone.is_aware(at) else at
    return f"{label} on {date_format(local_at, 'N j, Y')}"


def _footprint_source_at(connection: ModelConnection):
    """The timestamp belonging to whichever rung `footprint_source` named
    -- `None` for the operator's override (a declaration, not an
    observation, and it carries no date) and for the unknown case."""
    source = connection.footprint_source
    if source == "measured":
        return connection.measured_footprint_at
    if source == "engine_reported":
        return connection.engine_reported_footprint_at
    return None
```

Add `from django.utils.formats import date_format` to this module's imports (check first that no different `date_format` is already imported here), and `from django.utils import timezone` if it is not already there.

> **Pin the timezone behaviour, not just the string.** Add one test to `test_footprint_provenance.py` that renders a connection whose timestamp is late-evening UTC with `settings.TIME_ZONE` set to a zone several hours ahead, and asserts the LOCAL date — the date the template's `|date` filter produced before this change. Without it, a regression back to bare `date_format` passes every other assertion in this task.

In `_connection_views`'s returned dict, replace the two display keys with four:

```python
            # The facts <dl>, rebuilt by the queue memory-governance track
            # (spec §3.1): ONE resolved label per row, from the footprint
            # vocabulary, so the template no longer picks a fact and a
            # label independently of each other.
            "footprint_display": _human_size(connection.effective_footprint_bytes),
            "footprint_source_label": _footprint_source_label(
                connection.footprint_source, _footprint_source_at(connection),
            ),
            "measured_footprint_display": _human_size(connection.measured_footprint_bytes),
            "measured_source_label": _footprint_source_label(
                "measured", connection.measured_footprint_at,
            ),
```

Remove `"footprint_source_labels": _SOURCE_LABELS` from `_build_context`'s dict — the template no longer reads it, and leaving it would be a second, unused path to the wrong vocabulary.

In `models/registry/templates/inference/_registered_connection.html`, replace the footprint block:

```html
          {% if item.footprint_display %}
          <dt>Memory footprint</dt><dd>{{ item.footprint_display }} — {{ item.footprint_source_label }}</dd>
          {% if item.connection.footprint_override_bytes and item.connection.measured_footprint_bytes %}
          <dt>Last measured</dt><dd>{{ item.measured_footprint_display }} — {{ item.measured_source_label }}</dd>
          {% endif %}
          {% endif %}
```

and rewrite that block's paragraph in the file's `{% comment %}` header to describe the one resolved label (`models/registry/tests/test_template_comments.py` requires the comment — it is a registry-owned gate, not a `foundation/ops` one; a stale comment is worse than none).

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/registry/tests/
```

Expected: green, including the previously-failing `test_footprint_source_keys_match_the_footprint_label_vocabulary` from Task 1. If a capability-source assertion fails, the edit went into the wrong map — revert and re-read `_SOURCE_LABELS`'s own comment.

- [ ] **Step 4: Commit**

```bash
git add models/registry/views.py models/registry/templates/inference/_registered_connection.html models/registry/tests/
git commit -m "feat(registry): footprints get their own label vocabulary

The console labelled a post-run delta measurement 'detected from the model
server', which is our observation of the engine, not the engine's
declaration -- and the map it borrowed that phrase from is the
capability/embed-dim disclosure's, where the phrase is true. A separate
_FOOTPRINT_SOURCE_LABELS sits beside it; _SOURCE_LABELS is untouched.

Rung 3 reads 'from the engine's residency snapshot', per the engine
steward's check: one live engine reports no per-model residency at all, so
the stronger wording would be untrue there.

The template rendered a label and a fact from two independent decisions; it
now renders one item.footprint_source_label resolved in the view.

Re-pins these existing tests by name:
models/registry/tests/test_views_tables_and_picker.py's three footprint
disclosure assertions (override-only, measured-only, override-and-measured)
-- the footprint strings only; every capability-source assertion in that
module keeps its current wording."
```

---

## Task 4: `registered_endpoints()`, and the console helper refactored onto it

**Spec:** §3.3(e) (the seam, the union with the configured defaults, the console-helper refactor, the tolerance idiom).

**Files:**
- Modify: `models/registry/bindings.py` (+ `registered_endpoints`)
- Modify: `models/registry/views.py` (`_engine_endpoints` delegates)
- Modify: `models/registry/tests/test_footprint_provenance.py` (append)

**Interfaces:**
- Produces:
  ```python
  def registered_endpoints(
      connections: "list[ModelConnection] | None" = None,
  ) -> list[tuple[str, str, tuple[str, ...]]]
  ```
  `(engine, normalized endpoint, model_ids registered there)`, one entry per distinct `(engine, normalized endpoint)`, unioning `settings.INFERENCE_DEFAULT_ENDPOINTS` with every connection row's endpoint. A missing table degrades to the configured defaults alone. `connections=None` fetches; a supplied list costs **no** query.
- Consumes: `django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS`, `norm_endpoint`, `ModelConnection`.

**Why the optional argument exists (author decision, recorded):** the console page already holds its connections list and is pinned at 10 queries by two separate modules. A `registered_endpoints()` that always queried would add an eleventh on every console render. Passing the already-fetched list keeps ONE implementation of "every engine endpoint" — which is the point of the spec's "refactored to share this one implementation" — while leaving the console's pins untouched.

**The third element is load-bearing:** it is what makes a *foreign* endpoint addressable by a precautionary barrier call (§3.3d(3)). An endpoint with no connection row yields an empty tuple, and §11 names that as a residual — do **not** invent a synthetic model id for it.

- [ ] **Step 1: Write the failing tests**

Append to `models/registry/tests/test_footprint_provenance.py`:

```python
from models.registry.bindings import registered_endpoints


@pytest.mark.django_db
class TestRegisteredEndpoints:
    """The endpoint set the queue's cross-engine sweep walks. Derived
    from connection rows ALONE it would miss a freshly-installed second
    engine running at its configured address with no registered
    connection yet -- which is precisely the Q4 scenario (a model left
    warm on an engine nothing is currently using)."""

    def test_the_configured_defaults_are_present_with_no_rows_at_all(self, settings):
        settings.INFERENCE_DEFAULT_ENDPOINTS = {"ollama": "http://host:1/"}

        assert ("ollama", "http://host:1", ()) in registered_endpoints()

    def test_a_connection_row_contributes_its_endpoint_and_model_id(self, settings):
        settings.INFERENCE_DEFAULT_ENDPOINTS = {}
        ModelConnection.objects.create(
            name="c", engine="ollama", endpoint="http://other:2/", model_id="m",
        )

        assert ("ollama", "http://other:2", ("m",)) in registered_endpoints()

    def test_one_entry_per_normalized_endpoint_carrying_every_model_id(self, settings):
        settings.INFERENCE_DEFAULT_ENDPOINTS = {"ollama": "http://host:1"}
        for model_id in ("a", "b"):
            ModelConnection.objects.create(
                name=model_id, engine="ollama", endpoint="http://host:1/", model_id=model_id,
            )

        entries = [e for e in registered_endpoints() if e[1] == "http://host:1"]

        assert len(entries) == 1
        assert set(entries[0][2]) == {"a", "b"}

    def test_two_engines_at_one_address_stay_two_entries(self, settings):
        settings.INFERENCE_DEFAULT_ENDPOINTS = {
            "ollama": "http://host:1", "comfyui": "http://host:1",
        }

        engines = {engine for engine, _endpoint, _ids in registered_endpoints()}

        assert {"ollama", "comfyui"} <= engines

    def test_a_missing_table_degrades_to_the_configured_defaults(self, settings, monkeypatch):
        from django.db import ProgrammingError

        settings.INFERENCE_DEFAULT_ENDPOINTS = {"ollama": "http://host:1"}
        monkeypatch.setattr(
            ModelConnection.objects, "all",
            lambda: (_ for _ in ()).throw(ProgrammingError("no such table")),
        )

        assert registered_endpoints() == [("ollama", "http://host:1", ())]

    def test_a_supplied_connection_list_is_used_without_a_query(
            self, settings, django_assert_num_queries):
        settings.INFERENCE_DEFAULT_ENDPOINTS = {}
        rows = [ModelConnection(engine="ollama", endpoint="http://x:9", model_id="m")]

        with django_assert_num_queries(0):
            entries = registered_endpoints(connections=rows)

        assert entries == [("ollama", "http://x:9", ("m",))]


@pytest.mark.django_db
class TestTheConsoleStillPaysNoExtraQuery:
    """`_engine_endpoints` now delegates to `registered_endpoints`, which
    CAN fetch -- the console hands it the rows it already holds, so the
    page's pinned query count is unchanged. The two existing pins
    (`test_views_machine_add_and_dropdowns.py::TestConsoleViewQueryCount`
    and `test_console_override_query_count.py`) remain the authority on the
    number; this pins the delegation path itself."""

    def test_engine_endpoints_asks_for_no_connections_of_its_own(
            self, django_assert_num_queries):
        from models.registry.views import _engine_endpoints

        rows = [ModelConnection(engine="ollama", endpoint="http://x:9", model_id="m")]
        with django_assert_num_queries(0):
            _engine_endpoints(rows, "http://viewed:1")
```

- [ ] **Step 2: Implement**

In `models/registry/bindings.py`:

```python
def registered_endpoints(
    connections: "list[ModelConnection] | None" = None,
) -> list[tuple[str, str, tuple[str, ...]]]:
    """Every engine endpoint this box knows about:
    `(engine, normalized endpoint, model_ids registered there)`.

    THE ONE NOTION OF "every engine endpoint" IN THIS REPOSITORY. The
    execution queue's cross-engine eviction sweep
    (`models.queue.worker.Worker._evict_to_match_plan`, spec §3.3e) and
    the console's own per-engine discovery map
    (`models.registry.views._engine_endpoints`) both read it, so a
    freshly-installed engine cannot be visible to one and invisible to
    the other.

    THE UNION MATTERS: the configured default endpoints
    (`settings.INFERENCE_DEFAULT_ENDPOINTS`) PLUS every connection row's
    endpoint, per engine. Deriving the set from connection rows alone
    would miss a second engine running at its configured address with no
    registered connection yet -- exactly the case the queue's sweep exists
    for.

    THE THIRD ELEMENT IS NOT DECORATION. A barrier call at a FOREIGN
    endpoint has to be addressed with SOME `model_id` (the unload seam's
    signature takes one), and a configured endpoint with no connection row
    yields an empty tuple -- an endpoint the barrier therefore cannot
    address at all. That is a named residual (spec §11), never something
    to paper over with a synthetic id.

    `connections` is the caller's OWN already-fetched rows, threaded in
    rather than re-fetched -- the same "read it once per unit of work"
    shape `claim_and_admit`'s `settings_row` uses. The console page holds
    its rows already and is pinned at a fixed query count; fetching here
    unconditionally would add a query to every render. `None` fetches,
    tolerating a `ProgrammingError`/`OperationalError` from the table not
    existing yet (mid-`migrate`) by falling back to the configured
    defaults alone -- the tolerance idiom `_bound_connection` and
    `footprint_for` already use.
    """
    from django.conf import settings

    from models.registry.discovery import norm_endpoint
    from models.registry.models import ModelConnection

    if connections is None:
        from django.db import OperationalError, ProgrammingError

        try:
            connections = list(ModelConnection.objects.all())
        except (ProgrammingError, OperationalError):
            connections = []

    by_key: dict[tuple[str, str], list[str]] = {}

    def _add(engine: str, endpoint: str, model_id: str = "") -> None:
        if not engine or not endpoint:
            return
        bucket = by_key.setdefault((engine, norm_endpoint(endpoint)), [])
        if model_id and model_id not in bucket:
            bucket.append(model_id)

    for engine_name, default in settings.INFERENCE_DEFAULT_ENDPOINTS.items():
        _add(engine_name, default)
    for connection in connections:
        _add(connection.engine, connection.endpoint, connection.model_id)

    return [
        (engine, endpoint, tuple(model_ids))
        for (engine, endpoint), model_ids in by_key.items()
    ]
```

In `models/registry/views.py`, `_engine_endpoints` keeps its signature and its "add the viewed endpoint for every registered engine" behaviour, and stops re-implementing the union:

```python
    engine_map: dict[str, list[str]] = {}

    def _add(engine_name: str, candidate: str) -> None:
        if not candidate:
            return
        bucket = engine_map.setdefault(engine_name, [])
        if all(norm_endpoint(candidate) != norm_endpoint(seen) for seen in bucket):
            bucket.append(candidate)

    # The defaults-union-connections half is `models.registry.bindings.
    # registered_endpoints` -- ONE implementation, shared with the
    # execution queue's cross-engine sweep (spec §3.3e). The rows this
    # page already holds are handed straight in, so this costs no query.
    for engine_name, endpoint_value, _model_ids in registered_endpoints(connections=connections):
        _add(engine_name, endpoint_value)
    # Still this page's own behaviour, and not the queue's: the address
    # currently being VIEWED is polled for every REGISTERED engine, which
    # preserves the pre-D11 superset an operator's `?endpoint=` override
    # depends on.
    for engine in ENGINES.values():
        _add(engine.name, endpoint)

    return engine_map
```

Update that function's docstring to say the union now comes from `registered_endpoints`, and add the import.

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/registry/tests/
```

Expected: green, with both console query-count modules still at 10. A number that moved means the delegation is fetching — pass `connections=` through.

- [ ] **Step 4: Commit**

```bash
git add models/registry/bindings.py models/registry/views.py models/registry/tests/test_footprint_provenance.py
git commit -m "feat(registry): one notion of every engine endpoint

registered_endpoints() unions the configured default endpoints with every
connection row's, per engine, and carries the model ids registered at each
-- the third element is what makes a foreign endpoint addressable at all by
the queue's barrier, since the unload seam takes a model_id.

The console's own _engine_endpoints kept a second copy of that union; it now
delegates, passing the rows the page already holds so its pinned query count
is unchanged."
```

---

## Task 5: A heartbeat that does not share a thread with the work

**Spec:** §3.4(a). **This task must land before Task 12** — the phase the wide sweep lengthens (`_residency_snapshot`) contains no heartbeat call at all, and each `list_installed` can cost a full discovery timeout.

**Files:**
- Modify: `models/queue/worker.py` (`__init__`, `run_forever`, `_maybe_heartbeat`, `_shutdown`, + `_heartbeat_forever`)
- Modify: `models/queue/tests/test_worker.py` (`TestHeartbeat`, append)

**Interfaces:**
- Produces: `Worker._heartbeat_forever() -> None` (daemon thread body); `Worker._heartbeat_thread: threading.Thread | None`.
- Consumes: the existing `Worker._maybe_heartbeat` (still the ONE writer of `heartbeat_at`), `django.db.close_old_connections`.

**The connection story is the difference between fixing Q8 and re-creating it.** Django connections are thread-local and nothing else would ever close or health-check this thread's own; a write error must not kill the thread; and a thread that exits must say so loudly, because a silently dead heartbeat writer mass-orphans healthy work after a database restart.

**Started by `run_forever`, never by the constructor** — a single-tick diagnostic run (`manage.py run_jobs --once`) and the whole test suite construct `Worker()` and must spawn nothing.

- [ ] **Step 1: Write the failing tests**

Append to `models/queue/tests/test_worker.py`, inside `TestHeartbeat`:

```python
    def test_constructing_a_worker_starts_no_thread(self, worker):
        """`--once` and every test in this suite construct a Worker; none
        of them may leak a daemon thread."""
        assert worker._heartbeat_thread is None

    @pytest.mark.django_db(transaction=True)
    def test_the_thread_refreshes_a_row_while_the_tick_thread_is_blocked(self, worker, monkeypatch):
        """The Q8 shape: the tick thread is stuck inside a synchronous
        eviction pass (or a starved process during a long cold load) and
        writes nothing, and the sweep orphans a healthy job. The dedicated
        thread is what keeps that row alive."""
        monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
        job = _job(state=RUNNING)
        token = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(
            claim_token=token, heartbeat_at=timezone.now() - timedelta(seconds=60),
        )
        with worker._active_lock:
            worker._active_tokens[job.pk] = token

        worker._start_heartbeat_thread()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job.refresh_from_db()
                if job.heartbeat_at > timezone.now() - timedelta(seconds=5):
                    break
                time.sleep(0.05)
        finally:
            worker._stopping.set()
            worker._heartbeat_thread.join(timeout=5)

        job.refresh_from_db()
        assert job.heartbeat_at > timezone.now() - timedelta(seconds=5)

    def test_the_thread_closes_its_own_connections_each_iteration(self, worker, monkeypatch):
        """Nothing else ever closes or health-checks this thread's own
        connection -- Django's are thread-local."""
        calls = []
        monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
        monkeypatch.setattr(worker_module, "close_old_connections", lambda: calls.append(1))

        worker._start_heartbeat_thread()
        time.sleep(0.2)
        worker._stopping.set()
        worker._heartbeat_thread.join(timeout=5)

        assert calls

    def test_a_transient_write_error_does_not_kill_the_thread(self, worker, monkeypatch, caplog):
        monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
        failures = {"count": 0}

        def _boom():
            failures["count"] += 1
            if failures["count"] <= 2:
                raise OperationalError("connection lost")

        monkeypatch.setattr(worker, "_maybe_heartbeat", _boom)

        with caplog.at_level("WARNING", logger="models.queue.worker"):
            worker._start_heartbeat_thread()
            time.sleep(0.3)
            worker._stopping.set()
            worker._heartbeat_thread.join(timeout=5)

        assert failures["count"] > 2, "the thread stopped at the first error"
        assert any("heartbeat" in r.getMessage() for r in caplog.records)

    def test_the_thread_says_so_loudly_if_it_ever_exits(self, worker, monkeypatch, caplog):
        monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)

        with caplog.at_level("INFO", logger="models.queue.worker"):
            worker._start_heartbeat_thread()
            time.sleep(0.15)
            worker._stopping.set()
            worker._heartbeat_thread.join(timeout=5)

        assert any("heartbeat thread" in r.getMessage() for r in caplog.records)

    @pytest.mark.django_db(transaction=True)
    def test_the_throttle_is_read_and_written_under_the_lock(self, worker):
        """BEHAVIOUR, not source order. An earlier draft asserted that
        `with self._active_lock` appeared before `_last_heartbeat_
        monotonic` in the method's SOURCE -- which this same step's
        instruction to name that attribute in the docstring would have
        flipped, since a docstring IS part of the source.

        Instead: hold the lock from this thread, and prove the writer
        cannot get past its own throttle read while it is held. If the
        read-modify-write sat outside the lock, the call would return
        having written, and the two threads could interleave it."""
        job = _job(state=RUNNING)
        token = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(claim_token=token, heartbeat_at=None)
        with worker._active_lock:
            worker._active_tokens[job.pk] = token
            blocked = threading.Thread(target=worker._maybe_heartbeat, daemon=True)
            blocked.start()
            blocked.join(timeout=0.5)
            still_blocked = blocked.is_alive()
        blocked.join(timeout=5)

        assert still_blocked, "_maybe_heartbeat ran its throttle outside the lock"
```

- [ ] **Step 2: Implement**

In `Worker.__init__`, add:

```python
        # The dedicated heartbeat thread (Q8, spec §3.4a). `None` until
        # `run_forever` starts it -- NEVER started by this constructor: a
        # single-tick diagnostic run (`manage.py run_jobs --once`) and
        # every test in this suite build a `Worker` and must spawn nothing.
        self._heartbeat_thread: threading.Thread | None = None
```

Move the throttle read-modify-write under the existing lock in `_maybe_heartbeat`:

```python
        with self._active_lock:
            now = time.monotonic()
            if (
                self._last_heartbeat_monotonic is not None
                and now - self._last_heartbeat_monotonic < HEARTBEAT_SECONDS
            ):
                return
            self._last_heartbeat_monotonic = now
            tokens = list(self._active_tokens.values())
        if not tokens:
            return
```

(and extend that method's docstring: it is still the ONE writer of `heartbeat_at`, now called from two threads, and the throttle state is what the lock protects.)

Add the thread and its starter:

```python
    def _start_heartbeat_thread(self) -> None:
        """Start the dedicated heartbeat thread. Called by `run_forever`
        only -- see `__init__`'s own note on why not the constructor."""
        if self._heartbeat_thread is not None:
            return
        thread = threading.Thread(
            target=self._heartbeat_forever, name="jobs-heartbeat", daemon=True,
        )
        self._heartbeat_thread = thread
        thread.start()

    def _heartbeat_forever(self) -> None:
        """The heartbeat's own thread (Q8, spec §3.4a).

        WHY IT EXISTS: the heartbeat used to be written from the tick
        thread alone, so a tick that BLOCKS -- a synchronous eviction pass,
        or a whole process starved during a cold load measured in minutes
        -- stopped the heartbeat too, and the orphan sweep reclaimed a job
        that was perfectly healthy. The cross-engine sweep this track adds
        makes that worse before it makes it better: `_residency_snapshot`
        contains no heartbeat call at all and each `list_installed` can
        cost a full discovery timeout.

        THE CONNECTION STORY IS THE WHOLE POINT, and getting it wrong
        re-creates the bug it fixes:

        - `close_old_connections()` at the TOP of every iteration. Django
          connections are thread-local; nothing else in this process would
          ever close or health-check the one this thread opens, so without
          this it would hold a single connection open for ever and sail
          straight through a database restart.
        - the write is WRAPPED. A transient database error is logged and
          retried on the next iteration rather than killing the thread.
        - an exit is LOUD. A silently dead heartbeat writer mass-orphans
          every healthy job this worker holds, which is precisely the
          failure Q8 exists to remove.

        Waits on `self._stopping` rather than sleeping, so a SIGTERM ends
        this thread promptly instead of after one more full interval, and
        polls at half the heartbeat cadence so the writer's own throttle
        (`HEARTBEAT_SECONDS`, still shared with the tick thread's calls)
        cannot stretch the effective interval to twice the constant.

        The tick's own `_maybe_heartbeat()` calls REMAIN, harmlessly
        throttled -- that is what keeps `--once` exactly as protected as
        it is today, with no thread running at all.
        """
        logger.info("worker %s: heartbeat thread started", self.worker_id)
        try:
            # HALF the heartbeat cadence, with NO FLOOR. A floor (an
            # earlier draft had `max(1.0, ...)`) makes a test that
            # monkeypatches `HEARTBEAT_SECONDS` down to fractions of a
            # second never execute this loop body at all, so the thread's
            # own behaviour becomes unprovable. Polling at half the cadence
            # is what stops the writer's own throttle -- still shared with
            # the tick thread's calls -- from stretching the effective
            # interval to twice the constant.
            while not self._stopping.wait(HEARTBEAT_SECONDS / 2):
                try:
                    close_old_connections()
                    self._maybe_heartbeat()
                except Exception:  # noqa: BLE001 - log and retry next iteration; never die
                    logger.warning(
                        "worker %s: heartbeat write failed; retrying next iteration",
                        self.worker_id, exc_info=True,
                    )
        finally:
            logger.info(
                "worker %s: heartbeat thread exiting -- every running row this worker "
                "holds now depends on the tick thread alone", self.worker_id,
            )
```

In `run_forever`, start it right after the signal handlers are installed (`self._start_heartbeat_thread()`), and in `_shutdown` join it with a short timeout after the stopping flag is set (it is a daemon thread, so a missed join can never hold the process open).

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/test_worker.py
```

Expected: all green, and `TestRunJobsCommand`'s `--once` tests still pass with no thread started.

- [ ] **Step 4: Commit**

```bash
git add models/queue/worker.py models/queue/tests/test_worker.py
git commit -m "fix(queue): the heartbeat gets its own thread

A tick that blocks -- a synchronous eviction pass, or a process starved
through a cold load measured in minutes -- stopped the heartbeat too, and
the sweep orphaned a healthy job. The residency snapshot this track is
about to widen contains no heartbeat call at all.

A daemon thread started by run_forever (never by the constructor, so
--once and the suite spawn nothing) calls the same single writer, closes
its own thread-local connection each iteration, survives a transient write
error, and logs loudly if it ever exits. The writer's throttle state moves
under the existing lock so the two threads cannot interleave it."
```

---

## Task 6: Staleness that knows what it is watching

**Spec:** §3.4(b), plus the two named `claim_and_admit` signature changes.

**Files:**
- Modify: `models/contracts/jobkinds.py` (`JobKind.stale_after_seconds`)
- Modify: `models/queue/claim.py` (`claim_and_admit` signature; `_sweep_orphans` kind-aware)
- Modify: `models/queue/worker.py` (passes the new keyword)
- Modify: `models/queue/tests/test_claim.py` (`TestOrphanSweep`, append), `models/registry/tests/test_jobkinds.py` **or** `models/contracts/tests/` — put the registry-field test beside the existing `JobKind` tests, wherever `grep -rn "default_priority" models/*/tests/` finds them.

**Interfaces:**
- Produces:
  - `JobKind.stale_after_seconds: int | None = None` — code-declared; `None` means "use the caller's global".
  - `claim_and_admit(worker_id: str, *, stale_after_seconds: int, sweep_orphans: bool = True, settings_row: JobSettings | None = None) -> list[dict]`
  - `_sweep_orphans(default_stale_seconds: int) -> None` — resolves the kind→threshold map **inside the claim module**, from the registry it already imports.
- Consumes: `models.contracts.jobkinds.all_job_kinds`.

**Ownership, stated because it is easy to get wrong:** the sweep runs inside `claim_and_admit`'s advisory-lock transaction and cannot import the worker (which imports this module). The **worker** owns the global cadence constant (`STALE_AFTER_SECONDS`) and passes it in; the **claim module** owns the per-kind resolution, because the job-kind registry is already one of its imports and the worker has no business parameterising a sweep it does not run.

- [ ] **Step 1: Write the failing tests**

In `models/queue/tests/test_claim.py`:

First, the row helper these tests call — `test_claim.py` has `_job`, `_ref` and `_set_budget` already, but nothing that builds a *stale* running row:

```python
def _running_job(*, kind: str, heartbeat_age_seconds: int) -> InferenceJob:
    """One RUNNING row whose heartbeat is `heartbeat_age_seconds` old --
    the only shape the orphan sweep's tests care about."""
    row = _job(kind=kind, state=RUNNING)
    InferenceJob.objects.filter(pk=row.pk).update(
        claim_token=uuid.uuid4(),
        heartbeat_at=timezone.now() - timedelta(seconds=heartbeat_age_seconds),
    )
    row.refresh_from_db()
    return row
```

> `_job` currently hard-codes `kind="test.kind"`; give it a `kind="test.kind"` keyword with that same default, which changes no existing call.

```python
class TestKindAwareStaleness:
    """A global 120s cutoff cannot be right for both a sub-second embed
    and a kind that cold-loads a large model for sixteen minutes. A kind
    declares its own threshold in CODE (it is a property of what the work
    does, like `default_priority`), and the sweep honours it."""

    @pytest.mark.django_db
    def test_a_kind_declaring_a_long_threshold_is_not_swept_early(self, reset_registry):
        register_job_kind(JobKind(
            key="test.slow", label="Slow", planner=f"{MODULE}.plan_no_models",
            handler=f"{MODULE}.echo_handler", summarizer=f"{MODULE}.summarize_noop",
            stale_after_seconds=3600,
        ))
        job = _running_job(kind="test.slow", heartbeat_age_seconds=600)

        claim_and_admit("w", stale_after_seconds=120)

        job.refresh_from_db()
        assert job.state == RUNNING
        assert job.attempts == 0

    @pytest.mark.django_db
    def test_a_kind_declaring_nothing_falls_back_to_the_global(self, reset_registry):
        register_job_kind(JobKind(
            key="test.plain", label="Plain", planner=f"{MODULE}.plan_no_models",
            handler=f"{MODULE}.echo_handler", summarizer=f"{MODULE}.summarize_noop",
        ))
        job = _running_job(kind="test.plain", heartbeat_age_seconds=600)

        claim_and_admit("w", stale_after_seconds=120)

        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.attempts == 1

    @pytest.mark.django_db
    def test_an_unregistered_kind_falls_back_to_the_global(self, reset_registry):
        """A row whose kind was deregistered since it was enqueued is
        still swept, on the global threshold -- never left running for ever
        because nothing declares a number for it."""
        job = _running_job(kind="test.gone", heartbeat_age_seconds=600)

        claim_and_admit("w", stale_after_seconds=120)

        job.refresh_from_db()
        assert job.state == QUEUED

    @pytest.mark.django_db
    def test_sweep_orphans_false_skips_the_sweep_entirely(self, reset_registry):
        job = _running_job(kind="test.plain", heartbeat_age_seconds=600)

        claim_and_admit("w", stale_after_seconds=120, sweep_orphans=False)

        job.refresh_from_db()
        assert job.state == RUNNING

    @pytest.mark.django_db
    def test_the_sweep_reads_the_running_set_once_however_many_thresholds(
            self, reset_registry, django_assert_num_queries):
        """Grouped by distinct threshold into ONE query, not one per kind
        -- pinned so a later kind cannot quietly make the sweep N+1."""
        for index, seconds in enumerate((30, 300, 3000)):
            register_job_kind(JobKind(
                key=f"test.k{index}", label="K", planner=f"{MODULE}.plan_no_models",
                handler=f"{MODULE}.echo_handler", summarizer=f"{MODULE}.summarize_noop",
                stale_after_seconds=seconds,
            ))

        with django_assert_num_queries(1):
            _sweep_orphans(120)
```

- [ ] **Step 2: Implement**

`models/contracts/jobkinds.py` — one field plus its docstring paragraph, beside `default_priority`:

```python
    stale_after_seconds: int | None = None
```

```
        stale_after_seconds: How long this kind's running job may go without
            a heartbeat before the orphan sweep reclaims it, or `None` to
            use the worker's own global cutoff. CODE-DECLARED, never
            operator-editable: a kind's staleness is a property of what the
            work DOES -- a sub-second embed and a job that cold-loads a
            large model for minutes cannot share one number -- in the same
            spirit as `default_priority` above. Read by
            `models.queue.claim._sweep_orphans`, which resolves the whole
            kind->threshold map itself; nothing else branches on it.
```

`models/queue/claim.py`:

```python
def claim_and_admit(
    worker_id: str, *, stale_after_seconds: int, sweep_orphans: bool = True,
    settings_row: JobSettings | None = None,
) -> list[dict]:
```

with the docstring gaining:

```
    `sweep_orphans` (spec §3.4c) lets the caller skip step 2 for ONE round.
    The worker passes `False` for a grace period after it detects that the
    HOST SLEPT -- on wake every running row looks stale at once, because
    wall-clock hours passed while the process's monotonic clock barely
    advanced, and a sweep at that instant mass-orphans healthy work. It is
    a deliberate one-round skip, never a mode: the very next tick sweeps
    normally.
```

and step 2 becoming `if sweep_orphans: _sweep_orphans(stale_after_seconds)`.

`_sweep_orphans` gains the map and one grouped query:

```python
def _kind_stale_thresholds(default_stale_seconds: int) -> dict[str, int]:
    """`{job kind key: staleness threshold}` for every REGISTERED kind,
    with `default_stale_seconds` standing in for any kind that declares
    none.

    Resolved HERE, in the claim module, rather than threaded in from the
    worker: this module already imports the job-kind registry, the sweep
    runs inside this module's own advisory-lock transaction, and the
    worker owns only the global cadence constant it passes in (it may not
    be imported from here -- it imports this module).
    """
    thresholds: dict[str, int] = {}
    for kind in all_job_kinds():
        declared = getattr(kind, "stale_after_seconds", None)
        thresholds[kind.key] = declared if declared else default_stale_seconds
    return thresholds


def _sweep_orphans(default_stale_seconds: int) -> None:
    """Requeue-or-fail every `running` job whose heartbeat has gone stale
    -- now against THAT KIND's own threshold (spec §3.4b) rather than one
    global cutoff, which could never be right for both a sub-second embed
    and a job that cold-loads a large model for minutes.

    ONE QUERY, grouped by DISTINCT threshold (a handful of kinds, bounded
    and pinned by a query-count test) -- never one query per kind. A kind
    the registry does not know (deregistered since the row was enqueued)
    falls to `default_stale_seconds`, so no row is ever left running for
    ever merely because nothing declares a number for it.

    Everything below this point is unchanged: first orphaning requeues
    with `attempts=1`, a second fails permanently and schedules the kind's
    `on_terminal` hook via `transaction.on_commit`, and each row's own
    UPDATE re-checks `state=running AND heartbeat_at < <that row's own
    cutoff>` atomically so a heartbeat landing in the window between read
    and write leaves the job correctly alone.
    """
    now = timezone.now()
    thresholds = _kind_stale_thresholds(default_stale_seconds)

    by_threshold: dict[int, list[str]] = {}
    for kind_key, seconds in thresholds.items():
        by_threshold.setdefault(seconds, []).append(kind_key)

    condition = Q(
        ~Q(kind__in=list(thresholds)),
        heartbeat_at__lt=now - timedelta(seconds=default_stale_seconds),
    )
    for seconds, kind_keys in by_threshold.items():
        condition |= Q(kind__in=kind_keys, heartbeat_at__lt=now - timedelta(seconds=seconds))

    stale = list(InferenceJob.objects.filter(Q(state=RUNNING) & condition))

    for job in stale:
        cutoff = now - timedelta(seconds=thresholds.get(job.kind, default_stale_seconds))
        ...  # the existing two branches, unchanged, using this row's own `cutoff`
```

with `from django.db.models import Q` and `from models.contracts.jobkinds import all_job_kinds, invoke_on_terminal` at the top.

`models/queue/worker.py`'s `tick()` keeps passing `stale_after_seconds=STALE_AFTER_SECONDS` and gains `sweep_orphans=` in Task 7.

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/test_claim.py models/queue/tests/test_worker.py models/contracts/tests/ models/registry/tests/test_jobkinds.py
```

- [ ] **Step 4: Commit**

```bash
git add models/contracts/jobkinds.py models/queue/claim.py models/queue/tests/test_claim.py
git commit -m "feat(queue): staleness belongs to the kind, not to one global

One 120s cutoff cannot be right for both a sub-second embed and a kind
that cold-loads a large model for minutes -- the second is orphaned mid
cold load, every time.

JobKind gains a code-declared stale_after_seconds (a property of what the
work does, like default_priority, never an operator knob). The kind map is
resolved inside the claim module, which already imports the registry and
owns the transaction; the worker still passes only its global cadence.
The sweep stays ONE query, grouped by distinct threshold, pinned.
claim_and_admit also gains sweep_orphans=True, for the sleep-detection
grace period landing next."
```

---

## Task 7: A host that went to sleep

**Spec:** §3.4(c).

**Files:**
- Modify: `models/queue/worker.py` (`SLEEP_DETECT_SECONDS`, `tick()`)
- Modify: `models/queue/tests/test_worker.py` (append `TestSleepDetection`)

**Interfaces:**
- Produces: `SLEEP_DETECT_SECONDS: int`; `Worker._last_tick_wall`/`_last_tick_monotonic`; `Worker._slept_since: float | None`.
- Consumes: `claim_and_admit(..., sweep_orphans=...)` (Task 6).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
class TestSleepDetection:
    """The container VM ballooning after a host sleep, and the host
    sleeping mid-job, produce the same trap: wall-clock hours pass while
    the process's monotonic clock barely advances, so on wake EVERY
    running row looks stale at once and the sweep mass-orphans healthy
    work."""

    def test_a_wall_clock_jump_skips_one_sweep_and_writes_a_heartbeat(self, worker, monkeypatch):
        seen = {}

        def _fake_claim(worker_id, **kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(worker_module, "claim_and_admit", _fake_claim)
        worker.tick()
        assert seen["sweep_orphans"] is True

        worker._last_tick_wall -= 4 * 3600
        worker.tick()

        assert seen["sweep_orphans"] is False

    def test_the_grace_period_ends_and_sweeping_resumes(self, worker, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            worker_module, "claim_and_admit",
            lambda worker_id, **kwargs: (seen.update(kwargs), [])[1],
        )
        # A SMALL POSITIVE grace, never 0: with 0 the detecting tick
        # itself computes `mono >= mono + 0` -> True, so the final
        # assertion passes without the grace ever having been in force and
        # the test proves nothing.
        monkeypatch.setattr(worker_module, "SLEEP_GRACE_SECONDS", 0.2)

        worker.tick()
        worker._last_tick_wall -= 4 * 3600
        worker.tick()
        assert seen["sweep_orphans"] is False, "the grace was never in force"

        time.sleep(0.3)
        worker.tick()

        assert seen["sweep_orphans"] is True

    def test_it_says_so_honestly(self, worker, monkeypatch, caplog):
        monkeypatch.setattr(worker_module, "claim_and_admit", lambda *a, **k: [])
        worker.tick()
        worker._last_tick_wall -= 4 * 3600

        with caplog.at_level("WARNING", logger="models.queue.worker"):
            worker.tick()

        assert any("slept" in r.getMessage() for r in caplog.records)

    def test_an_ordinary_tick_never_trips_it(self, worker, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            worker_module, "claim_and_admit",
            lambda worker_id, **kwargs: (seen.update(kwargs), [])[1],
        )

        worker.tick()
        worker.tick()

        assert seen["sweep_orphans"] is True
```

- [ ] **Step 2: Implement**

Constants, beside `STALE_AFTER_SECONDS`:

```python
# A tick whose WALL-CLOCK delta exceeds its MONOTONIC delta by this many
# seconds did not take that long -- the host (or the container VM) was
# suspended. Monotonic clocks on this platform do not advance across a
# sleep; wall clock does. 60s is far beyond any scheduling delay a 0.5s
# tick loop could accumulate and far below the shortest sleep worth
# noticing.
SLEEP_DETECT_SECONDS = 60

# How long after a detected sleep the orphan sweep is skipped, so every
# live worker's rows can re-stamp themselves before anything judges them.
# Comfortably more than `HEARTBEAT_SECONDS` and less than
# `STALE_AFTER_SECONDS`: long enough for a heartbeat to land, short enough
# that a genuinely dead job is still reclaimed promptly.
SLEEP_GRACE_SECONDS = 30
```

In `__init__`: `self._last_tick_wall: float | None = None`, `self._last_tick_monotonic: float | None = None`, `self._sweep_skip_until: float | None = None`.

At the top of `tick()`, before the claim:

```python
        wall, mono = time.time(), time.monotonic()
        if self._last_tick_wall is not None:
            drift = (wall - self._last_tick_wall) - (mono - self._last_tick_monotonic)
            if drift > SLEEP_DETECT_SECONDS:
                logger.warning(
                    "worker %s: the host appears to have slept for about %.0f seconds "
                    "(wall clock moved that much further than the monotonic clock); "
                    "skipping the orphan sweep for %ss so live rows can re-stamp "
                    "themselves before anything judges them",
                    self.worker_id, drift, SLEEP_GRACE_SECONDS,
                )
                self._sweep_skip_until = mono + SLEEP_GRACE_SECONDS
                # A fresh heartbeat IMMEDIATELY, not on the throttle: every
                # row this worker holds looks stale at this instant, and
                # the next admitter's sweep is not necessarily ours.
                with self._active_lock:
                    self._last_heartbeat_monotonic = None
                self._maybe_heartbeat()
        self._last_tick_wall, self._last_tick_monotonic = wall, mono

        sweep = self._sweep_skip_until is None or mono >= self._sweep_skip_until
```

and thread `sweep_orphans=sweep` into the `claim_and_admit` call.

Extend `tick()`'s docstring with the sleep paragraph, and note in `SLEEP_DETECT_SECONDS`'s comment that a false positive costs exactly one skipped sweep — deliberately the cheap direction (spec §11).

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/test_worker.py models/queue/tests/test_claim.py
```

- [ ] **Step 4: Commit**

```bash
git add models/queue/worker.py models/queue/tests/test_worker.py
git commit -m "fix(queue): a host that slept does not mass-orphan healthy work

Monotonic clocks do not advance across a suspend; wall clock does. On wake
every running row looked stale at once and the very next sweep reclaimed
all of them.

The tick compares the two deltas, says so honestly when they diverge past
SLEEP_DETECT_SECONDS, writes a fresh heartbeat immediately (off the
throttle -- the next admitter is not necessarily us), and passes
sweep_orphans=False for one grace period. A false positive costs exactly
one skipped sweep, deliberately the cheap direction."
```

---

## Task 8: A job already running here is never submitted twice

**Spec:** §3.4(d) — the unbuilt half of the 2026-08-25 reclaim fix, and §9.11 (why a requeue is the wrong answer).

**Files:**
- Modify: `models/queue/worker.py` (`_launch`, + `_live_attempt_token`, + `_restore_to_live_attempt`)
- Modify: `models/queue/tests/test_worker.py` (append to `TestReclaimedAttemptTokenCollision`)

**Interfaces:**
- Produces: `Worker._live_attempt_token(job_id: int, exclude: uuid.UUID) -> uuid.UUID | None`; `Worker._restore_to_live_attempt(descriptor: dict, live_token: uuid.UUID) -> bool`.
- Consumes: `self._futures` (already keyed per attempt), `_requeue_unlaunched` (the zero-rows fallback).

**Why not a requeue.** A requeue would leave the row `queued` with no token tracked, strip the live attempt of heartbeat protection, invite re-admission 0.5 s later for the whole length of a cold load (each tick paying a widened eviction pass), and finally discard the live attempt's own token-conditional writeback — so the job would run a third time. The refusal instead **restores the row to the live attempt**.

- [ ] **Step 1: Write the failing tests**

```python
    @pytest.mark.django_db(transaction=True)
    def test_a_second_launch_restores_the_row_to_the_live_attempt(self, worker):
        """Two handlers, one job, one set of models -- the shape the
        2026-08-25 fix repaired the bookkeeping for without ever stopping
        the second execution."""
        job = _job(state=RUNNING)
        live_token = uuid.uuid4()
        live_future = concurrent.futures.Future()
        worker._futures[(job.pk, live_token)] = live_future
        superseding = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(claim_token=superseding)

        worker._launch({
            "id": job.pk, "kind": job.kind, "payload": {}, "model_refs": [],
            "claim_token": superseding, "exclusive": False, "checkpoint": None,
            "attempts": 0,
        })

        job.refresh_from_db()
        assert job.state == RUNNING
        assert job.claim_token == live_token
        assert job.claimed_by == worker.worker_id
        assert (job.pk, superseding) not in worker._futures
        with worker._active_lock:
            assert worker._active_tokens[job.pk] == live_token
        live_future.set_result(None)

    @pytest.mark.django_db(transaction=True)
    def test_the_refusal_is_logged_as_a_warning_naming_the_job(self, worker, caplog):
        job = _job(state=RUNNING)
        live_token = uuid.uuid4()
        future = concurrent.futures.Future()
        worker._futures[(job.pk, live_token)] = future
        superseding = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(claim_token=superseding)

        with caplog.at_level("WARNING", logger="models.queue.worker"):
            worker._launch({
                "id": job.pk, "kind": job.kind, "payload": {}, "model_refs": [],
                "claim_token": superseding, "exclusive": False, "checkpoint": None,
                "attempts": 0,
            })

        assert any(str(job.pk) in r.getMessage() for r in caplog.records)
        future.set_result(None)

    @pytest.mark.django_db(transaction=True)
    def test_a_done_attempt_does_not_block_a_relaunch(self, worker):
        job = _job(state=RUNNING)
        finished_token = uuid.uuid4()
        done = concurrent.futures.Future()
        done.set_result(None)
        worker._futures[(job.pk, finished_token)] = done
        fresh = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(claim_token=fresh)

        worker._launch({
            "id": job.pk, "kind": job.kind, "payload": {}, "model_refs": [],
            "claim_token": fresh, "exclusive": False, "checkpoint": None, "attempts": 0,
        })

        assert (job.pk, fresh) in worker._futures

    @pytest.mark.django_db(transaction=True)
    def test_when_another_worker_owns_the_row_the_fresh_claim_is_requeued(self, worker):
        """Zero rows matched: someone else legitimately holds it now. The
        fresh claim goes back the ordinary way and the live attempt is left
        to discover its own writeback is stale -- the existing, documented
        behaviour."""
        job = _job(state=RUNNING)
        live_token = uuid.uuid4()
        future = concurrent.futures.Future()
        worker._futures[(job.pk, live_token)] = future
        superseding = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(claim_token=uuid.uuid4())

        worker._launch({
            "id": job.pk, "kind": job.kind, "payload": {}, "model_refs": [],
            "claim_token": superseding, "exclusive": False, "checkpoint": None,
            "attempts": 0,
        })

        job.refresh_from_db()
        assert job.state == QUEUED
        future.set_result(None)
```

- [ ] **Step 2: Implement**

```python
    def _live_attempt_token(self, job_id: int, exclude: uuid.UUID) -> uuid.UUID | None:
        """The claim token of an attempt for `job_id` this process still
        has IN FLIGHT (a future that is not `done()`), other than
        `exclude` -- or `None`.

        `self._futures` is keyed per attempt (2026-08-25's second defect
        fix), which is exactly what makes this answerable: a superseded
        attempt and its successor are both present, under their own keys.
        """
        for (tracked_id, token), future in self._futures.items():
            if tracked_id == job_id and token != exclude and not future.done():
                return token
        return None

    def _restore_to_live_attempt(self, descriptor: dict, live_token: uuid.UUID) -> bool:
        """Give the row back to the attempt that is genuinely still
        running it, under THAT attempt's own claim token, and report
        whether the write landed.

        Conditional on the SUPERSEDING token, so this can only ever
        rewrite the row this claim actually holds: if another worker
        legitimately owns it by now, zero rows match and the caller
        requeues the fresh claim the ordinary way instead (the existing,
        documented "stale token, zero rows" shape).
        """
        job_id = descriptor["id"]
        restored = InferenceJob.objects.filter(
            pk=job_id, claim_token=descriptor["claim_token"],
        ).update(
            state=RUNNING, claim_token=live_token, claimed_by=self.worker_id,
            heartbeat_at=timezone.now(),
        )
        if not restored:
            return False
        with self._active_lock:
            self._active_tokens[job_id] = live_token
        return True
```

and `_launch` gains its refusal at the top:

```python
        job_id = descriptor["id"]
        claim_token = descriptor["claim_token"]

        # NO DUPLICATE SUBMIT (spec §3.4d, the unbuilt half of the
        # 2026-08-25 reclaim fix). A job this process is ALREADY executing
        # can be orphaned (its own cold load starved the heartbeat),
        # re-admitted, and launched a second time here: two handlers, one
        # job, one set of models.
        #
        # The naive answer -- requeue the fresh claim -- is wrong in four
        # ways at once: the row would sit `queued` with no token tracked,
        # the live attempt would lose heartbeat protection, re-admission
        # would come round again 0.5s later for the entire length of the
        # cold load this exists for (each tick paying a widened eviction
        # pass), and the live attempt's own token-conditional writeback
        # would finally be discarded, running the job a THIRD time.
        #
        # So the refusal RESTORES the row to the live attempt instead: back
        # to `running` under that attempt's own token, claimed by this
        # worker, freshly heartbeaten. The row is then not a candidate, the
        # live attempt is heartbeat-protected again, and its eventual
        # writeback matches the row it is writing to.
        live_token = self._live_attempt_token(job_id, exclude=claim_token)
        if live_token is not None:
            # NOTE FOR TASK 12: this `return` sits ABOVE the `_futures`
            # insert at the bottom of this method, and Task 12 adds a
            # companion `_inflight_refs` write beside that insert. That
            # write MUST stay below this refusal -- written above it, a
            # refused descriptor would leave an `_inflight_refs` entry with
            # no matching `_futures` key, which `_prune_finished_futures`
            # (it iterates `_futures`) could never drop, and eviction would
            # protect that key for the life of the process.
            logger.warning(
                "worker %s: refusing to submit job %s twice -- an attempt is still in "
                "flight here; restoring the row to it and discarding this claim",
                self.worker_id, job_id,
            )
            if not self._restore_to_live_attempt(descriptor, live_token):
                self._requeue_unlaunched([descriptor])
            return
```

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/test_worker.py
```

- [ ] **Step 4: Commit**

```bash
git add models/queue/worker.py models/queue/tests/test_worker.py
git commit -m "fix(queue): a job already in flight here is never submitted twice

The 2026-08-25 reclaim fix repaired the bookkeeping for a re-admitted job
whose earlier attempt was still running, but never stopped the second
execution: two handlers, one job, one set of models.

_launch now refuses a job id that still has a live future -- and restores
the row to the live attempt under its own claim token rather than
requeueing it, which would strip heartbeat protection, churn every tick for
the length of a cold load, and finally discard the live attempt's own
writeback. Zero rows matched means another worker legitimately owns it: the
fresh claim requeues the ordinary way."
```

---

## Task 9: A cold boot is quiet

**Spec:** §3.10.

**Files:**
- Modify: `models/queue/worker.py` (`__init__`'s pool read; `tick()`'s guard)
- Modify: `models/queue/tests/test_worker.py` (append `TestBootTolerance`)

**Interfaces:**
- Produces: `BOOT_SCHEMA_WAIT_ATTEMPTS: int`, `BOOT_SCHEMA_WAIT_SECONDS: float`, `Worker._settings_row_or_wait()`.
- Consumes: `JobSettings.get_solo`.

**Both sites, both exception classes.** An untolerated `ProgrammingError` escaping the tick is treated by `run_forever` as a **crash** — traceback, loop stopped, non-zero exit — which is the very traceback this exists to remove.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
class TestBootTolerance:
    """On a cold compose boot the worker can win the race against
    `migrate` at either of its two `JobSettings` reads."""

    @pytest.mark.parametrize("error", [ProgrammingError, OperationalError])
    def test_the_constructor_waits_then_falls_back_to_the_documented_default(
            self, monkeypatch, caplog, error):
        monkeypatch.setattr(worker_module, "BOOT_SCHEMA_WAIT_ATTEMPTS", 2)
        monkeypatch.setattr(worker_module, "BOOT_SCHEMA_WAIT_SECONDS", 0)
        monkeypatch.setattr(
            JobSettings, "get_solo",
            classmethod(lambda cls: (_ for _ in ()).throw(error("no such table"))),
        )

        with caplog.at_level("INFO", logger="models.queue.worker"):
            built = Worker(worker_id="boot")

        assert built._executor._max_workers == JobSettings.MAX_CONCURRENT_JOBS_DEFAULT
        assert any("waiting for the database schema" in r.getMessage() for r in caplog.records)
        assert not any(r.exc_info for r in caplog.records), "a traceback was logged"
        built._executor.shutdown(wait=False)

    @pytest.mark.parametrize("error", [ProgrammingError, OperationalError])
    def test_the_first_ticks_return_quietly_rather_than_raising(
            self, worker, monkeypatch, caplog, error):
        monkeypatch.setattr(
            JobSettings, "get_solo",
            classmethod(lambda cls: (_ for _ in ()).throw(error("no such table"))),
        )

        with caplog.at_level("INFO", logger="models.queue.worker"):
            worker.tick()  # must not raise

        assert not any(r.levelname == "ERROR" for r in caplog.records)
```

- [ ] **Step 2: Implement**

```python
# How many times, and how long apart, the CONSTRUCTOR waits for a racing
# `migrate` before giving up and sizing the pool from the documented
# default. Deliberately small: a worker that cannot read its settings row
# after this long is better off running at the default cap than blocking a
# compose boot, and the tick loop tolerates the same failure independently.
BOOT_SCHEMA_WAIT_ATTEMPTS = 10
BOOT_SCHEMA_WAIT_SECONDS = 3.0
```

```python
    def _settings_row_or_wait(self) -> JobSettings | None:
        """The settings row, waiting briefly for a racing `migrate`, or
        `None` once the wait expires.

        ONE INFO LINE, NEVER A TRACEBACK. On a cold compose boot the
        worker and `migrate` start together and this read can genuinely
        lose the race; a traceback there is noise an operator learns to
        ignore, on the one boot where a real error would matter.
        """
        from django.db import OperationalError, ProgrammingError

        for attempt in range(BOOT_SCHEMA_WAIT_ATTEMPTS):
            try:
                return JobSettings.get_solo()
            except (ProgrammingError, OperationalError):
                if attempt == 0:
                    logger.info(
                        "worker: waiting for the database schema (the queue's tables are "
                        "not there yet -- `migrate` is probably still running)",
                    )
                self._stopping.wait(BOOT_SCHEMA_WAIT_SECONDS)
        return None
```

`__init__` uses it:

```python
        row = self._settings_row_or_wait()
        max_workers = max(
            (row.max_concurrent_jobs if row is not None
             else JobSettings.MAX_CONCURRENT_JOBS_DEFAULT),
            1,
        )
```

and `tick()` guards its own read:

```python
        try:
            settings_row = JobSettings.get_solo()
        except (ProgrammingError, OperationalError):
            # A tick that raises is treated by `run_forever` as a CRASH:
            # traceback, loop stopped, non-zero exit. On a cold boot that
            # is simply the wrong reading of "migrate has not finished
            # yet", so the first ticks return quietly and the loop survives
            # to try again (spec §3.10).
            logger.info("worker %s: database schema not ready yet; skipping this tick",
                        self.worker_id)
            return
```

with `from django.db import OperationalError, ProgrammingError` added to the module imports.

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/test_worker.py
```

- [ ] **Step 4: Commit**

```bash
git add models/queue/worker.py models/queue/tests/test_worker.py
git commit -m "fix(queue): a cold boot is quiet at both settings reads

The worker reads JobSettings in two places -- the constructor, to size its
pool, and every tick -- and on a cold compose boot either can win the race
against migrate. An untolerated ProgrammingError escaping the tick is read
by run_forever as a crash: traceback, loop stopped, non-zero exit.

The constructor now waits briefly (one INFO line, never a traceback) and
falls back to the documented default pool size; the first ticks return
quietly. Both sites name ProgrammingError and OperationalError explicitly."
```

---

## Task 10: Migration 2 — five columns on the queue app

**Spec:** §7 ("Two migrations across two apps, seven columns"). `not_before` and `passed_over` on the job row; `kind_wait_seconds`, `detected_memory_bytes`, `detected_memory_at` on the settings row.

**Files:**
- Modify: `models/queue/models.py`
- Create: `models/queue/migrations/0005_queue_memory_governance.py` (generated)
- Modify: `models/queue/backend.py` (`QueueRow` carries the two job-row columns)
- Modify: `models/queue/tests/test_models.py` (append)

**Interfaces:**
- Produces:
  - `InferenceJob.not_before: datetime | None` (null/blank, indexed with the claim scan)
  - `InferenceJob.passed_over: int` (PositiveSmallIntegerField, default 0)
  - `JobSettings.kind_wait_seconds: dict` (JSONField, default `dict`)
  - `JobSettings.detected_memory_bytes: int | None`, `JobSettings.detected_memory_at: datetime | None`
  - `QueueRow.not_before`, `QueueRow.passed_over`
- Consumes: nothing.

**One migration, authored once, consumed by five later tasks.** Tasks 12, 13, 14, 15 and 16 all read columns this task creates; none of them may add a migration of its own. **Migration number verified against the tree (2026-09-21):** `models/queue/migrations/` ends at `0004_jobsettings_response_timeout_seconds.py`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
class TestTheGovernanceColumns:
    def test_a_fresh_job_is_immediately_claimable_and_never_passed_over(self):
        job = make_queue_job(kind="test.k")

        assert job.not_before is None
        assert job.passed_over == 0

    def test_the_settings_row_ships_an_empty_wait_map_and_no_detected_memory(self):
        row = JobSettings.get_solo()

        assert row.kind_wait_seconds == {}
        assert row.detected_memory_bytes is None
        assert row.detected_memory_at is None

    def test_the_wait_map_round_trips(self):
        row = JobSettings.get_solo()
        row.kind_wait_seconds = {"agent.turn": 1800}
        row.save(update_fields=["kind_wait_seconds"])
        row.refresh_from_db()

        assert row.kind_wait_seconds == {"agent.turn": 1800}

    def test_the_queue_row_carries_both_job_columns(self):
        from models.queue.backend import queue_snapshot

        make_queue_job(kind="test.k")
        row = queue_snapshot().waiting[0]

        assert row.not_before is None
        assert row.passed_over == 0
```

- [ ] **Step 2: Implement**

On `InferenceJob`:

```python
    # THE HOLD-OFF (spec §3.3d). A job the queue has DELIBERATELY declined
    # to consider until this moment -- written by the worker when an
    # exclusive launch is refused, either because a protected endpoint is
    # still busy or because the barrier got an informative refusal. The
    # claim's candidate query honours it (`not_before IS NULL OR
    # not_before <= now`), which makes it the ONE new admission-side
    # filter this track adds.
    #
    # DURABLE rather than in-process, deliberately: the restart that would
    # clear an in-memory hold-off is the same restart that clears the
    # barrier's refusal count, and the two together would drop a freshly
    # restarted worker straight back into 0.5s-tick churn against an
    # endpoint that is still holding memory.
    #
    # The deadlock proof survives because the exclusion is time-bounded and
    # SELF-CLEARING: the job returns to its own head position the moment
    # the hold-off expires, and an effectively-exclusive head is still
    # admitted alone the instant the machine is idle.
    not_before = models.DateTimeField(null=True, blank=True)

    # HOW MANY TIMES MODEL-AFFINITY REORDERING HAS PUT A LATER PEER AHEAD
    # OF THIS JOB (spec §3.6). Durable, not in-memory state: a worker
    # restart must not reset a job's age and let it be passed over for
    # ever. At `models.queue.scheduler.MAX_PASSOVERS` the job is PINNED --
    # it sorts strictly by id within its priority from then on and can
    # never be reordered behind a peer again.
    passed_over = models.PositiveSmallIntegerField(default=0)
```

Add `not_before` to the claim-scan index so the new filter does not cost a scan:

```python
            models.Index(fields=["state", "not_before", "priority", "id"],
                         name="jobs_claim_scan_holdoff"),
```

(keep `jobs_claim_scan` — the plain state/priority/id scan is still used by the queue page's partition and by `get_job`'s position count.)

On `JobSettings`:

```python
    # PER-KIND WAIT CEILINGS (spec §3.5a), `{job kind key: seconds}`. The
    # OPERATOR-editable half of a kind's wait ceiling; the code-declared
    # default lives on `models.contracts.jobkinds.JobKind.
    # default_wait_seconds`. Rides on THIS row, which
    # `models.queue.worker.Worker._build_job_context` already fetches for
    # `response_timeout_seconds` -- a second `get_solo()` would be a
    # query-count regression and is explicitly not how this is read.
    kind_wait_seconds = models.JSONField(default=dict, blank=True)

    # WHAT THE WORKER PROCESS MEASURED, ONCE, AT BOOT (spec §3.7). The
    # console renders in the WEB service and the budget governs the WORKER
    # service -- separate containers -- so memory detected in the web
    # process describes the wrong machine. Written by the worker, rendered
    # on the settings page labelled with the process that measured it and
    # the date. NOTHING IS EVER APPLIED ON THE OPERATOR'S BEHALF: the
    # container sees the VM's allocation rather than the host's, and a
    # silently derived budget would be authoritative and wrong.
    detected_memory_bytes = models.BigIntegerField(null=True, blank=True)
    detected_memory_at = models.DateTimeField(null=True, blank=True)
```

and add the three to `get_solo`'s `defaults` dict.

`QueueRow` gains `not_before: datetime | None` and `passed_over: int`, filled in `_queue_row` from the job row.

```bash
.venv/bin/python manage.py makemigrations queue -n queue_memory_governance
```

Expected: one file, `models/queue/migrations/0005_queue_memory_governance.py`, with five `AddField` operations and the one `AddIndex`. **If it produces two files or a sixth field, stop.**

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/
.venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: tests green; `makemigrations --check` reports no changes.

- [ ] **Step 4: Commit**

```bash
git add models/queue/models.py models/queue/migrations/0005_queue_memory_governance.py models/queue/backend.py models/queue/tests/test_models.py
git commit -m "feat(queue): the five columns memory governance needs

One migration for the whole track (the registry's is the other): not_before
and passed_over on the job row, kind_wait_seconds and the worker-measured
detected_memory pair on the settings row. Tasks landing after this one
consume these columns and add no migration of their own.

not_before is durable rather than in-process because the restart that
would clear an in-memory hold-off is the same restart that clears the
barrier's refusal count. It joins the claim scan's index so the new
admission-side filter costs no scan."
```

---

## Task 11: The image adapter declares what its unload frees and what its residency report is worth

**Spec:** §3.3(a), and §13's steward-amendment table (finding S-1). **CROSS-COLUMN: this task's diff belongs to the engine steward's column and is PRE-CLEARED by that steward.**

**Files:**
- Modify: `models/contracts/engines/comfyui.py` (two class attributes on `ComfyUIEngine`, with their comment)
- Modify: `models/contracts/tests/test_engines_base.py` (append)

**Interfaces:**
- Produces: `ComfyUIEngine.unload_scope = "endpoint"`, `ComfyUIEngine.residency_authority = "memo"`.
- Consumes: nothing — these are declarations. Task 12 is what reads them.

**Both values are the queue's own safe defaults, so this task changes no behaviour** — it removes a guess. Keep it as its own commit so the steward can take, hold or drop it without touching the rest of the branch; the queue stays correct either way (spec §11, "declarations, not probes").

**How a drop actually happens, so "the steward may drop it" is not a wish.** Nothing else on the branch imports, reads or names these attributes except through `getattr` with a safe default (Task 12's `_unload_scope`/`_residency_authority`), and no test outside this task's own asserts them. So if the steward declines, the mechanism is `git revert <this commit's sha>` on the branch before the pull request — a one-file revert that touches nothing else, needs no rebase, and leaves every other commit's tests green. Do **not** rebase the commit out of the middle of the branch: later commits touch the same file's neighbourhood (`models/contracts/engines/base.py`'s seam docstring in Task 12), and a revert keeps the history honest about the decision having been made.

**The text adapter's equivalent lines are that steward's call and are NOT written here.** Factually they are `unload_scope = "model"` and `residency_authority = "endpoint"`; the queue's behaviour is consistent with or without them.

- [ ] **Step 1: Write the failing tests**

```python
class TestTheImageAdapterDeclaresItsEvictionSemantics:
    """Two one-line declarations, pre-cleared by the engine steward
    (spec §13 steward amendment, S-1). Both match what the queue would
    have ASSUMED, so this changes no behaviour -- it replaces a guess
    with a fact, which is what lets the queue tell this engine apart from
    one that really does free a single named model."""

    def test_its_unload_frees_the_whole_endpoint(self):
        assert ComfyUIEngine.unload_scope == "endpoint"

    def test_its_residency_report_is_a_process_local_memo(self):
        assert ComfyUIEngine.residency_authority == "memo"
```

- [ ] **Step 2: Implement**

On `ComfyUIEngine`, beside `well_known_ports`:

```python
    # WHAT AN UNLOAD CALL HERE ACTUALLY FREES, declared for the execution
    # queue's eviction pass (ADR 0013's queue memory-governance amendment).
    # "endpoint": `POST /free` calls `unload_all_models()`, which frees
    # EVERY model at this endpoint -- `model_id` is addressing, not
    # selection (see `unload`'s own docstring). A caller that assumed
    # per-model granularity would evict a needed checkpoint out from under
    # a live attempt.
    unload_scope = "endpoint"

    # HOW MUCH THIS ENGINE'S RESIDENCY REPORT IS WORTH. "memo": the
    # `loaded` flags `list_installed` carries come from this adapter's own
    # TTL'd, process-local run memo, not from any residency endpoint the
    # engine answers -- so "nothing resident" here means "this process does
    # not remember anything", which a restart alone produces. The queue
    # reads this to decide whether an empty residency answer is worth
    # trusting before it launches an exclusive job.
    residency_authority = "memo"
```

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/contracts/tests/ models/queue/tests/
```

- [ ] **Step 4: Commit**

```bash
git add models/contracts/engines/comfyui.py models/contracts/tests/test_engines_base.py
git commit -m "feat(engines): the image adapter declares its unload scope and residency authority

Pre-cleared by the engine steward for the queue memory-governance track.
Two class attributes, no behaviour change: both values are what the queue
already assumes by default. unload_scope='endpoint' because /free releases
every model at the endpoint and ignores model_id; residency_authority=
'memo' because the loaded flags come from this adapter's own TTL'd,
process-local run memo rather than a residency endpoint.

The text adapter's equivalent declarations are its steward's call and are
deliberately not written here."
```

---


## Task 12: The eviction rewrite — reach, protection, the barrier, and saying what happened

**Spec:** §3.3(a) through §3.3(g) in full, §3.1's rung-3 harvest, §3.6's residency cache, §9.3 through §9.10, owner decision 6 (accepted: an honest job failure).

**ONE TASK, THREE COMMITS.** Tasks 12–14 of the first draft were one rewrite of `_evict_to_match_plan` split across three commits with no declared end state, and the plan-hygiene review (2026-09-21, M5 / Ruling A) rejected that split: five interfaces moved more than once, `_unload_endpoint`'s declared return type could not produce a value a later commit required, the claim that it was "the ONE place an unload is called" was falsified by the barrier's precautionary call, and the protected-key rule was **dead code on a no-budget box** until the last commit removed the early return — so the first commit's own verification proved less than it appeared to.

They are now one task. **The end-state contract below is declared once, before the first commit, and no commit writes a shape a later commit revises.** Where a parameter is declared in commit 1 and first *read* in commit 3 (`_unload_endpoint`'s `reason`), that is deliberate and is stated at the declaration.

**Files:**
- Modify: `models/contracts/engines/base.py` (document both optional attributes on the seam)
- Modify: `models/queue/worker.py` (the whole eviction pass; `_launch`/`_prune_finished_futures`; `tick()`)
- Modify: `models/queue/claim.py` (the candidate query honours `not_before`)
- Modify: `models/queue/tests/test_worker.py` (test helpers; `TestEviction` extended; four new classes)
- Modify: `models/queue/tests/test_claim.py` (the hold-off's admission-side half)

---

### End-state contract (declared once; every commit writes a subset of this and nothing else)

```python
# --- reading the two optional engine declarations -------------------------
_UNLOAD_SCOPES = ("model", "endpoint")
_RESIDENCY_AUTHORITIES = ("endpoint", "memo")

@staticmethod
def _unload_scope(engine_obj) -> str: ...            # "model" | "endpoint"; anything else -> "endpoint"

@staticmethod
def _residency_authority(engine_obj) -> str: ...     # "endpoint" | "memo"; anything else -> "memo"

# --- phase 1: what the machine is supposed to be holding -------------------
def _eviction_targets(self, claimed: list[dict]) -> tuple[
    set[tuple[str, str]],                            # endpoints to sweep this tick
    set[tuple[str, str]],                            # the admitted exclusive job's OWN endpoints
    dict[tuple[str, str], tuple[str, ...]],          # model ids each endpoint can be ADDRESSED by
] | None: ...                                        # None when nothing is RUNNING (unchanged early-out)

# --- the one safety set ----------------------------------------------------
def _protected_keys(self) -> set[tuple[str, str, str]]: ...

# --- phase 2: what the machine is ACTUALLY holding -------------------------
def _residency_snapshot(
    self, endpoints: set[tuple[str, str]], claimed: list[dict], budget_bytes: int | None,
) -> tuple[
    dict[tuple[str, str], list],                     # installed_by_endpoint
    set[tuple[str, str, str]],                       # believed_resident  (NEW output, M2)
    bool,                                            # over_budget; False whenever budget_bytes is None
]: ...

# --- the ONE place a believed-resident model is unloaded -------------------
def _unload_endpoint(
    self, engine_name: str, endpoint: str, installed: list, *,
    protected_keys: set[tuple[str, str, str]],
    own_keys: set[tuple[str, str, str]],
    reason: str,
    limit: int | None = None,
) -> set[tuple[str, str, str]]: ...                  # the keys actually RELEASED (M2)

# --- the barrier -----------------------------------------------------------
def _protection_refusal(
    self, claimed: list[dict], endpoints: set[tuple[str, str]],
    protected_keys: set[tuple[str, str, str]],
    own_keys_by_job: dict[int, set[tuple[str, str, str]]],
) -> set[int]: ...                                   # job ids refused BEFORE any HTTP

def _barrier(
    self, claimed: list[dict], endpoints: set[tuple[str, str]],
    installed_by_endpoint: dict[tuple[str, str], list],
    model_ids_by_endpoint: dict[tuple[str, str], tuple[str, ...]],
    protected_keys: set[tuple[str, str, str]],
    own_keys_by_job: dict[int, set[tuple[str, str, str]]],
) -> set[int]: ...                                   # job ids refused by an INFORMATIVE False

def _record_barrier_refusal(self, job_id: int) -> bool: ...   # True once BOTH bounds are met
def _requeue_refused(self, descriptor: dict) -> None: ...
def _fail_barrier_refused(
    self, descriptor: dict, engine_name: str, endpoint: str, span_seconds: float,
) -> None: ...

# --- the pass itself -------------------------------------------------------
def _evict_to_match_plan(
    self, claimed: list[dict], *, settings_row: JobSettings | None = None,
) -> set[int]: ...                                   # the ids this tick REFUSED to launch
```

**State on the worker, final shape:**

```python
self._inflight_refs: dict[tuple[int, uuid.UUID], list[dict]]   # keyed exactly like self._futures
self._barrier_refusals: dict[int, tuple[int, float]]           # {job_id: (count, first refusal monotonic)}
self._resident_keys: frozenset[tuple[str, str, str]]           # spec §3.6's affinity snapshot
```

**Constants, final values:**

```python
BARRIER_HOLDOFF_SECONDS = 45
MAX_BARRIER_REFUSALS = 3
MIN_BARRIER_REFUSAL_SPAN_SECONDS = 300
```

**Three invariants the whole task rests on, stated here so no commit can quietly break one:**

1. **`_unload_endpoint` is the one place a *believed-resident* model is unloaded.** It is not the only place `unload` is *called*: `_barrier`'s **precautionary** call has, by definition, no believed-resident model to iterate, so it calls `unload` directly and its result is read by the rule in §3.3(d)(4). Commit 2 states this at the call site. (The first draft claimed "the ONE place an unload is called" and was wrong — review M5.)
2. **The budget gate is already moved in commit 1.** Phases 1 and 2, protection, and the exclusive pass run regardless of `memory_budget_bytes`; only the capped, budget-driven pass stays gated. Commit 1's tests therefore prove something on the posture the field actually ran in (no budget set), instead of only under a budget.
3. **`_inflight_refs` and `_futures` are written and dropped together, by the same key, on every path.** See commit 1's `_launch`/`_prune_finished_futures` note — this is the leak the review's M4 found.

---

### Commit 1 — the pass sees the whole machine, and never destroys live work

**Covers:** §3.3(a) consumption, §3.3(b), §3.3(c), §3.3(e), §3.3(f), §3.1's rung-3 harvest, §3.6's residency cache write.

- [ ] **Step 1: Add the test helpers this task needs, then write the failing tests**

The plan's first draft used three helpers that **do not exist** (review m6). Add them to `models/queue/tests/test_worker.py`'s header first, beside the existing fixtures:

```python
# The endpoint every eviction test already uses as a literal. Named once
# here, because the eviction suite below now refers to it a few dozen times.
ENDPOINT = "http://fake:1"


def _installed(model_id: str, *, loaded: bool = False, loaded_size: int | None = None):
    """One `InstalledModel`, the shape `list_installed` returns. The
    existing tests build these inline; the eviction suite needs too many
    of them for that to stay readable."""
    return InstalledModel(model_id=model_id, loaded=loaded, loaded_size=loaded_size)
```

plus the two job-shaped helpers this suite's eviction tests call (every other name they use -- `_job`, `_set_budget`, `_ref`, `worker`, `register_engine` -- already exists in this module):

```python
def _running_job_holding(engine: str, endpoint: str, model_id: str) -> InferenceJob:
    """One RUNNING row holding exactly that model, which is what puts its
    key in `_protected_keys`' first half."""
    return _job(state=RUNNING, model_refs=[
        _ref(engine=engine, endpoint=endpoint, model_id=model_id),
    ])


def _admitted_exclusive(engine: str, endpoint: str, model_id: str) -> dict:
    """One claim descriptor of the shape `claim_and_admit` returns, for a
    job this tick admitted AS EXCLUSIVE -- the input `_evict_to_match_plan`
    takes. The row is created RUNNING too, because by the time the pass
    runs the claim has committed (which is why one RUNNING query covers
    "running union admitted")."""
    row = _running_job_holding(engine, endpoint, model_id)
    InferenceJob.objects.filter(pk=row.pk).update(exclusive=True)
    return {
        "id": row.pk, "kind": row.kind, "payload": {},
        "model_refs": [_ref(engine=engine, endpoint=endpoint, model_id=model_id)],
        "claim_token": uuid.uuid4(), "exclusive": True,
        "checkpoint": None, "attempts": 0,
    }
```

and give `FakeEngine` a `list_installed` counter plus the two declarations:

```python
class FakeEngine:
    """...

    DECLARES `unload_scope = "model"` and `residency_authority =
    "endpoint"` (queue memory governance, 2026-09-21). "model" is chosen
    deliberately: every SHIPPED assertion in `TestEviction` counts
    PER-MODEL unload calls -- `test_unneeded_resident_model_is_unloaded_
    when_over_budget` expects one call for `unneeded-model` while
    `needed-model` is protected at the SAME endpoint, and two tests assert
    `len(engine.unload_calls) == MAX_UNLOADS_PER_TICK`. Declaring this stub
    endpoint-scope would invert all of them (an endpoint-scope endpoint
    holding a protected key is skipped WHOLE). The endpoint-scope cases get
    their own stub below, so neither semantic is tested through a fixture
    that also has to keep the other one's assertions true."""

    unload_scope = "model"
    residency_authority = "endpoint"

    def __init__(self, ...):
        ...
        self.list_installed_calls = 0

    def list_installed(self, endpoint):
        self.list_installed_calls += 1
        return self._installed


class FakeEndpointScopeEngine(FakeEngine):
    """The other half of the seam: one call frees everything here, and the
    residency report is a process-local memo rather than a live endpoint.
    Same recording surface as `FakeEngine`."""

    unload_scope = "endpoint"
    residency_authority = "memo"
```

> **No shipped assertion in `TestEviction` changes under this choice** — check that before writing anything else: `.venv/bin/pytest -q models/queue/tests/test_worker.py -k Eviction` must be green with only the helper additions applied. If any assertion does move, stop and enumerate it here with its new expected call shape, because the commit message has to name it.

Then the new tests:

```python
    def test_an_undeclared_engine_is_assumed_endpoint_scope_and_memo_backed(
            self, worker, register_engine):
        """The safe assumptions: a wrong 'endpoint' guess costs a needless
        reload, a wrong 'model' guess destroys a live cold load; a wrong
        'memo' guess costs one precautionary call, a wrong 'endpoint'
        guess trusts an empty answer that may only mean this process
        forgot."""
        engine = register_engine(FakeEngineNoOptionalMethods("plain"))

        assert worker._unload_scope(engine) == "endpoint"
        assert worker._residency_authority(engine) == "memo"

    def test_a_nonsense_declaration_degrades_to_the_safe_default(self, worker, register_engine):
        engine = register_engine(FakeEngine("odd"))
        engine.unload_scope = "per-shard"
        engine.residency_authority = "vibes"

        assert worker._unload_scope(engine) == "endpoint"
        assert worker._residency_authority(engine) == "memo"

    def test_a_model_scope_endpoint_skips_only_the_protected_key(self, worker, register_engine):
        engine = register_engine(FakeEngine("e", installed=[
            _installed("needed", loaded=True), _installed("spare", loaded=True),
        ]))
        _running_job_holding("e", ENDPOINT, "needed")
        claimed = [_admitted_exclusive("e", ENDPOINT, "needed")]

        worker._evict_to_match_plan(claimed)

        assert engine.unload_calls == [(ENDPOINT, "spare")]

    def test_an_endpoint_scope_endpoint_is_skipped_whole_for_a_foreign_protected_key(
            self, worker, register_engine, caplog):
        """One call would take the protected model with it, so no call is
        made at all -- and the skip is a WARNING naming what protected it,
        because a human would otherwise have to infer it."""
        engine = register_engine(FakeEndpointScopeEngine("e", installed=[
            _installed("someone-elses", loaded=True), _installed("spare", loaded=True),
        ]))
        _running_job_holding("e", ENDPOINT, "someone-elses")
        claimed = [_admitted_exclusive("e", "http://other:2", "mine")]

        with caplog.at_level("WARNING", logger="models.queue.worker"):
            worker._evict_to_match_plan(claimed)

        assert engine.unload_calls == []
        assert any("protected" in r.getMessage() for r in caplog.records)

    def test_a_live_in_flight_attempts_model_protects_its_endpoint(self, worker, register_engine):
        """Q11: an orphaned-but-not-yet-readmitted attempt whose row is
        briefly back at `queued` while its handler is genuinely still
        mid-cold-load is invisible to a RUNNING-derived set. The cold load
        measured in minutes IS that window."""
        engine = register_engine(FakeEndpointScopeEngine("e", installed=[
            _installed("loading", loaded=True),
        ]))
        key = (99, uuid.uuid4())
        future = concurrent.futures.Future()
        worker._futures[key] = future
        worker._inflight_refs[key] = [
            {"engine": "e", "endpoint": ENDPOINT, "model_id": "loading"},
        ]
        _running_job_holding("e", "http://other:2", "mine")

        worker._evict_to_match_plan([_admitted_exclusive("e", "http://other:2", "mine")])

        assert engine.unload_calls == []
        future.set_result(None)

    def test_the_admitted_batch_is_protected_at_a_model_scope_endpoint(
            self, worker, register_engine):
        """Shipped behaviour, kept: today's `needed_keys` already covers
        "running union admitted", because the claim committed before this
        pass runs. An agent turn is planned EXCLUSIVE, so every chat turn
        runs this pass -- an unprotected definition would unload that
        turn's own warm chat model and cold-load it again on every single
        message, on hardware whose cold loads are measured in minutes."""
        engine = register_engine(FakeEngine("e", installed=[
            _installed("the-admitted-model", loaded=True), _installed("spare", loaded=True),
        ]))
        claimed = [_admitted_exclusive("e", ENDPOINT, "the-admitted-model")]

        worker._evict_to_match_plan(claimed)

        assert (ENDPOINT, "the-admitted-model") not in engine.unload_calls
        assert (ENDPOINT, "spare") in engine.unload_calls

    def test_the_admitted_jobs_own_endpoint_scope_endpoint_is_freed_anyway(
            self, worker, register_engine):
        """The ONE sanctioned exception (§3.3c), pinned as a PAIR with the
        test above so neither can drift: freeing that endpoint unavoidably
        takes the job's own model with it and there is no per-model call to
        make instead -- at worst one reload."""
        engine = register_engine(FakeEndpointScopeEngine("e", installed=[
            _installed("the-admitted-model", loaded=True),
        ]))
        claimed = [_admitted_exclusive("e", ENDPOINT, "the-admitted-model")]

        worker._evict_to_match_plan(claimed)

        assert engine.unload_calls == [(ENDPOINT, "the-admitted-model")]

    def test_another_jobs_key_at_that_same_endpoint_still_protects_it(
            self, worker, register_engine):
        """The exception has a named boundary: the admitted job's OWN keys
        at its OWN endpoint, never another job's and never a live
        attempt's."""
        engine = register_engine(FakeEndpointScopeEngine("e", installed=[
            _installed("the-admitted-model", loaded=True), _installed("someone-elses", loaded=True),
        ]))
        _running_job_holding("e", ENDPOINT, "someone-elses")
        claimed = [_admitted_exclusive("e", ENDPOINT, "the-admitted-model")]

        worker._evict_to_match_plan(claimed)

        assert engine.unload_calls == []

    def test_an_exclusive_admission_sweeps_every_registered_endpoint(
            self, worker, register_engine, settings):
        """Q4: a model left warm on an IDLE engine was never visited,
        because the endpoint set came from the running jobs' own refs."""
        settings.INFERENCE_DEFAULT_ENDPOINTS = {"idle": "http://idle:1"}
        idle = register_engine(FakeEngine("idle", installed=[_installed("warm", loaded=True)]))
        ...

        assert idle.unload_calls == [("http://idle:1", "warm")]

    def test_a_non_exclusive_admission_does_not_pay_the_wide_probe(
            self, worker, register_engine, settings):
        """The bound on the added HTTP: only the admission entitled to the
        whole machine gets the whole machine probed."""
        ...

        assert idle.list_installed_calls == 0

    def test_the_pass_runs_with_no_budget_set(self, worker, register_engine):
        """The posture the field actually ran in ("nothing offloaded at all
        until a budget was finally set"). Before the gate moved, every
        mechanism in this pass was dead code there -- which is also why
        this commit moves the gate rather than leaving it to a later
        one."""
        _set_budget(memory_budget_bytes=None)
        ...

        assert engine.unload_calls

    def test_the_capped_budget_pass_still_needs_a_budget(self, worker, register_engine):
        """It is the ONE mechanism whose decision is arithmetic against a
        number that does not exist."""
        _set_budget(memory_budget_bytes=None)
        ...  # a NON-exclusive admission, an unneeded resident model elsewhere

        assert engine.unload_calls == []

    def test_a_loaded_size_fills_the_third_footprint_rung(self, worker, register_engine):
        """No new HTTP: the snapshot is already on the wire and its numbers
        were being discarded."""
        connection = ModelConnection.objects.create(
            name="c", engine="e", endpoint=ENDPOINT, model_id="warm", capabilities=["chat"],
        )
        register_engine(FakeEngine("e", installed=[
            _installed("warm", loaded=True, loaded_size=4096),
        ]))
        ...

        connection.refresh_from_db()
        assert connection.engine_reported_footprint_bytes == 4096

    def test_a_missing_loaded_size_writes_nothing_not_a_zero(self, worker, register_engine):
        ...

        connection.refresh_from_db()
        assert connection.engine_reported_footprint_bytes is None

    def test_the_affinity_cache_is_the_belief_minus_what_this_pass_unloaded(
            self, worker, register_engine):
        """Spec §3.6 requires the subtraction in words: caching a key this
        same pass then unloaded would make the ordering preference
        systematically wrong."""
        engine = register_engine(FakeEngine("e", installed=[
            _installed("kept", loaded=True), _installed("spare", loaded=True),
        ]))
        _running_job_holding("e", ENDPOINT, "kept")
        ...

        assert ("e", ENDPOINT, "kept") in worker._resident_keys
        assert ("e", ENDPOINT, "spare") not in worker._resident_keys

    def test_an_endpoint_scope_unload_releases_every_believed_key_there(
            self, worker, register_engine):
        """`_unload_endpoint` returns the keys RELEASED, not the key it
        addressed: at endpoint scope one call frees everything believed
        resident there, and the affinity cache has to know that."""
        ...

        assert worker._resident_keys == frozenset()
```

Plus the two leak tests the review's M4 requires:

```python
@pytest.mark.django_db(transaction=True)
class TestTheInFlightRefsMapNeverLeaks:
    """`_inflight_refs` feeds `_protected_keys`, so an entry that outlives
    its attempt does not merely waste memory -- it PERMANENTLY protects a
    key and blocks eviction at that endpoint for the life of the process.
    That is exactly the leak class this whole track exists to close."""

    def test_a_refused_duplicate_submit_adds_to_neither_map(self, worker):
        job = _job(state=RUNNING)
        live_token = uuid.uuid4()
        future = concurrent.futures.Future()
        worker._futures[(job.pk, live_token)] = future
        worker._inflight_refs[(job.pk, live_token)] = [{"engine": "e", "endpoint": ENDPOINT,
                                                        "model_id": "loading"}]
        superseding = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(claim_token=superseding)

        worker._launch({"id": job.pk, "kind": job.kind, "payload": {},
                        "model_refs": [{"engine": "e", "endpoint": ENDPOINT, "model_id": "x"}],
                        "claim_token": superseding, "exclusive": False,
                        "checkpoint": None, "attempts": 0})

        assert (job.pk, superseding) not in worker._futures
        assert (job.pk, superseding) not in worker._inflight_refs
        future.set_result(None)

    def test_pruning_drops_both_maps_by_the_same_key(self, worker):
        key = (7, uuid.uuid4())
        done = concurrent.futures.Future()
        done.set_result(None)
        worker._futures[key] = done
        worker._inflight_refs[key] = [{"engine": "e", "endpoint": ENDPOINT, "model_id": "x"}]

        worker._prune_finished_futures()

        assert key not in worker._futures
        assert key not in worker._inflight_refs

    def test_a_pruned_attempt_stops_protecting_its_key(self, worker):
        ...

        assert ("e", ENDPOINT, "x") not in worker._protected_keys()
```

- [ ] **Step 2: Implement commit 1**

`models/contracts/engines/base.py`, in the optional-seam block — both attributes, the getattr/degrade idiom, and both safe defaults:

```python
    # --- Optional: what an unload frees, and what residency is worth ------
    #
    # Two DECLARATIONS, read by the execution queue's eviction pass the
    # same defensive way the two methods above are (`getattr(engine,
    # "unload_scope", None)`), never called and never required.
    #
    # `unload_scope`: "model" -- `unload(endpoint, model_id)` releases that
    # model and leaves others. "endpoint" -- the call releases everything
    # at the endpoint and `model_id` is addressing, not selection.
    # ABSENT -> the queue assumes "endpoint", the safe assumption: it can
    # cost a needless reload, never a destroyed cold load.
    #
    # `residency_authority`: "endpoint" -- `list_installed`'s `loaded`
    # flags come from a real residency endpoint the engine answers live, so
    # "nothing resident" is a FACT. "memo" -- they come from a TTL'd,
    # process-local belief the adapter maintains itself, so "nothing
    # resident" means "this process does not remember anything", which a
    # restart alone produces. ABSENT -> the queue assumes "memo", the safe
    # default: it triggers a precautionary barrier call rather than
    # trusting an empty answer.
    unload_scope: str
    residency_authority: str
```

`models/queue/worker.py`:

- `_unload_scope` / `_residency_authority` exactly as the end-state block declares, each with the "anything absent or unrecognised reads as the safe value" docstring.
- `_inflight_refs` in `__init__`, documented as the companion of `_futures`:

  ```python
        # Model refs per IN-FLIGHT ATTEMPT, keyed IDENTICALLY to
        # `self._futures`. Eviction's protected set needs the KEYS a live
        # attempt holds, and a `Future` does not carry them; the claim
        # descriptor does. The only query-free alternative -- re-reading
        # `InferenceJob.model_refs` for the live job ids -- would put a
        # query on the 0.5s tick path.
        #
        # WRITTEN AND DROPPED IN LOCKSTEP WITH `self._futures`, on EVERY
        # path, and that is not a nicety: an entry here that outlives its
        # attempt PERMANENTLY protects a key and blocks eviction at that
        # endpoint for the life of the process. So the write sits in the
        # SAME statement block as the `_futures` insert at the bottom of
        # `_launch` -- BELOW the duplicate-submit refusal's early return,
        # never at the top of the method -- and `_prune_finished_futures`
        # drops from both maps by the same key.
        #
        # Tick-thread-only, exactly like `_futures` (see
        # `_prune_finished_futures`'s own docstring): no lock, because
        # there is no second writer.
        self._inflight_refs: dict[tuple[int, uuid.UUID], list[dict]] = {}
  ```

- `_launch`'s bottom, after Task 8's refusal check:

  ```python
        # ONE statement block, so the two maps can never disagree about
        # which attempts are live (see `_inflight_refs`'s declaration).
        self._futures[(job_id, claim_token)] = self._executor.submit(self._execute, descriptor)
        self._inflight_refs[(job_id, claim_token)] = descriptor["model_refs"]
  ```

- `_prune_finished_futures` rebuilds **both** maps from the same surviving key set, and its docstring gains one sentence naming why the second map must not be forgotten.
- `_protected_keys(self)` — **no `claimed` parameter** (review n2: everything comes from the `RUNNING` query, which already covers "running ∪ admitted", and from `_inflight_refs`). Both maps are read without a lock, because both are tick-thread-only. Docstring: the two halves and why each matters, per §3.3(c).
- `_eviction_targets` returns the end-state triple. The swept set is the running jobs' endpoints, unioned with `registered_endpoints()`'s **only when this tick admits an exclusive job**; the model-id map comes from the same call and is what makes a foreign endpoint addressable at all. Docstring carries §3.3(e)'s "only on an exclusive-admitting tick" bound and the stated `over_budget` consequence.
- `_residency_snapshot` gains `budget_bytes: int | None` (computing `over_budget` only when it is not `None`, `False` otherwise), returns `believed_resident` as its second element, and harvests rung 3 per loaded model that reports a size:

  ```python
                if model.loaded and getattr(model, "loaded_size", None):
                    # RUNG 3 (spec §3.1), from a snapshot already on the
                    # wire -- never a call made for this purpose. Only a
                    # POSITIVE reading is written: a `None` or zero size
                    # writes nothing, never a zero, because a zero would
                    # read back as a real "this model is free" answer.
                    record_engine_reported_footprint(
                        engine_name, endpoint, model.model_id, model.loaded_size,
                    )
  ```

- `_unload_endpoint` — the end-state signature, returning **the keys actually released**:

  ```python
        """Unload what may be unloaded at ONE endpoint, and return the set
        of keys that were actually RELEASED.

        THE RETURN IS A KEY SET, NOT A COUNT, for one concrete reason: at
        `"endpoint"` scope a single call frees EVERY believed-resident
        model there, so "what was released" is not "the key that was
        addressed", and spec §3.6's affinity cache has to subtract the
        real set.

        THE PROTECTION RULE, applied per scope (spec §3.3c):

        - `"model"` scope -- skip protected keys one by one; everything
          else at the endpoint is unloaded individually.
        - `"endpoint"` scope -- ONE call frees everything here, so the
          WHOLE endpoint is skipped if any protected key lives at it...
        - ...UNLESS every protected key here belongs to `own_keys`: the
          admitted exclusive job's OWN keys at its OWN endpoint. Freeing
          that endpoint unavoidably takes its own model with it and there
          is no per-model call to make instead, so the barrier proceeds and
          the job pays at worst one reload. `own_keys` is EMPTY for every
          other caller, which is what keeps this exception to the one case
          it is written for.

        `reason` is the log vocabulary's "why" (`not needed at an exclusive
        endpoint` / `over budget` / `precautionary barrier`). DECLARED HERE
        IN COMMIT 1 AND FIRST READ IN COMMIT 3, deliberately: the signature
        is final from the start so no later commit revises it.

        `limit` caps the calls issued here, for the budget-driven pass's
        share of `MAX_UNLOADS_PER_TICK`; `None` is uncapped.

        `self._maybe_heartbeat()` is called after EVERY unload call, not
        once around the loop -- that is what makes an uncapped exclusive
        pass safe, and hoisting it out reintroduces the stale-row window
        this pass was fixed to close.
        """
  ```

- `_evict_exclusive_endpoints` / `_evict_for_budget` become thin callers of `_unload_endpoint`, taking the swept set and `protected_keys` (which replaces `needed_keys` entirely) and, for the exclusive pass only, `own_keys`. Each returns the union of released keys.
- `_evict_to_match_plan`: the gate moves (phases 1 and 2 and the exclusive pass run regardless of the budget; only the capped pass checks `budget_bytes is not None and over_budget`), and it returns `set()` in this commit — **the final signature, from the first commit**, so commit 2 adds behaviour rather than changing a shape.
- The affinity cache, written at the end of the pass from values the interfaces now actually produce:

  ```python
        # The affinity snapshot (spec §3.6), cached for the NEXT claim
        # round: the pre-eviction belief MINUS what this pass actually
        # released. `believed_resident` comes from `_residency_snapshot`;
        # `released` is the union of what each `_unload_endpoint` call
        # returned. Naming a model this same pass then unloaded would make
        # the ordering preference systematically wrong.
        #
        # Wholesale-replaced every admitting tick, bounded by the resident
        # set, written and read on the tick thread alone: no lock and no
        # pruning contract.
        self._resident_keys = frozenset(believed_resident - released)
  ```

- [ ] **Step 3: Verify commit 1**

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
.venv/bin/pytest -q models/queue/tests/test_worker.py models/contracts/tests/ models/registry/tests/
```

Expected: green, **with every shipped `TestEviction` assertion unchanged** (that is what `FakeEngine.unload_scope = "model"` buys) and `TestTheSingleSettingsReadPerTick` still at its existing number — the rewrite must add no `JobSettings` read.

- [ ] **Step 4: Commit 1**

```bash
git add models/contracts/engines/base.py models/queue/worker.py models/queue/tests/test_worker.py
git commit -m "fix(queue): eviction sees the whole machine and never destroys live work

Three defects with one thesis: the pass was blind where it mattered.

It assumed per-model unload granularity, so on an engine whose unload frees
the whole endpoint it evicted a needed checkpoint out from under a live
attempt -- and the existing per-model loop already made exactly that call
for a non-needed model sharing an endpoint with a needed one. The seam now
carries two optional declarations read with getattr and safe defaults
(unload_scope -> 'endpoint', residency_authority -> 'memo'), and every
believed-resident unload routes through one method obeying one protected
set: every RUNNING key (this tick's admitted batch included, as today's
needed_keys already did) plus every attempt still in flight here, which is
the orphaned-mid-cold-load case a RUNNING query cannot see.

It only ever visited endpoints the RUNNING jobs named, so a model left warm
on an idle engine was never touched. On an exclusive admission the endpoint
set is now the union with every registered engine endpoint -- which also
makes over_budget mean something wider on those ticks (deliberate:
under-counting resident memory is the direction that crashes hosts).

And the whole pass returned early when no memory budget was set, which is
the posture the field ran in, so everything above would have been dead code
exactly where it was needed. Only the capped budget-driven pass stays
gated now. The residency snapshot also fills the third footprint rung from
numbers it was already fetching and discarding, and caches the believed
set minus what this pass released for the ordering preference landing
later.

No shipped TestEviction assertion changes: the existing stub declares
unload_scope='model', which is what its per-model call counts mean; the
endpoint-scope cases get their own stub."
```

---

### Commit 2 — the exclusive barrier

**Covers:** §3.3(d)(1) through (5), §9.4, §9.5, §9.8, §9.9, §9.10, owner decision 6.

**The five parts, in implementation order:**

1. **Scope** — on a tick that admits an exclusive job the barrier covers **every endpoint in the swept set** (already final from commit 1), not only the admitted job's own. A warm model on an idle *foreign* engine is the literal host-crash shape.
2. **Protection first, and before any HTTP** — any endpoint holding a protected key (the §3.3(c) exception included) means the admitted job is **not launched this tick**. Evaluated **before** phase 2's residency snapshot: `protected_keys` comes from database rows and this process's own maps and needs no network, while evaluating it after the snapshot would pay a full cross-engine probe on every 0.5 s tick for the entire life of the protecting attempt — a cold load measured in minutes. This is `_evict_to_match_plan`'s **fifth ordering rule**.
3. **The calls** — at each remaining endpoint, every believed-resident non-protected model is unloaded through `_unload_endpoint`. The **precautionary** call is made only when `list_installed` was unavailable or raised, **or** when it reports nothing resident **and** the endpoint's engine declares (or defaults to) `residency_authority="memo"` — addressed with any `model_id` the swept set's model-id map carries for that endpoint, or the admitted job's own ref when the endpoint is its own. An engine declaring `residency_authority="endpoint"` that reports nothing gets **no** precautionary call. **This one call does not route through `_unload_endpoint`** — there is no believed-resident model to iterate — and the call site says so, which is the correction to the first draft's "the ONE place an unload is called".
4. **Reading the answer** — `False` is honoured **only** for a call made against a *believed-resident* model. A `False` from a *precautionary* call is logged at INFO and **does not block the launch**: the adapter cannot tell "nothing freed" from "nothing to free", and an empty endpoint is the common case after a restart.
5. **Bounded refusal, in count AND wall clock — and only for an INFORMATIVE refusal.** A refused job takes a `not_before` hold-off so it is not re-claimed every 0.5 s; it is failed only once it has accumulated `MAX_BARRIER_REFUSALS` **informative** refusals **and** `MIN_BARRIER_REFUSAL_SPAN_SECONDS` has passed since the first. A successful barrier resets the count.

   **A PROTECTION refusal never increments that counter** (review M6). Spec §3.3(d)(5) counts informative refusals; §3.3(d)(2) says the protection wait "is genuinely bounded (the live attempt ends)". Since an agent turn is planned exclusive, counting protection refusals would fail three consecutive chat turns for an ordinary long-running foreign job at a shared endpoint — a job the queue was correctly waiting for. Only `_barrier`'s believed-resident `False` calls `_record_barrier_refusal`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
class TestTheExclusiveBarrier:

    def test_a_protected_endpoint_refuses_the_launch_before_any_http(
            self, worker, register_engine):
        """The fifth ordering rule: no `list_installed` may be called on a
        tick refused for a protected endpoint -- otherwise the refusal
        costs a full cross-engine probe every 0.5s for the whole life of
        the protecting attempt."""
        engine = register_engine(FakeEndpointScopeEngine("e", installed=[
            _installed("someone-elses", loaded=True),
        ]))
        ...

        assert refused == {admitted.pk}
        assert engine.list_installed_calls == 0

    def test_a_protection_refusal_never_counts_toward_the_failure_bound(
            self, worker, register_engine, monkeypatch):
        """An agent turn is planned exclusive, so counting these would fail
        three consecutive chat turns for an ordinary long-running foreign
        job the queue was CORRECTLY waiting for. The protection wait is
        bounded by the live attempt's own end; the informative barrier
        refusal is not."""
        monkeypatch.setattr(worker_module, "MIN_BARRIER_REFUSAL_SPAN_SECONDS", 0)
        ...

        for _ in range(4):
            worker._evict_to_match_plan(claimed)

        job.refresh_from_db()
        assert job.state == QUEUED
        assert worker._barrier_refusals == {}

    def test_a_believed_resident_false_blocks_the_launch_and_warns(
            self, worker, register_engine, caplog):
        ...

        assert refused == {job.pk}
        assert any(r.levelname == "WARNING" for r in caplog.records)

    def test_a_precautionary_false_does_not_block_the_launch(
            self, worker, register_engine, caplog):
        """A cold, empty endpoint is the COMMON case after a restart, and
        the adapter returns False there after burning its settle poll. A
        rule that refused on it would make an exclusive job unlaunchable
        not for one tick but for ever."""
        engine = register_engine(FakeEndpointScopeEngine("e", installed=[]))
        engine.unload_returns = False
        ...

        assert refused == set()
        assert engine.unload_calls          # the precautionary call WAS made
        assert all(r.levelname != "WARNING" for r in caplog.records)

    def test_an_authoritative_empty_endpoint_gets_no_precautionary_call(
            self, worker, register_engine):
        """It actually knows nothing is resident: there is nothing to
        barrier and nothing its answer could add. This narrowing is what
        keeps a 30s no-rise poll off every chat turn."""
        engine = register_engine(FakeEngine("e", installed=[]))   # residency_authority="endpoint"
        ...

        assert engine.unload_calls == []

    def test_a_memo_backed_empty_endpoint_does_get_one(self, worker, register_engine):
        """The safe default, pinned as its own case: absent or "memo" means
        an empty answer may only mean this process forgot."""
        engine = register_engine(FakeEndpointScopeEngine("e", installed=[]))
        ...

        assert engine.unload_calls

    def test_an_endpoint_whose_snapshot_raised_gets_one_too(self, worker, register_engine):
        ...

    def test_a_foreign_endpoint_with_no_registered_model_id_is_not_barriered(
            self, worker, register_engine, settings):
        """The named residual (spec §11): the unload seam takes a model_id
        and a configured endpoint with no connection row supplies none."""
        ...

        assert engine.unload_calls == []

    def test_three_informative_refusals_inside_the_span_do_not_fail_the_job(
            self, worker, monkeypatch):
        monkeypatch.setattr(worker_module, "MIN_BARRIER_REFUSAL_SPAN_SECONDS", 10_000)
        ...

        job.refresh_from_db()
        assert job.state == QUEUED

    def test_both_bounds_together_fail_it_with_an_operator_readable_error(
            self, worker, monkeypatch):
        monkeypatch.setattr(worker_module, "MIN_BARRIER_REFUSAL_SPAN_SECONDS", 0)
        ...

        job.refresh_from_db()
        assert job.state == FAILED
        # The specced sentence, not a substring that cannot fail: an
        # earlier draft asserted `"e" in job.error`, which is true of
        # almost any English sentence. Assert the engine NAME as the error
        # actually renders it, the endpoint, and the count.
        assert "did not release memory" in job.error
        assert "engine 'e'" in job.error
        assert ENDPOINT in job.error
        assert "3 attempts" in job.error

    def test_a_successful_barrier_resets_the_count(self, worker):
        ...

        assert worker._barrier_refusals.get(job.pk) is None

    def test_a_refused_job_takes_a_holdoff_and_keeps_its_attempts(self, worker):
        """It never ran: state back to queued, token cleared, attempts
        untouched, `not_before` in the future."""
        ...

        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.claim_token is None
        assert job.attempts == 0
        assert job.not_before > timezone.now()

    def test_every_refusal_path_pops_the_active_token(self, worker):
        """A refused descriptor NEVER reaches `_launch`, so no Future is
        ever created for it and `_prune_finished_futures` can never clean
        it up -- exactly what `_requeue_unlaunched`'s own docstring warns
        about. Both refusal writers pop it first."""
        ...

        with worker._active_lock:
            assert worker._active_tokens == {}

    def test_a_failed_refusal_pops_the_active_token_too(self, worker, monkeypatch):
        monkeypatch.setattr(worker_module, "MIN_BARRIER_REFUSAL_SPAN_SECONDS", 0)
        ...

        with worker._active_lock:
            assert worker._active_tokens == {}

    def test_a_refused_job_is_never_launched_this_tick(self, worker, monkeypatch):
        launched = []
        monkeypatch.setattr(worker, "_launch", lambda d: launched.append(d["id"]))
        ...

        assert launched == []
```

and in `models/queue/tests/test_claim.py`:

`_queued_job` is one more row helper for the same module — a plain queued row that can carry a `not_before`:

```python
def _queued_job(*, priority: int = 100, **extra) -> InferenceJob:
    return _job(priority=priority, state=QUEUED, **extra)
```

```python
class TestTheHoldOff:
    def test_a_job_held_off_into_the_future_is_not_a_candidate(self):
        _queued_job(not_before=timezone.now() + timedelta(seconds=60))

        assert claim_and_admit("w", stale_after_seconds=120) == []

    def test_it_becomes_a_candidate_again_once_the_time_passes(self):
        job = _queued_job(not_before=timezone.now() - timedelta(seconds=1))

        assert [d["id"] for d in claim_and_admit("w", stale_after_seconds=120)] == [job.pk]

    def test_a_null_not_before_is_claimable_as_always(self):
        job = _queued_job()

        assert [d["id"] for d in claim_and_admit("w", stale_after_seconds=120)] == [job.pk]

    def test_a_held_off_head_does_not_block_the_job_behind_it(self):
        """Time-bounded and self-clearing: the exclusion is what makes the
        deadlock proof survive (ADR amendment §3), so a peer may be
        admitted ahead of a held-off job and the held-off job returns to
        its own head position the moment the hold-off expires."""
        _queued_job(priority=100, not_before=timezone.now() + timedelta(seconds=60))
        behind = _queued_job(priority=100)

        assert [d["id"] for d in claim_and_admit("w", stale_after_seconds=120)] == [behind.pk]
```

- [ ] **Step 2: Implement commit 2**

Constants (final values, from the end-state block), each with its reasoning comment:

```python
# How long a barrier-refused (or protection-refused) job waits before it
# may be claimed again, written to `InferenceJob.not_before`. COMFORTABLY
# LONGER THAN ONE UNLOAD TIMEOUT (30s on the engine that polls), so a
# refused job is not re-claimed on every 0.5s tick and the retries the
# refusal bound counts are genuinely spaced.
BARRIER_HOLDOFF_SECONDS = 45

# How many INFORMATIVE barrier refusals a job may accumulate before it is
# failed -- and see the span below, which must ALSO be satisfied. A
# PROTECTION refusal is deliberately not counted here: that wait is
# bounded by the live attempt's own end, and since an agent turn is
# planned exclusive, counting it would fail three consecutive chat turns
# for an ordinary long-running foreign job at a shared endpoint.
MAX_BARRIER_REFUSALS = 3

# ...because counting refusals alone is a trap. An informative `False` is
# exactly what a BUSY engine returns (the adapter withholds True while a
# prompt is still running), so three refusals could elapse in barely more
# than the time three unload calls take. A job is failed only once BOTH
# bounds are met, so "three attempts" can never mean "a second and a half".
MIN_BARRIER_REFUSAL_SPAN_SECONDS = 300
```

`_evict_to_match_plan`'s docstring gains the **fifth ordering rule** paragraph (the protection check runs before the residency snapshot, and why that ordering is about cost rather than taste), and the function now returns the refused set instead of `set()`.

`_protection_refusal` — part 2, before any HTTP; one WARNING per refusal naming job, engine and endpoint; **never touches `_barrier_refusals`**.

`_barrier` — parts 3 and 4. A precautionary call is issued when `installed_by_endpoint` has no entry for the endpoint (snapshot unavailable or raised) **or** the entry has no loaded model **and** `self._residency_authority(engine_obj) == "memo"`. Its call site carries:

```python
                # THE PRECAUTIONARY CALL DOES NOT ROUTE THROUGH
                # `_unload_endpoint`, and cannot: there is no
                # believed-resident model to iterate. `_unload_endpoint` is
                # the one place a BELIEVED-RESIDENT model is unloaded; this
                # is the one place a call is made precisely because the
                # belief is worth nothing. Its `False` is INFO and does not
                # refuse (§3.3d(4)).
```

`_record_barrier_refusal(job_id) -> bool` — part 5, `{job_id: (count, first monotonic)}`, returning `True` only when the count has reached `MAX_BARRIER_REFUSALS` **and** the span has elapsed.

`_requeue_refused(descriptor)` and `_fail_barrier_refused(descriptor, ...)` — **both pop `self._active_tokens[job_id]` under `self._active_lock` BEFORE their conditional UPDATE**, matching `_requeue_unlaunched`'s shape and for the reason that method's own docstring gives: a refused descriptor never reaches `_launch`, so no `Future` exists for it and `_prune_finished_futures` can never clean it up; a lingering token would have `_maybe_heartbeat` refreshing a row that is `queued` again, for ever. `_requeue_refused` then writes state `queued`, token cleared, **`attempts` untouched — it never ran** — plus `not_before = now + BARRIER_HOLDOFF_SECONDS`. `_fail_barrier_refused` writes the terminal row with an operator-readable error naming the engine, the endpoint and the elapsed span ("the image engine at http://… did not release memory after three attempts over N minutes") and schedules the kind's `on_terminal` hook with `transaction.on_commit`, exactly as `_sweep_orphans`'s second-orphaning branch does.

A **successful** barrier for a job clears `self._barrier_refusals.pop(job_id, None)`.

`tick()`:

```python
        refused = self._evict_to_match_plan(claimed, settings_row=settings_row)
        self._maybe_heartbeat()

        if self._stopping.is_set():
            # A refused descriptor has already been handed back (and its
            # token popped) by the refusal writers -- requeueing it twice
            # would clobber the hold-off just written to it.
            self._requeue_unlaunched([d for d in claimed if d["id"] not in refused])
            return

        for descriptor in claimed:
            if descriptor["id"] in refused:
                continue
            self._launch(descriptor)
```

`models/queue/claim.py`'s candidate query gains the filter, and step 3's docstring paragraph gains the deadlock note:

```python
        now = timezone.now()
        candidate_rows = list(
            InferenceJob.objects.select_for_update(skip_locked=True)
            .filter(state=QUEUED)
            .filter(Q(not_before__isnull=True) | Q(not_before__lte=now))
            .order_by("priority", "id")[:CANDIDATE_WINDOW]
        )
```

```
       `not_before` (spec §3.3d) is the ONE new admission-side filter this
       track adds -- an extra `WHERE` on this same SELECT, not a second
       statement. A job the worker refused to launch this tick is excluded
       until its hold-off expires, so it cannot be re-claimed on every 0.5s
       tick against an engine that is still holding memory. The no-backfill
       deadlock proof survives because the exclusion is time-bounded and
       SELF-CLEARING -- the job returns to its own head position the moment
       the hold-off passes, and an effectively-exclusive head is still
       admitted alone the instant the machine is idle -- so nothing can
       wait behind it for ever.
```

- [ ] **Step 3: Verify commit 2**

```bash
.venv/bin/pytest -q models/queue/tests/
```

**`not_before` adds no query** — it is an extra `WHERE` on the existing candidate `SELECT`. There is no query-count assertion in `models/queue/tests/test_claim.py` to re-pin (verified against the tree on 2026-09-21: `grep -rn 'num_queries\|CaptureQueriesContext' models/queue/tests/` hits `test_worker.py` only), and `TestTheSingleSettingsReadPerTick` counts `jobs_jobsettings` statements alone and is untouched. **Do not write re-pin language into this commit message.**

- [ ] **Step 4: Commit 2**

```bash
git add models/queue/worker.py models/queue/claim.py models/queue/tests/
git commit -m "feat(queue): an exclusive job waits for the memory it was promised

The field saw an exclusive job's handler start before the memory it was
promised had been released, loading a large checkpoint on top of a
resident one. But False from the barrier-polling adapter is ambiguous --
an empty endpoint returns False after burning its settle poll -- so
refusing on any False would make an exclusive job unlaunchable for ever,
not for one tick.

So: protection is checked FIRST and costs no HTTP (the fifth ordering
rule); False is honoured only for a call made against a believed-resident
model; the precautionary call is issued only where residency_authority
says the belief is unauthoritative, and is the one unload this pass makes
outside _unload_endpoint because there is no believed model to iterate;
and the refusal bound is count AND wall clock, because an informative
False is exactly what a busy engine returns.

A PROTECTION refusal is deliberately not counted toward that bound -- that
wait ends when the live attempt does, and an agent turn is planned
exclusive, so counting it would fail three consecutive chat turns for a
foreign job the queue was correctly waiting for.

A refused job takes a durable not_before hold-off -- an extra WHERE on the
existing candidate SELECT, the one new admission-side filter this track
adds -- and both refusal writers pop the job's _active_tokens entry first,
because a refused descriptor never reaches _launch and nothing else would
ever clean it up."
```

---

### Commit 3 — say what happened

**Covers:** §3.3(g) in full, plus the two query-count pins spec §5 names for this pass.

A successful eviction is silent today; `False` is the only thing ever logged. Every decision and every result gets one INFO line with a stable vocabulary, and WARNING is reserved for what an operator can act on.

- [ ] **Step 1: Write the failing tests**

```python
    def test_one_line_per_tick_that_evicts_at_all(self, worker, register_engine, caplog):
        ...
        line = next(r.getMessage() for r in caplog.records if "eviction pass" in r.getMessage())
        assert "exclusive admission" in line
        assert "budget unset" in line

    def test_one_line_per_unload_attempt_with_the_stable_vocabulary(
            self, worker, register_engine, caplog):
        ...
        line = next(r.getMessage() for r in caplog.records if "unload" in r.getMessage())
        for fragment in ("e", ENDPOINT, "spare", "model", "not needed at an exclusive endpoint"):
            assert fragment in line
        assert "accepted" in line or "refused" in line or "unavailable" in line

    def test_the_capped_pass_says_when_it_hits_its_cap(self, worker, register_engine, caplog):
        ...
        assert any("cap" in r.getMessage() for r in caplog.records)

    def test_a_snapshot_that_raised_says_so(self, worker, register_engine, caplog):
        ...

    def test_warnings_are_reserved_for_what_an_operator_can_act_on(
            self, worker, register_engine, caplog):
        """An ordinary successful eviction produces INFO only."""
        ...
        assert all(r.levelname == "INFO" for r in caplog.records)
```

and the two **new** query-count pins (authored here, not re-pinned — see Ruling B):

```python
@pytest.mark.django_db
class TestTheWidenedSweepDoesNotQueryPerEndpoint:
    """Spec §5 names a query-count pin for the widened sweep.

    WHAT IT ACTUALLY COSTS, measured rather than assumed: **one** query
    per exclusive-admitting tick -- `_eviction_targets` calls
    `registered_endpoints()` with no `connections=`, so that helper
    fetches the connection rows itself, once, however many endpoints come
    back. The rest of the sweep's cost is `list_installed` HTTP, which no
    query counter sees.

    SO THE FACT WORTH PINNING IS THE SHAPE, NOT THE ZERO: the obvious way
    to build the swept set is to look up each endpoint's connection rows
    inside the loop, which would put N queries on an admitting tick. This
    asserts the same LITERAL at one endpoint and at four.

    NON-VACUOUS, and this is the part an earlier draft got wrong: each
    extra idle endpoint must REGISTER A REAL (fake) ENGINE. An endpoint
    whose engine name does not resolve is dropped by
    `_get_engine_or_none` before the sweep ever reaches it, so three
    unregistered addresses would leave the count flat no matter how the
    swept set were implemented -- a pin that cannot fail."""

    def test_an_exclusive_admitting_tick_pays_the_same_queries_at_one_and_four_endpoints(
            self, worker, register_engine, settings, django_assert_num_queries):
        register_engine(FakeEngine("e", installed=[_installed("warm", loaded=True)]))
        _running_job_holding("e", ENDPOINT, "warm")
        claimed = [_admitted_exclusive("e", ENDPOINT, "warm")]

        settings.INFERENCE_DEFAULT_ENDPOINTS = {"e": ENDPOINT}
        with django_assert_num_queries(<literal>):
            worker._evict_to_match_plan(claimed)

        # Each idle endpoint gets its OWN registered engine, so all three
        # genuinely enter the sweep (and genuinely get probed) instead of
        # being dropped for an unresolvable engine name.
        for index in (1, 2, 3):
            register_engine(FakeEngine("idle%d" % index,
                                       installed=[_installed("warm", loaded=True)]))
        settings.INFERENCE_DEFAULT_ENDPOINTS = {
            "e": ENDPOINT, "idle1": "http://idle:1",
            "idle2": "http://idle:2", "idle3": "http://idle:3",
        }
        with django_assert_num_queries(<the same literal>):
            worker._evict_to_match_plan(claimed)
```

> Fill `<literal>` from the first red run's reported count, then re-run. Record the number in the commit message. **Sanity-check the pin before trusting it:** the second block must actually probe all four endpoints — assert `idle1.list_installed_calls == 1` alongside the count, or the non-vacuity argument above is unproven.

- [ ] **Step 2: Implement commit 3**

The stable vocabulary (§3.3g):

- **one INFO per tick that evicts at all** — `"worker: eviction pass (%s): swept %s endpoints, %s resident, %s admitted marginal, budget %s"`, trigger `exclusive admission` / `over budget`, budget rendered as `unset` when `None`;
- **one INFO per unload attempt** — engine, endpoint, model, scope (`model`/`endpoint`), why (`not needed at an exclusive endpoint` / `over budget` / `precautionary barrier` — this is `_unload_endpoint`'s `reason`, declared in commit 1 and first read here), and result (`accepted` / `refused` / `unavailable`);
- **one line per skip a human would otherwise have to infer** — an endpoint protected by a live attempt (WARNING), an engine offering no `unload` (INFO, still once per engine+method via `_warn_missing_method_once`), a snapshot call that raised (INFO), the capped pass hitting its cap (INFO).

WARNING is reserved for what an operator can act on: an informative barrier refusal, a protected endpoint blocking an exclusive launch, a job failed after `MAX_BARRIER_REFUSALS`, and a refused footprint lowering (Task 2's, in the registry).

- [ ] **Step 3: Verify commit 3**

```bash
.venv/bin/pytest -q models/queue/tests/ models/registry/tests/ models/contracts/tests/
```

- [ ] **Step 4: Commit 3**

```bash
git add models/queue/worker.py models/queue/tests/test_worker.py
git commit -m "feat(queue): eviction says what it did

A successful eviction was silent -- an unload returning False was the only
thing this pass ever logged, so an operator watching a host fill up had no
way to tell 'nothing needed evicting' from 'everything was skipped'.

Every decision and every result now gets one INFO line with a stable
vocabulary: one per tick that evicts at all (trigger, endpoints swept,
resident bytes, admitted marginal, budget or 'unset'), one per unload
attempt (engine, endpoint, model, scope, why, result), and one per skip a
human would otherwise have to infer. WARNING is reserved for what an
operator can act on.

Adds the widened-sweep query-count pin spec section 5 names: an
exclusive-admitting tick costs the same N queries at one endpoint and at
four. The sweep pays one connection fetch however many endpoints come
back; the rest of its cost is list_installed HTTP, which no query counter
sees. Each idle endpoint in the pin registers its own engine, so all four
genuinely enter the sweep rather than being dropped for an unresolvable
engine name. The number is a literal, proven red first."
```

---
## Task 13: A waiting row says why it is waiting

**Spec:** §3.6's closing paragraph (the R3-2 ruling: a delay the queue imposes deliberately is never left for an operator to infer), §5 "Queue surfaces".

**Files:**
- Modify: `models/queue/views.py` (`_present_row`)
- Modify: `models/queue/templates/jobs/queue.html` (the waiting row's `job-meta`)
- Modify: `models/queue/tests/test_views.py` (append `TestHoldOffReading`)

**Interfaces:**
- Produces: `_present_row`'s `hold_off_until: datetime | None` — the row's `not_before` **only while it is still in the future**, `None` otherwise.
- Consumes: `QueueRow.not_before` (Task 10).

**Display only, and it must stay that way:** server-rendered from the row the page already loads, revealed by the page's existing refresh — **no new query, no polling, no script**. The zero-JS doctrine governs here as everywhere else.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
class TestHoldOffReading:
    """A job the queue is deliberately declining to consider for the next
    forty-five seconds is a stronger case of an unexplained delay than a
    reordering, and it would otherwise surface only as a log line -- which
    fails this track's own observability thesis."""

    def test_a_future_hold_off_renders_on_the_waiting_row(self, client, admin_signed_in):
        make_queue_job(kind="test.k", not_before=timezone.now() + timedelta(minutes=2))

        body = client.get(reverse("jobs-queue")).content.decode()

        assert "waiting for engine memory" in body
        assert "retries at" in body

    def test_a_past_hold_off_renders_nothing(self, client, admin_signed_in):
        make_queue_job(kind="test.k", not_before=timezone.now() - timedelta(minutes=2))

        body = client.get(reverse("jobs-queue")).content.decode()

        assert "waiting for engine memory" not in body

    def test_an_unheld_job_renders_nothing(self, client, admin_signed_in):
        make_queue_job(kind="test.k")

        assert "waiting for engine memory" not in client.get(
            reverse("jobs-queue")).content.decode()

    def test_the_reading_costs_no_extra_query(self, client, admin_signed_in,
                                              django_assert_num_queries):
        """It comes off the row `queue_snapshot` already loaded.

        A LITERAL, never a computed baseline: an earlier draft called a
        `_queue_page_query_count(client)` helper (which does not exist)
        and asserted against its own answer -- a test that passes whatever
        the page does, which the plan's own Global Constraints forbid.
        Fill the number from the first red run and write it in."""
        for index in range(3):
            make_queue_job(kind="test.k%d" % index,
                           not_before=timezone.now() + timedelta(minutes=2))

        with django_assert_num_queries(<literal>):
            client.get(reverse("jobs-queue"))

    def test_the_reading_does_not_grow_with_the_number_of_held_off_rows(
            self, client, admin_signed_in, django_assert_num_queries):
        """The non-vacuous half: three held-off rows cost the same as
        one. Without this, the literal above would pass a reading that
        secretly queried per row."""
        make_queue_job(kind="test.only", not_before=timezone.now() + timedelta(minutes=2))

        with django_assert_num_queries(<the same literal>):
            client.get(reverse("jobs-queue"))

    def test_the_page_never_500s_on_a_row_with_a_hold_off(self, client, admin_signed_in):
        make_queue_job(kind="test.unregistered", not_before=timezone.now() + timedelta(minutes=2))

        assert client.get(reverse("jobs-queue")).status_code == 200
```

- [ ] **Step 2: Implement**

In `_present_row`:

```python
    # THE HOLD-OFF READING (spec §3.6, R3-2 ruling). `not_before` is only
    # interesting while it is still in the FUTURE: a past value is just a
    # hold-off that has expired, and rendering it would read as a delay
    # that is still in force. Display only -- this row was already loaded
    # by `queue_snapshot`, so the reading costs no query and needs no
    # script.
    hold_off_until = (
        row.not_before if row.not_before and row.not_before > timezone.now() else None
    )
```

added to the returned dict as `"hold_off_until": hold_off_until`, with a paragraph in the docstring saying why a past value renders nothing.

In `jobs/queue.html`, inside the waiting row's `job-meta` div:

```html
              {% if row.hold_off_until %}
              · waiting for engine memory — retries at {{ row.hold_off_until|time:"H:i" }}
              {% endif %}
```

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/test_views.py foundation/ops/tests/test_shared_poller.py
```

- [ ] **Step 4: Commit**

```bash
git add models/queue/views.py models/queue/templates/jobs/queue.html models/queue/tests/test_views.py
git commit -m "feat(queue): a held-off job says so on the Queue page

A job the queue is deliberately declining to consider for the length of a
hold-off surfaced only as a log line, which fails this track's own
observability thesis: a delay the queue imposes on purpose is never left
for an operator to infer.

Display only -- server-rendered off the row the page already loads,
revealed by the existing refresh, no new query and no script. A hold-off
already in the past renders nothing, because an expired one is not a delay
still in force."
```

---

## Task 14: Model-affinity batching, with an aging bound

**Spec:** §3.6 in full, §9.12, §9.14, owner decision 4 (accepted: `MAX_PASSOVERS = 3`, affinity on by default).

**Files:**
- Modify: `models/queue/scheduler.py` (+ `MAX_PASSOVERS`, `SchedCandidate.passed_over`, `affinity_order`; `plan_admissions` takes two new keywords and uses the helper in place of its inline sort)
- Modify: `models/queue/claim.py` (build candidates with `passed_over`; pass the worker's snapshot; derive and write the increment)
- Modify: `models/queue/worker.py` (thread `resident_keys=self._resident_keys` into `claim_and_admit`)
- Modify: `models/queue/views.py` + `templates/jobs/queue.html` (the "passed over" reading)
- Modify: `models/queue/tests/test_scheduler.py`, `test_claim.py`, `test_views.py`

**Interfaces:**
- Produces:
  - `MAX_PASSOVERS: int = 3`
  - `SchedCandidate.passed_over: int = 0` (new trailing field, defaulted — every existing construction keeps working)
  - `affinity_order(candidates: Sequence[SchedCandidate], *, resident_keys: frozenset, max_passovers: int) -> list[SchedCandidate]`
  - `plan_admissions(candidates, running, *, budget_bytes, max_concurrent, resident_keys=frozenset(), max_passovers=MAX_PASSOVERS) -> list[int]`
  - `claim_and_admit(..., resident_keys: frozenset = frozenset())`
  - `_present_row`'s `passed_over: int`
- Consumes: `Worker._resident_keys` (Task 12, commit 1).

**The ordering key is `(priority, not pinned, not affine, job_id)`.** Priority is never crossed. The walk still stops at the first candidate that cannot be admitted, so the no-backfill deadlock proof is untouched. A candidate is **pinned** once `passed_over` reaches `max_passovers`, after which it sorts strictly by id within its priority and can never be reordered behind a peer again.

**Where "resident" comes from, and the two rejected alternatives:** the **worker's most recent residency snapshot**, cached on the worker and handed in as a plain frozen set. *Not* the pure scheduler's own resident fold — that is derived from running jobs and would make affinity a no-op in sequential mode, exactly the posture where batching matters most. *Not* a live `list_installed` — that would put engine HTTP inside the advisory-lock transaction and stall every other admitter.

**How stale it really is:** snapshots are only taken on ticks that *admit*, so in sequential mode the cached set can be **minutes** old; it is the pre-eviction belief with the pass's own unloads subtracted; and a fresh worker has none at all, falling back to the running jobs' declared keys. That is exactly why it drives an ordering *preference* and nothing else — a wrong guess costs one suboptimal ordering decision, never a wrong admission or a wrong eviction.

- [ ] **Step 1: Write the failing tests**

`models/queue/tests/test_scheduler.py` — this module already has module-level `model(key, footprint)` and `candidate(job_id, *, priority, exclusive, models)` helpers (no leading underscore). Add one wrapper beside them rather than a second vocabulary:

```python
def _candidate(
    job_id: int, *, priority: int = 100, keys: list[tuple[str, str, str]] | None = None,
    passed_over: int = 0,
) -> SchedCandidate:
    """`candidate()` plus the two things the affinity tests need: REAL
    three-segment keys (affinity compares them against the worker's
    believed-resident set, so the existing `model()` helper's
    `(key, key, key)` shorthand would not read naturally here) and
    `passed_over`, the new durable field."""
    return SchedCandidate(
        job_id=job_id, priority=priority, exclusive=False,
        models=tuple(SchedModel(key=key, footprint_bytes=1) for key in (keys or [])),
        passed_over=passed_over,
    )
```

```python
class TestAffinityOrder:
    def test_an_affine_candidate_sorts_ahead_within_a_priority(self):
        warm = _candidate(job_id=9, priority=100, keys=[("e", "http://x", "warm")])
        cold = _candidate(job_id=2, priority=100, keys=[("e", "http://x", "cold")])

        ordered = affinity_order(
            [cold, warm], resident_keys=frozenset({("e", "http://x", "warm")}),
            max_passovers=3,
        )

        assert [c.job_id for c in ordered] == [9, 2]

    def test_priority_is_never_crossed(self):
        warm_low = _candidate(job_id=9, priority=200, keys=[("e", "http://x", "warm")])
        cold_high = _candidate(job_id=2, priority=100, keys=[("e", "http://x", "cold")])

        ordered = affinity_order(
            [warm_low, cold_high], resident_keys=frozenset({("e", "http://x", "warm")}),
            max_passovers=3,
        )

        assert [c.job_id for c in ordered] == [2, 9]

    def test_a_pinned_candidate_sorts_by_id_and_is_never_reordered_again(self):
        pinned = _candidate(job_id=2, priority=100, keys=[("e", "http://x", "cold")],
                            passed_over=3)
        warm = _candidate(job_id=9, priority=100, keys=[("e", "http://x", "warm")])

        ordered = affinity_order(
            [warm, pinned], resident_keys=frozenset({("e", "http://x", "warm")}),
            max_passovers=3,
        )

        assert [c.job_id for c in ordered] == [2, 9]

    def test_with_no_snapshot_it_is_plain_priority_id_order(self):
        ordered = affinity_order(
            [_candidate(job_id=9, priority=100), _candidate(job_id=2, priority=100)],
            resident_keys=frozenset(), max_passovers=3,
        )

        assert [c.job_id for c in ordered] == [2, 9]

    def test_a_candidate_with_no_models_is_never_affine(self):
        """"All of its keys are resident" must not be vacuously true for a
        job that declares none -- such a job is already effectively
        exclusive (rule 2c) and must not jump a queue for free."""
        empty = _candidate(job_id=9, priority=100, keys=[])
        cold = _candidate(job_id=2, priority=100, keys=[("e", "http://x", "cold")])

        ordered = affinity_order([empty, cold], resident_keys=frozenset({("e", "http://x", "warm")}),
                                 max_passovers=3)

        assert [c.job_id for c in ordered] == [2, 9]

    def test_partially_resident_is_not_affine(self):
        """Affinity means "would load NOTHING", not "would load less"."""
        ...

    def test_plan_admissions_walks_the_same_order_this_helper_returns(self):
        """One function, so the order the scheduler walks and the order the
        claim code reasons about can never drift."""
        ...
```

Then re-run the existing scheduler invariants under the new ordering — `TestStrictOrderNoBackfill`, `TestSequentialMode`, `TestOversizeExclusive`, `TestMaxConcurrentCap` — adding one affinity-flavoured case to each rather than replacing them.

`models/queue/tests/test_claim.py`:

```python
class TestPassOverAccounting:
    @pytest.mark.django_db
    def test_a_job_a_later_peer_was_admitted_ahead_of_is_counted(self):
        ...
        passed.refresh_from_db()
        assert passed.passed_over == 1

    @pytest.mark.django_db
    def test_a_job_nothing_was_admitted_ahead_of_is_not_counted(self):
        ...
        assert untouched.passed_over == 0

    @pytest.mark.django_db
    def test_the_increment_is_one_bulk_update(self, django_assert_num_queries):
        ...

    @pytest.mark.django_db
    def test_a_pinned_job_is_admitted_ahead_of_an_affine_peer(self):
        """The aging bound, end to end: within one priority a job can be
        passed over at most MAX_PASSOVERS times before it is pinned and
        ordered by insertion."""
        ...
```

`models/queue/tests/test_views.py`:

```python
    def test_a_passed_over_job_says_so_on_the_waiting_row(self, client, admin_signed_in):
        make_queue_job(kind="test.k", passed_over=2)

        assert "passed over twice" in client.get(reverse("jobs-queue")).content.decode()
```

**The pass-over pin is AUTHORED here, not re-pinned.** Verified against the tree on 2026-09-21: `models/queue/tests/test_claim.py` contains no query-count assertion of any kind, so there is nothing to move — this task writes the first one, and it must be non-vacuous and literal:

```python
    @staticmethod
    def _warm_keys() -> frozenset:
        """The believed-resident set the affinity ordering reads -- whatever
        keys the fixture's warm candidates hold, so at least one candidate
        sorts ahead of a later-by-(priority, id) peer and there is
        something to count."""
        return frozenset({("ollama", "http://ollama.local:11434", "warm")})

    @pytest.mark.django_db
    def test_the_increment_is_one_bulk_update_however_many_jobs_were_passed_over(
            self, django_assert_num_queries):
        """ONE `UPDATE ... SET passed_over = passed_over + 1` inside the
        transaction already open -- never one per job. NON-VACUOUS: the
        same literal has to hold with one passed-over candidate and with
        four, or a per-row implementation would sail through.

        The number is a LITERAL, filled from the first red run. This is a
        NEW pin: `test_claim.py` had no query-count assertion before this
        task."""
        ...  # one passed-over candidate
        with django_assert_num_queries(<literal>):
            claim_and_admit("w", stale_after_seconds=120, resident_keys=_warm_keys())

        ...  # four passed-over candidates
        with django_assert_num_queries(<the same literal>):
            claim_and_admit("w", stale_after_seconds=120, resident_keys=_warm_keys())
```

- [ ] **Step 2: Implement**

`models/queue/scheduler.py`:

```python
# How many times model-affinity reordering may put a later-by-(priority,
# id) peer ahead of one candidate before that candidate is PINNED --
# ordered strictly by id within its priority from then on, and never
# reordered behind a peer again (owner decision 4). Supplied by the claim
# code as a keyword argument so table tests can vary it; this is the
# default the queue actually runs with.
MAX_PASSOVERS = 3
```

```python
    # How many times a later-by-(priority, id) peer has been admitted
    # ahead of this candidate (`InferenceJob.passed_over`). Durable, not
    # in-memory: a worker restart must not reset a job's age. Trailing and
    # defaulted, so every existing construction of this dataclass is
    # unchanged.
    passed_over: int = 0
```

```python
def affinity_order(
    candidates: Sequence[SchedCandidate], *, resident_keys: frozenset, max_passovers: int,
) -> list[SchedCandidate]:
    """Order `candidates` for admission: `(priority, not pinned, not
    affine, job_id)` (rule 10, spec §3.6).

    THE OWNER'S OWN REQUIREMENT: "we should bundle like requests for a
    given model even if they were submitted at different times... This
    should be a feature of the queue." Among candidates AT THE SAME
    PRIORITY NUMBER, one whose model keys are ALL already resident sorts
    ahead of one that would have to load something.

    PRIORITY IS NEVER CROSSED, and `plan_admissions` still stops at the
    first candidate it cannot admit, so the no-backfill deadlock proof is
    untouched: whichever candidate heads a round is still admitted alone
    the instant the machine is idle.

    THE AGING BOUND: a candidate whose `passed_over` has reached
    `max_passovers` is PINNED -- it sorts strictly by id within its
    priority and can never be reordered behind a peer again. Within one
    priority number a job can therefore be passed over at most that many
    times; across priority numbers nothing changed, so the ADR's existing,
    honestly-scoped starvation caveat is neither improved nor worsened.

    "AFFINE" MEANS "WOULD LOAD NOTHING", not "would load less": every one
    of the candidate's keys must be in `resident_keys`. A candidate
    declaring NO models is never affine -- the set-subset test would
    otherwise be vacuously true, and such a job is already effectively
    exclusive (rule 2c) rather than a free rider entitled to jump a queue.

    PURE. `resident_keys` is a plain frozen set the CALLER believes is
    resident; this module never asks an engine anything and never touches
    a row (see the module docstring).
    """
    def _key(candidate: SchedCandidate) -> tuple:
        pinned = candidate.passed_over >= max_passovers
        keys = {model.key for model in candidate.models}
        affine = bool(keys) and keys <= resident_keys
        return (candidate.priority, not pinned, not affine, candidate.job_id)

    return sorted(candidates, key=_key)
```

`plan_admissions` takes `resident_keys: frozenset = frozenset()` and `max_passovers: int = MAX_PASSOVERS` and replaces its inline `sorted(...)` with `affinity_order(...)` — the defaults keep every existing direct call (and every table test) behaving exactly as before, which is what makes this an addition rather than a rewrite.

`models/queue/claim.py`:

- `claim_and_admit(..., resident_keys: frozenset = frozenset())`, threaded into `plan_admissions`.
- `_sched_candidate` passes `passed_over=row.passed_over`.
- after the admissions are decided, derive and write the increment:

```python
        # PASS-OVER ACCOUNTING (spec §3.6). A candidate is "passed over"
        # when a peer that is LATER by strict `(priority, id)` was admitted
        # ahead of it. Derived by comparing the order `affinity_order`
        # produced against strict order -- using the SAME helper
        # `plan_admissions` walks, so the ordering and the accounting can
        # never disagree about what happened.
        #
        # ONE BULK UPDATE inside the transaction already open. The count is
        # what the aging bound reads, and it is durable precisely so a
        # worker restart cannot reset a job's age.
        admitted_set = set(admitted_ids)
        latest_admitted = max(
            ((rows_by_id[job_id].priority, job_id) for job_id in admitted_set), default=None,
        )
        if latest_admitted is not None:
            passed_over_ids = [
                row.pk for row in candidate_rows
                if row.pk not in admitted_set and (row.priority, row.pk) < latest_admitted
            ]
            if passed_over_ids:
                InferenceJob.objects.filter(pk__in=passed_over_ids).update(
                    passed_over=F("passed_over") + 1,
                )
```

`from django.db.models import F` joins `models/queue/claim.py`'s imports beside the `Q` Task 6 added — the bulk increment needs it and nothing else in that module does.

`models/queue/worker.py`: `self._resident_keys: frozenset = frozenset()` in `__init__` (documented as "the worker's last believed-resident set, minus what that pass unloaded — an ordering preference and nothing else"), threaded into the `claim_and_admit` call; when it is empty the scheduler simply sees no affinity and orders by `(priority, id)`, which is the fresh-worker fallback.

`models/queue/views.py`: `_present_row` gains `"passed_over": row.passed_over`, and the template renders a count in words for the two smallest values and a number above that:

```html
              {% if row.passed_over %}
              · passed over {% if row.passed_over == 1 %}once{% elif row.passed_over == 2 %}twice{% else %}{{ row.passed_over }} times{% endif %}
              {% endif %}
```

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/
```

Expected: green, with the newly authored pin above passing at its literal number. **Nothing is re-pinned by this task** — `test_claim.py` had no query-count assertion before it, and `TestTheSingleSettingsReadPerTick` counts `jobs_jobsettings` statements alone and is untouched by the bulk `UPDATE`.

- [ ] **Step 4: Commit**

```bash
git add models/queue/scheduler.py models/queue/claim.py models/queue/worker.py models/queue/views.py models/queue/templates/jobs/queue.html models/queue/tests/
git commit -m "feat(queue): like work for one model runs together

The owner's own requirement: bundle like requests for a given model even
when they were submitted at different times. Among candidates at the SAME
priority, one whose models are all already resident sorts ahead of one
that would have to load something.

affinity_order is a pure helper in the scheduler, used by plan_admissions
in place of its inline sort AND by the claim code to derive who was passed
over -- one function, so the order walked and the order reasoned about
cannot drift. Priority is never crossed and the walk still stops at the
first candidate it cannot admit, so the no-backfill deadlock proof is
untouched; a job pinned at MAX_PASSOVERS sorts by id for ever after.

Residency comes from the worker's last snapshot (minutes old in sequential
mode, pre-eviction, with that pass's unloads subtracted) -- never the
running-jobs fold, which is dead in sequential mode where batching matters
most, and never a live engine call inside the advisory lock.

Authors the first query-count pin in test_claim.py (there was none): the
pass-over increment is one bulk UPDATE at one passed-over candidate and at
four. Nothing is re-pinned -- the only shipped queue-side pin counts
jobs_jobsettings statements and is untouched."
```

---

## Task 15: The unset budget is loud, and the prefill names the process that measured it

**Spec:** §3.7, owner decision 1 (accepted), §8 proof 8.

**Files:**
- Modify: `models/queue/worker.py` (write the detected figure once at boot)
- Modify: `models/queue/views.py` (`_job_settings_context` carries the detected pair)
- Modify: `models/queue/templates/jobs/queue.html` (the budget block becomes a callout)
- Modify: `models/queue/templates/jobs/settings.html` (the honest prefill note)
- Modify: `models/queue/tests/test_views.py`, `test_worker.py`

**Interfaces:**
- Produces: `Worker._record_detected_memory() -> None`; `detected_memory_human` / `detected_memory_at` in the settings context.
- Consumes: `JobSettings.detected_memory_bytes`/`_at` (Task 10).

**The prefill is measured where it matters.** The console renders in the **web** service; the budget governs the **worker** service — separate containers, so memory detected in the web process describes the wrong machine. The worker writes the figure once at boot and the settings page renders it **labelled with what it is**: "detected by the worker process on *date*". **Nothing is applied on the operator's behalf** — the container sees the VM's allocation rather than the host's, and a silently derived budget would be authoritative and wrong.

**No new dependency.** `psutil` is not in `requirements.txt` and this track does not add it: the figure comes from `os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")`, guarded, with `None` written when the platform does not answer. A number we cannot get is honestly absent, exactly like the budget itself.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
class TestTheDetectedMemoryFact:
    def test_the_worker_writes_it_once_at_boot(self, worker):
        worker._record_detected_memory()

        row = JobSettings.get_solo()
        assert row.detected_memory_bytes > 0
        assert row.detected_memory_at is not None

    def test_a_platform_that_does_not_answer_writes_nothing(self, worker, monkeypatch):
        # Patched on the WORKER MODULE's own name, not on the real `os`
        # module: `monkeypatch.setattr(worker_module.os, ...)` reaches
        # through to the stdlib object every other test in the process
        # shares. `_record_detected_memory` therefore calls a
        # module-level `_total_memory_bytes()` helper, and this patches
        # THAT.
        monkeypatch.setattr(worker_module, "_total_memory_bytes",
                            lambda: (_ for _ in ()).throw(ValueError()))

        worker._record_detected_memory()

        assert JobSettings.get_solo().detected_memory_bytes is None

    def test_it_never_sets_the_budget(self, worker):
        """The container sees the VM's allocation, not the host's: a
        silently derived budget would be authoritative and wrong."""
        worker._record_detected_memory()

        assert JobSettings.get_solo().memory_budget_bytes is None


@pytest.mark.django_db
class TestTheUnsetBudgetIsLoud:
    def test_the_queue_page_says_what_an_unset_budget_costs(self, client, admin_signed_in):
        body = client.get(reverse("jobs-queue")).content.decode()

        assert "Jobs run one at a time" in body
        assert "nothing is offloaded for budget reasons" in body
        assert reverse("jobs-settings") in body

    def test_a_set_budget_renders_no_callout(self, client, admin_signed_in):
        JobSettings.objects.filter(pk=1).update(memory_budget_bytes=32 * 1024**3)

        assert "nothing is offloaded for budget reasons" not in client.get(
            reverse("jobs-queue")).content.decode()

    def test_the_prefill_names_the_process_that_measured_it(self, client, admin_signed_in):
        JobSettings.objects.filter(pk=1).update(
            detected_memory_bytes=64 * 1024**3,
            detected_memory_at=datetime(2026, 3, 4, tzinfo=dt_timezone.utc),
        )

        body = client.get(reverse("jobs-settings")).content.decode()

        assert "detected by the worker process on March 4, 2026" in body
        assert "64" in body

    def test_with_nothing_detected_the_settings_page_still_renders(self, client, admin_signed_in):
        assert client.get(reverse("jobs-settings")).status_code == 200
```

- [ ] **Step 2: Implement**

In `models/queue/worker.py`:

```python
    def _record_detected_memory(self) -> None:
        """Write what THIS PROCESS's machine reports as total memory onto
        the settings row, once, at startup (spec §3.7).

        WHY THE WORKER AND NOT THE CONSOLE: the console renders in the web
        service and the budget governs the worker service -- separate
        containers -- so memory detected in the web process describes the
        wrong machine, in precisely the way the operator would be misled
        by.

        NOTHING IS APPLIED ON THE OPERATOR'S BEHALF. This is a PREFILL and
        a label, never a budget: the container sees the VM's allocation
        rather than the host's, and a silently derived budget would be
        authoritative and wrong. The settings page renders the number with
        the process and the date attached so an operator can judge it.

        No new dependency: `os.sysconf` answers on both platforms this
        runs on. A platform that does not answer writes NOTHING -- an
        honestly absent number, like the budget itself -- rather than a
        guess.
        """
        try:
            total = _total_memory_bytes()
        except (AttributeError, ValueError, OSError):
            return
        if total <= 0:
            return
        try:
            JobSettings.objects.filter(pk=1).update(
                detected_memory_bytes=total, detected_memory_at=timezone.now(),
            )
        except (ProgrammingError, OperationalError):
            return
```

with the one-line helper beside it, so a test can replace this process's answer without reaching into the shared stdlib `os` module:

```python
def _total_memory_bytes() -> int:
    """What THIS machine reports as total physical memory. A module-level
    function, not an inline `os.sysconf` call, purely so a test can patch
    the worker's own name instead of the stdlib object every other test in
    the process shares."""
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
```

called from `run_forever`, right after `_start_heartbeat_thread()` (process-level, like the pool and the thread — the deploy note in spec §7 already says a worker restart is required).

In `models/queue/views.py`, `_job_settings_context` gains `detected_memory_human` (via `_human_size`) and `detected_memory_at`, `None`-safe on the degraded box.

In `jobs/queue.html`, the unset branch of the budget block becomes a real callout in the page's existing notice style:

```html
      <p class="settings-current">
        <strong>Memory budget — not set.</strong> Jobs run one at a time, and nothing is
        offloaded for budget reasons. Set a budget on
        <a href="{% url 'jobs-settings' %}">Job execution</a> to let jobs whose footprints
        fit run together.
      </p>
```

**`settings-current`, not `notice`** — verified against `models/queue/templates/jobs/queue.html` on 2026-09-21: that is the class this block's own copy already uses, and the template has no `notice` class at all. Inventing one would need a rule in a stylesheet this column does not own, and `foundation/ops/tests/test_css_ownership.py` is a gate. Confirm with `grep -n 'class="' models/queue/templates/jobs/queue.html` before writing, and if the surrounding markup has moved, match what is there rather than what is printed here.

In `jobs/settings.html`, under the budget input:

```html
    {% if detected_memory_human %}
    <p class="caveat">This box reports {{ detected_memory_human }} of memory in total — detected by the worker process on {{ detected_memory_at|date:"N j, Y" }}. It is not applied for you: a container sees its own allocation rather than the host's, so the number that belongs here is yours to decide.</p>
    {% endif %}
```

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/ foundation/ops/tests/test_css_ownership.py
```

- [ ] **Step 4: Commit**

```bash
git add models/queue/worker.py models/queue/views.py models/queue/templates/jobs/ models/queue/tests/
git commit -m "feat(queue): an unset memory budget says what it costs

With the budget gate moved, an unset budget no longer switches the
apparatus off -- but the capped pass still has no number to work against
and the operator had no way to know it. The Queue page's budget block
becomes a real callout naming both consequences and the control that
changes them.

The prefill is measured where it matters: the worker writes the detected
figure once at boot (the console renders in the web service; the budget
governs the worker service), and the settings page labels it with the
process and the date. Nothing is applied on the operator's behalf -- a
container sees its own allocation, not the host's. No new dependency:
os.sysconf, guarded, writing nothing where the platform does not answer."
```

---

## Task 16: Kind-owned wait ceilings, and the rule about the exclusive slot

**Spec:** §3.5(a) and (b), §9.13, owner decision 5 (accepted: one editable row per registered kind).

**Files:**
- Modify: `models/contracts/jobkinds.py` (`JobKind.default_wait_seconds`, `JobContext.wait_seconds`, and the seam rule on `JobKind.handler`'s docstring)
- Modify: `models/queue/worker.py` (`_build_job_context` resolves the ceiling)
- Modify: `models/queue/views.py` (a **fourth** settings form, `"waits"`)
- Modify: `models/queue/templates/jobs/settings.html` (one row per registered kind)
- Modify: `models/queue/tests/test_worker.py`, `test_views.py`

**Interfaces:**
- Produces:
  - `JobKind.default_wait_seconds: int | None = None`
  - `JobContext.wait_seconds: float | None = None`
  - `queue_settings_update`'s `form == "waits"` branch
- Consumes: `JobSettings.kind_wait_seconds` (Task 10).

**This costs no additional query.** `_build_job_context` already fetches the settings row for `response_timeout_seconds`, and the map rides on that same row — a second `get_solo()` would be a query-count regression and is explicitly **not** what this does.

**The rule the queue publishes but can only partly enforce (§3.5b):** a job kind whose wait ceiling expires must not write a terminal outcome while its engine still reports the work running. It must either keep holding (continuing to report progress, which keeps the row alive under the heartbeat), or cancel the engine-side work and confirm the engine is terminal, and only then return. The queue enforces the other side of it from §3.3(d): the barrier catches a still-working engine before the next *exclusive* job launches, because the barrier-polling adapter withholds `True` while a prompt is still running. The exclusive→non-exclusive case and the cross-engine case stay unenforceable (§11).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.django_db
class TestTheWaitCeiling:
    def test_the_operators_value_wins_for_that_kind(self, worker):
        JobSettings.objects.filter(pk=1).update(kind_wait_seconds={"test.echo": 42})

        ctx = worker._build_job_context({"id": 1, "kind": "test.echo", "claim_token": uuid.uuid4()})

        assert ctx.wait_seconds == 42

    def test_a_kinds_declared_default_applies_with_no_operator_value(self, worker, reset_registry):
        register_job_kind(JobKind(..., key="test.slow", default_wait_seconds=900))

        ctx = worker._build_job_context({"id": 1, "kind": "test.slow", "claim_token": uuid.uuid4()})

        assert ctx.wait_seconds == 900

    def test_a_kind_declaring_nothing_gets_none_not_a_guess(self, worker, reset_registry):
        ctx = worker._build_job_context({"id": 1, "kind": "test.echo", "claim_token": uuid.uuid4()})

        assert ctx.wait_seconds is None

    def test_an_unregistered_kind_does_not_raise(self, worker):
        ctx = worker._build_job_context({"id": 1, "kind": "test.gone", "claim_token": uuid.uuid4()})

        assert ctx.wait_seconds is None

    def test_resolving_it_costs_no_second_settings_read(self, worker, django_assert_num_queries):
        """It rides on the row `_build_job_context` already fetches for
        `response_timeout_seconds` -- a SECOND `get_solo()` would be a
        query-count regression and is explicitly not how this is read.

        THE ROW IS CREATED FIRST, deliberately: `get_solo()` is a
        `get_or_create`, so on a database where the singleton does not yet
        exist it is a SELECT plus savepoint/INSERT/RELEASE, and a pin
        written without this line would pass or fail on fixture ordering
        rather than on the code under test."""
        JobSettings.get_solo()

        with django_assert_num_queries(1):
            worker._build_job_context({"id": 1, "kind": "test.echo", "claim_token": uuid.uuid4()})


@pytest.mark.django_db
class TestTheWaitCeilingForm:
    def test_one_row_per_registered_kind_renders_from_the_registry(
            self, client, admin_signed_in, reset_registry):
        register_job_kind(JobKind(..., key="test.echo", label="Echo"))

        body = client.get(reverse("jobs-settings")).content.decode()

        assert "Echo" in body
        assert 'name="wait_test.echo"' in body

    def test_saving_one_leaves_the_others_untouched(self, client, admin_signed_in):
        ...

    def test_a_blank_value_clears_that_kinds_ceiling(self, client, admin_signed_in):
        ...

    def test_a_non_numeric_value_saves_nothing_and_says_so(self, client, admin_signed_in):
        ...
        assert JobSettings.get_solo().kind_wait_seconds == {}

    def test_an_unknown_kind_key_is_ignored_rather_than_stored(self, client, admin_signed_in):
        """The form renders from the registry, so a posted key that names
        no registered kind is tampering, not data."""
        ...
```

- [ ] **Step 2: Implement**

`models/contracts/jobkinds.py`:

```python
    default_wait_seconds: int | None = None
```

```
        default_wait_seconds: How long this kind's handler may wait on its
            ENGINE before it gives up, or `None` for "this kind does not
            wait". CODE-DECLARED like `stale_after_seconds`; the OPERATOR
            may override it per kind on the job-execution settings page
            (`models.queue.models.JobSettings.kind_wait_seconds`), and the
            worker stamps whichever wins onto `JobContext.wait_seconds`.

            THE RULE THAT COMES WITH IT, and it is not optional: a kind
            whose wait ceiling expires MUST NOT write a terminal outcome
            while its engine still reports the work running. The exclusive
            slot is released the instant the job row goes terminal, so a
            kind that gives up waiting and reports success hands the
            machine to the next admission while its own engine is still
            sampling. Such a handler must either keep holding (continuing
            to report progress, which keeps the row alive under the
            worker's heartbeat) or cancel the engine-side work and confirm
            the engine is terminal, and only then return.
```

`JobContext` gains:

```python
    # THE KIND'S WAIT CEILING (spec §3.5a), stamped by
    # `models.queue.worker.Worker._build_job_context` off the SAME
    # settings row `response_timeout_seconds` above rides on -- never a
    # second read. `None` means this kind declares no ceiling and the
    # operator set none: a handler that does not wait on an engine simply
    # never looks at it, exactly as it may ignore `models`.
    wait_seconds: float | None = None
```

`Worker._build_job_context` resolves it off the row it already has (restructure so `get_solo()` is called once and both values come off it), tolerating an unregistered kind through `get_job_kind`'s `ValueError`.

`models/queue/views.py`: `queue_settings_update`'s `form` set grows `"waits"`; `_update_kind_waits(request, settings_row)` validates **every** posted `wait_<kind key>` field against the **registered** kinds before saving anything (never-500 grammar: a bad field leaves the row completely untouched), treats blank as "clear this kind's entry", and ignores a key naming no registered kind. `_job_settings_context` adds `kind_wait_rows` — one `{key, label, value}` per `all_job_kinds()`, so a newly registered kind appears with no template edit.

`jobs/settings.html` gains the **fourth** form, anchored like its siblings (`id="kind-waits"`). `queue_settings_update` already dispatches `budget`, `retention` and `timeout` (verified against `models/queue/views.py`, 2026-09-21), so the recognised set becomes four, and the "unrecognised form flashes and redirects" branch keeps its current copy.

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q models/queue/tests/ models/contracts/tests/ agents/runtime/tests/
```

- [ ] **Step 4: Commit**

```bash
git add models/contracts/jobkinds.py models/queue/worker.py models/queue/views.py models/queue/templates/jobs/settings.html models/queue/tests/
git commit -m "feat(queue): a wait ceiling belongs to the kind and the operator

A fixed 600s wait inside a job kind fired while a first-load generation was
still genuinely running, and the work finished about fifteen minutes later.
A ceiling hard-coded in a handler cannot know what hardware it is on.

JobKind declares a default; the operator overrides it per kind on the
job-execution page, one row rendered from the registry so a new kind needs
no template edit; the worker stamps whichever wins onto JobContext. It
costs no extra query -- the map rides on the settings row
_build_job_context already fetches for the response timeout.

The seam also publishes the rule that goes with it: a kind whose ceiling
expires must not write a terminal outcome while its engine still reports
the work running, because the exclusive slot is released the instant the
row goes terminal."
```

---

## Task 17: A turn whose job row vanished recovers

**Spec:** §3.8, owner decision 7 (accepted: idempotent auto-repair), §9.15.

**Files:**
- Create: `agents/reconcile.py` (+ `STRANDED_TURN_GRACE_SECONDS`, `reconcile_stranded_turn`, `reconcile_stranded_turns`, `count_stranded_turns`) — *2026-09-21 correction, Task 19: this line read "Modify: `agents/runtime/jobs.py`". The module had to move to the column root, because `foundation/ops/tests/test_column_boundaries.py::test_no_runtime_module_blocks_on_a_queue_job` forbids a `get_job` call anywhere under `agents/runtime/`, and this module's whole condition is that call. `agents/runtime/jobs.py` was not edited.*
- Create: `agents/management/commands/reconcile_turns.py`
- Modify: `agents/chat/views/turns.py` (`_queued_body`/`_running_body` reconcile a missing job row)
- Modify: `agents/runtime/tests/test_jobs.py`, `agents/chat/tests/test_turn_status.py`

**Interfaces:**
- Produces:
  - `reconcile_stranded_turn(turn) -> bool` — `True` when this call closed it.
  - `reconcile_stranded_turns(grace_seconds: int = STRANDED_TURN_GRACE_SECONDS) -> int`
  - `manage.py reconcile_turns [--dry-run]`
- Consumes: `models.contracts.queue.get_job` (already imported by the chat view), `agents.runtime.audit.close_open_invocations`, `Turn`.

**Owned by the agent column, necessarily:** the queue cannot know which turn points at which job, and may not import the agent layer. Reconciliation lives beside `on_turn_terminal` and reuses its exact write shape.

**The condition is deliberately narrow:** an **assistant** turn still `queued`/`running`, whose `queue_job_id` is null **or** names a job row that **no longer exists**, and whose row has been in that state longer than the grace period — comfortably longer than the enqueue-then-commit window, so a turn mid-creation is never touched.

**The invocation-closing half is a documented no-op on the null branch.** `close_open_invocations` returns zero for a falsy job id by design: there is nothing stamped to close. Say so rather than leaving a reader to wonder.

**No periodic background sweeper.** The condition is rare, the read surface already visits exactly the row that matters, and an always-on sweeper is machinery this evidence does not justify.

- [ ] **Step 1: Write the failing tests**

`agents/runtime/tests/test_jobs.py` — that module has `_turn_for(agent)` and `_payload(turn, **overrides)` already; these two build the specific rows this condition is about:

```python
def _assistant_turn(*, state, queue_job_id, age_seconds: int):
    """One ASSISTANT turn in `state`, pointing at `queue_job_id` (which
    may name no row at all), whose row has been in that state for
    `age_seconds` -- the three facts the stranded condition reads."""
    turn = _turn_for(make_agent())
    Turn.objects.filter(pk=turn.pk).update(
        role=Turn.Role.ASSISTANT, state=state, queue_job_id=queue_job_id,
        created_at=timezone.now() - timedelta(seconds=age_seconds),
    )
    turn.refresh_from_db()
    return turn


def _stranded_assistant_turn():
    """The shape the poll path reconciles: past the grace, pointing at a
    job row that does not exist."""
    return _assistant_turn(
        state=Turn.State.RUNNING, queue_job_id=4242,
        age_seconds=STRANDED_TURN_GRACE_SECONDS + 10,
    )
```

> The condition reads "how long the row has been in that state". If `Turn` carries no per-state timestamp, use `created_at` as above and say so in `reconcile_stranded_turn`'s docstring — an assistant turn's creation IS the start of its queued/running life. Check the model before writing either.

`agents/chat/tests/test_turn_status.py` imports `_stranded_assistant_turn` by name from that module, the way this repository's test files already import each other's helpers.

```python
class TestReconcileStrandedTurn:
    """A chat turn whose job row was rolled back by a database crash sits
    at "working" for ever: the on_terminal hook that closes a placeholder
    is driven by the job row's own terminal write, and there is no row
    left to write. Recovery today is "delete the conversation and
    resend"."""

    @pytest.mark.django_db
    def test_a_turn_whose_job_row_is_gone_is_closed_honestly(self):
        turn = _assistant_turn(state=Turn.State.RUNNING, queue_job_id=4242,
                               age_seconds=STRANDED_TURN_GRACE_SECONDS + 10)

        assert reconcile_stranded_turn(turn) is True

        turn.refresh_from_db()
        assert turn.state == Turn.State.FAILED
        assert turn.error == "The queue lost this turn before it ran; nothing was retried."

    @pytest.mark.django_db
    def test_a_turn_whose_job_row_exists_is_left_alone(self):
        job = make_queue_job(kind="agent.turn")
        turn = _assistant_turn(state=Turn.State.RUNNING, queue_job_id=job.pk,
                               age_seconds=STRANDED_TURN_GRACE_SECONDS + 10)

        assert reconcile_stranded_turn(turn) is False

    @pytest.mark.django_db
    def test_a_turn_inside_the_grace_is_left_alone(self):
        """The enqueue-then-commit window: a turn mid-creation genuinely
        has no job row yet."""
        turn = _assistant_turn(state=Turn.State.QUEUED, queue_job_id=None, age_seconds=1)

        assert reconcile_stranded_turn(turn) is False

    @pytest.mark.django_db
    def test_a_terminal_turn_is_never_rewritten(self):
        turn = _assistant_turn(state=Turn.State.DONE, queue_job_id=4242,
                               age_seconds=STRANDED_TURN_GRACE_SECONDS + 10)

        assert reconcile_stranded_turn(turn) is False

    @pytest.mark.django_db
    def test_a_user_turn_is_never_touched(self):
        ...

    @pytest.mark.django_db
    def test_it_closes_the_turns_open_invocation_rows(self):
        ...
        assert invocation.finished_at is not None

    @pytest.mark.django_db
    def test_the_null_job_id_half_closes_nothing_and_says_so(self):
        """Documented no-op: `close_open_invocations` returns zero for a
        falsy job id, because nothing was ever stamped with one."""
        turn = _assistant_turn(state=Turn.State.QUEUED, queue_job_id=None,
                               age_seconds=STRANDED_TURN_GRACE_SECONDS + 10)

        assert reconcile_stranded_turn(turn) is True

    @pytest.mark.django_db
    def test_calling_it_twice_writes_once(self):
        """Idempotent by construction: one conditional UPDATE filtered on
        the two non-terminal states."""
        ...
        assert reconcile_stranded_turn(turn) is False

    @pytest.mark.django_db
    def test_the_sweep_counts_what_it_closed(self):
        ...
        assert reconcile_stranded_turns() == 2


class TestTheReconcileTurnsCommand:
    @pytest.mark.django_db
    def test_dry_run_reports_without_writing(self):
        out = StringIO()
        call_command("reconcile_turns", "--dry-run", stdout=out)

        turn.refresh_from_db()
        assert turn.state == Turn.State.RUNNING
        assert "1" in out.getvalue()

    @pytest.mark.django_db
    def test_it_closes_them_for_real_without_the_flag(self):
        ...
```

`agents/chat/tests/test_turn_status.py`:

```python
    def test_a_polled_turn_whose_job_row_is_gone_comes_back_failed(self, client):
        """Turns a permanent "Queued — waiting…" into an honest failed
        card within one poll tick -- with JS on or off, since a plain
        reload takes the same path through the rendered card."""
        turn = _stranded_assistant_turn()

        body = client.get(reverse("chat-turn-status", args=[turn.pk])).json()

        assert body["state"] == "failed"
        assert "lost this turn" in body["error"]

    def test_a_turn_inside_the_grace_still_reports_queued(self, client):
        ...
        assert body["state"] == "queued"
```

- [ ] **Step 2: Implement**

`agents/runtime/jobs.py`:

```python
# How long an assistant turn may sit queued/running with no job row
# behind it before it is treated as STRANDED. Comfortably longer than the
# enqueue-then-commit window, so a turn whose job row simply has not been
# written yet is never touched -- that window is milliseconds, and this is
# the difference between repairing a broken row and racing a healthy one.
STRANDED_TURN_GRACE_SECONDS = 60


def reconcile_stranded_turn(turn) -> bool:
    """Close `turn` honestly if -- and only if -- its job row is gone, and
    report whether THIS call is what closed it.

    THE CONDITION, deliberately narrow (spec §3.8): an ASSISTANT turn,
    still `queued` or `running`, whose `queue_job_id` is null or names a
    job row that NO LONGER EXISTS, and whose row has been in that state
    longer than `STRANDED_TURN_GRACE_SECONDS`.

    WHY IT EXISTS: the `on_terminal` hook that closes a placeholder turn
    is driven by the JOB ROW's own terminal write. A database crash that
    rolls the job row back leaves nothing to drive it, and the turn sits
    at "working" for ever -- recovery today is "delete the conversation
    and resend".

    OWNED BY THIS COLUMN, necessarily: the queue cannot know which turn
    points at which job, and may not import the agent layer at all.

    ONE CONDITIONAL UPDATE, filtered on the two non-terminal states --
    the exact shape `on_turn_terminal` uses, and for the same reason: a
    still-alive worker can be writing DONE in the window between this
    read and this write, and the `state__in` guard makes clobbering it
    impossible. Idempotent by construction, which is what makes it safe
    to call from a polled GET (owner decision 7).

    It also closes the turn's open invocation rows -- which closes rows
    where a job id was stamped, and has nothing to close on the
    null-`queue_job_id` half of the condition:
    `close_open_invocations` returns zero for a falsy job id BY DESIGN,
    since nothing was ever stamped with one.
    """
```

`reconcile_stranded_turns(grace_seconds=STRANDED_TURN_GRACE_SECONDS) -> int` walks the non-terminal assistant turns older than the grace and returns how many it closed — one `get_job` per candidate, which is bounded because the condition is rare and the command is an operator action, not a loop.

`agents/management/commands/reconcile_turns.py` — thin, like `run_jobs`: `--dry-run` counts without writing and prints what it would close; without it, calls `reconcile_stranded_turns()` and prints the count.

`agents/chat/views/turns.py` — in `_queued_body` and `_running_body`, where `get_job(turn.queue_job_id)` already returns `None` for a missing row:

```python
    job = get_job(turn.queue_job_id)
    if job is None and reconcile_stranded_turn(turn):
        # The strand is visible HERE, at the one surface that was going to
        # answer "Queued — waiting…" for ever. Re-read and answer with the
        # honest state instead, within this same poll tick -- a plain
        # reload takes the same path through the rendered card, so this is
        # not a JS-only repair.
        turn.refresh_from_db()
        return _BODY_BUILDERS[turn.state](turn, request)
```

Watch the recursion: `_failed_body` never reconciles, so this can recurse at most once.

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q agents/runtime/tests/ agents/chat/tests/
```

- [ ] **Step 4: Commit**

```bash
git add agents/runtime/jobs.py agents/management/commands/reconcile_turns.py agents/chat/views/turns.py agents/runtime/tests/test_jobs.py agents/chat/tests/test_turn_status.py
git commit -m "fix(agents): a turn whose job row vanished stops saying 'working'

The on_terminal hook that closes a placeholder turn is driven by the job
row's own terminal write. A database crash that rolls that row back leaves
nothing to drive it, and the turn sits at 'working' for ever -- recovery
was 'delete the conversation and resend'.

reconcile_stranded_turn is one conditional UPDATE filtered on the two
non-terminal states, idempotent by construction, on a deliberately narrow
condition (an assistant turn past the grace whose job row is gone). It is
called where the strand is VISIBLE -- the polled status of that one turn --
plus manage.py reconcile_turns for the operator. No background sweeper:
the read surface already visits exactly the row that matters."
```

---

## Task 18: A poll loop that cannot tick silently for ever

**Spec:** §3.9 (both changes, with change 1 named load-bearing), and §9's author decision 16. *(2026-09-21, Task 19: this read "§9.16", which reads as a subsection the spec does not have — §9 is a numbered list of author decisions, not a sectioned chapter. The binding text for this task is §3.9; author decision 16 is the ruling that the loop is fixed in place under its existing exemption. Spelled out rather than renumbered, because the reference itself was correct.)*

**Files:**
- Modify: `agents/chat/views/turns.py` (`turn_status`'s retryable `503`)
- Modify: `agents/chat/templates/chat/conversation.html` (the loop's unrecognized-state branch and its 5xx handling)
- Modify: `agents/chat/tests/test_turn_status.py`, `agents/chat/tests/test_composer.py` (or whichever module already asserts at the rendered-script level — `grep -rn "MAX_TRANSPORT_RETRIES" agents/chat/tests/`)

**Interfaces:**
- Produces: a `503` body carrying `{"retryable": true, ...}`, distinct from the terminal configuration `503` (which keeps its `setup_url`).
- Consumes: nothing.

**Where the incident actually went.** A 5xx whose body is not JSON — the error page during a database outage — already rejects inside `response.json()` and lands in the loop's `.catch()`, where it is counted against the transport ceiling and bounded. The genuinely uncounted branch is a **parseable body carrying no recognized state**, which falls into the forward-compatible final `else` and re-ticks silently until the duration ceiling hours later. **Closing that is the load-bearing half.**

**Change 2 is explicitly best-effort** and cannot be a view-local `try` around the body builders: the view touches the database in principal resolution and turn visibility *before* any builder runs, and session middleware touches it before the view is entered at all. The guard therefore wraps the whole view body and names `OperationalError` explicitly — and a truly unavailable database can still produce a non-JSON 5xx that only change 1 handles.

**The gate, and the obligation this creates.** The hand-rolled loop is held in `_EXEMPT` in `foundation/ops/tests/test_shared_poller.py` under a dated, self-deleting exemption whose companion test asserts the loop still exists. Fixing in place is therefore correct today — and when the held follow-up repoints the page onto `foundation/templates/_poller.html`, **change 1 must be carried onto that shared loop** or it is silently lost. Task 19 writes that obligation into `agents/chat/README.md`. *(2026-09-21: this sentence read "Task 21", a task number this plan does not have — the doc task is 19, as its own heading and the spec-coverage table both say.)*

- [ ] **Step 1: Write the failing tests**

One helper for the rendered-script assertions — `grep -rn "conversation.html" agents/chat/tests/` first and reuse whatever that module already does to render the page; only add this if nothing equivalent exists:

```python
def _rendered_conversation_script(client, conversation) -> str:
    """The conversation page's rendered inline script, as text. The poll
    loop is asserted at the RENDERED level (not by reading the template
    file) because the numbers it depends on -- POLL_INTERVAL_MS,
    MAX_TRANSPORT_RETRIES, MAX_POLL_DURATION_MS -- are interpolated by
    the view."""
    body = client.get(conversation_url(conversation)).content.decode()
    return body[body.index("<script"):body.rindex("</script>")]
```

```python
class TestTheRetryable503:
    def test_a_momentarily_unavailable_database_answers_retryable(self, client, monkeypatch):
        monkeypatch.setattr(
            turns_module, "visible_turn",
            lambda *a, **k: (_ for _ in ()).throw(OperationalError("server closed")),
        )

        response = client.get(reverse("chat-turn-status", args=[1]))

        assert response.status_code == 503
        assert response.json()["retryable"] is True

    def test_the_configuration_503_stays_terminal_and_keeps_its_setup_link(self, client):
        """Two different 503s: one says "try again", the other says "go
        configure the queue". Conflating them would either spin on a
        misconfigured box or give up on a recovering one."""
        ...
        body = response.json()
        assert body.get("retryable") is not True
        assert "setup_url" in body


class TestTheLoopCountsWhatItCannotActOn:
    """The uncounted branch: a PARSEABLE body carrying no recognized state
    fell into the forward-compatible final `else` and re-ticked silently
    until the duration ceiling, hours later. A 5xx with an unparseable
    body was already counted -- it rejects in response.json() and lands in
    .catch()."""

    def test_an_unrecognized_state_increments_the_bounded_counter(self):
        script = _rendered_conversation_script()
        branch = script[script.index("} else {", script.index('data.state === "cancelled"')):]

        assert "transportFailures" in branch
        assert "setTimeout(tick, POLL_INTERVAL_MS)" in branch

    def test_a_parseable_5xx_is_counted_too(self):
        script = _rendered_conversation_script()

        assert "result.status >= 500" in script

    def test_exhausting_the_counter_shows_the_same_honest_note(self):
        script = _rendered_conversation_script()

        assert script.count("Lost contact with the server") >= 1

    def test_the_retryable_503_is_retried_rather_than_given_up_on(self):
        script = _rendered_conversation_script()

        assert "data.retryable" in script

    def test_the_transport_counter_is_still_only_reset_by_an_actionable_answer(self):
        """`transportFailures = 0` must not sit before the branch that
        counts -- resetting on every parseable response would make the
        ceiling unreachable."""
        ...
```

- [ ] **Step 2: Implement**

`turn_status` wraps its **whole body**:

```python
    try:
        principal = principal_for_request(request)
        turn = visible_turn(principal, turn_id)
        if turn is None:
            raise Http404(f"Turn {turn_id} does not exist.")
        body = _BODY_BUILDERS[turn.state](turn, request)
    except QueueUnavailable:
        # TERMINAL: the queue is not configured. Keeps its setup link, and
        # the loop stops -- retrying would spin for ever on a box that
        # needs an operator, not another request.
        return JsonResponse(
            {"error": QUEUE_UNAVAILABLE, "setup_url": reverse("inference-console")},
            status=503,
        )
    except OperationalError:
        # RETRYABLE: the database was momentarily unavailable (a restart,
        # a recovery). BEST-EFFORT, and the docstring says so: this guard
        # cannot live around the body builders alone -- the view touches
        # the database in principal resolution and turn visibility BEFORE
        # any builder runs, and session middleware touches it before the
        # view is entered at all, so a truly unavailable database can
        # still produce a non-JSON 5xx that only the loop's own counting
        # handles.
        return JsonResponse(
            {"error": "The queue is briefly unavailable; this will retry.", "retryable": True},
            status=503,
        )
    return JsonResponse(body)
```

(`Http404` must not be swallowed — raise it outside the `try`, or re-raise it before the `OperationalError` handler; write it whichever way keeps the 404 behaviour byte-identical, and pin that with the existing `TestTheTwoNon200s`.)

In `conversation.html`'s `poll()`, the `.then(...)` chain:

```js
          if (result.status === 503) {
            if (data.retryable) {
              // A database that is recovering, not one that is missing.
              // Counted like any other unusable answer, so this cannot
              // tick for ever either.
              transportFailures += 1;
              if (transportFailures > MAX_TRANSPORT_RETRIES) {
                showCardError(card, "Lost contact with the server — reload this page.", "");
                return;
              }
              setTimeout(tick, POLL_INTERVAL_MS);
              return;
            }
            showCardError(card, data.error || "The queue isn't ready yet.", data.setup_url);
            return;
          }
```

and the forward-compatible `else` stops being free:

```js
          } else {
            // A RESPONSE THIS PAGE CANNOT ACT ON IS A FAILURE, AND IT IS
            // COUNTED. A parseable body with no recognized state used to
            // fall through here and re-tick in silence until
            // MAX_POLL_DURATION_MS gave up, hours later -- the actual
            // shape of the two post-crash turns that polled for ever at
            // "Queued — waiting…". Forward compatibility is still honoured
            // (an unknown state is retried, not treated as an error on the
            // first tick); what it no longer buys is an unbounded loop.
            transportFailures += 1;
            if (transportFailures > MAX_TRANSPORT_RETRIES) {
              showCardError(card, "Lost contact with the server — reload this page.", "");
              return;
            }
            setTimeout(tick, POLL_INTERVAL_MS);
          }
```

A **parseable** 5xx that is not the 503 handled above goes through the same counter — printed here because Task 18's own test asserts the literal `result.status >= 500` appears in the rendered script, and prose would not put it there:

```js
          if (result.status >= 500) {
            // A 5xx whose body DID parse. Its unparseable twin already
            // lands in .catch() and is counted there; this one used to
            // fall through to the state branches below with no state to
            // match, and re-tick in silence.
            transportFailures += 1;
            if (transportFailures > MAX_TRANSPORT_RETRIES) {
              showCardError(card, "Lost contact with the server — reload this page.", "");
              return;
            }
            setTimeout(tick, POLL_INTERVAL_MS);
            return;
          }
```

Move `transportFailures = 0;` so it is reset only by a response the page **could** act on (a recognized state, or the terminal 503), never by merely parsing JSON — which means it moves **below** both the 503 branch and this one.

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q agents/chat/tests/ foundation/ops/tests/test_shared_poller.py
```

Expected: green, and **both** halves of the shared-poller gate still pass — the exemption's companion assertion (the loop still exists) and the repo-wide "no other template rolls its own".

- [ ] **Step 4: Commit**

```bash
git add agents/chat/views/turns.py agents/chat/templates/chat/conversation.html agents/chat/tests/
git commit -m "fix(chat): a poll response the page cannot act on is counted

Two post-crash turns polled for ever at 'Queued — waiting…'. The 5xx with
an unparseable body was never the uncounted branch -- that already rejects
in response.json() and lands in .catch(). The uncounted branch was a
PARSEABLE body carrying no recognized state, which fell into the
forward-compatible final else and re-ticked in silence until the duration
ceiling hours later.

That branch now increments the same bounded counter a dropped connection
does, and exhausting it shows the same honest note. Best-effort beside it:
turn_status answers a retryable 503 when the database is momentarily
unavailable, distinct from the terminal 'the queue is not configured' 503,
which keeps its setup link. The guard wraps the whole view body because
principal resolution, turn visibility and session middleware all touch the
database before any body builder runs."
```

---

## Task 19: The ADR amendment, and every doc this track made stale

**Spec:** §4 (the amendment's seven items) and §6 (the doc list).

**Files:**
- Modify: `docs/adr/0013-inference-execution-queue.md` (one dated amendment)
- Create: `models/queue/README.md` (**does not exist today** — verified 2026-09-21; the spec names it, so this task writes it)
- Modify: `models/registry/README.md`, `agents/chat/README.md`, `agents/runtime/README.md`, `docs/OPERATIONS.md`, `docs/EXTENDING.md`
- Modify: `models/contracts/engines/base.py` (docstring only — the two attributes and their safe defaults; done in Task 12, verified here)

**Interfaces:** none. Documentation only.

**The amendment records, in this order (spec §4):**

1. **§4 memory accounting** — the four-rung ladder and its own label vocabulary; the keep-the-maximum recorder with the geometric-ratchet reasoning for having no tolerance band.
2. **§4/§7 eviction** — `unload_scope` and `residency_authority` and why the queue must know both; the protected-key rule **including that the admitted batch is protected** and the single endpoint-scope exception; the **fifth ordering rule** (the protection check runs before the residency snapshot, so a refusal costs no HTTP); the redesigned barrier including the ambiguity of `False`, the narrowed precautionary call, and the refusal bounded in count **and** wall clock with a `not_before` hold-off; the cross-engine sweep and its effect on the budget arithmetic; **the budget gate moving** so the barrier, the sweep and the logging are unconditional while only the capped pass stays gated; and the log vocabulary.
3. **§3/§9 ordering** — affinity batching within a priority with an aging bound, the `affinity_order` seam, and the explicit note that the no-backfill deadlock proof and the honestly-scoped starvation caveat are both unchanged. **`not_before` is recorded here too**, as the one admission-order change this track makes, with the sentence on why the proof survives: the exclusion is time-bounded and self-clearing, and an effectively-exclusive head is still admitted alone the instant the machine is idle.
4. **§7 worker lifecycle** — the heartbeat thread and its connection story, kind-aware staleness and the two `claim_and_admit` signature changes, sleep detection, the restore-to-the-live-attempt refusal, and boot tolerance.
5. **§5 kind registry** — `stale_after_seconds`, `default_wait_seconds`, the operator-editable per-kind wait map riding the existing settings read, and the rule that a wait ceiling may never release the exclusive slot early.
6. **§8 named gaps** — the three follow-ups named on 2026-08-25 (staleness, early slot release, eviction's blindness to a live attempt) are **discharged**, with a sentence each saying how; the spec's §11 residuals are added in their place — **including the three adapter-level ones the engine steward contributed** (a `True` from the no-baseline path that observed nothing; machine-wide memory drift faking the settle rise; the eight-endpoint LRU cap on the residency memo). Named, not fixed: all three are adapter-side.
7. A closing note that ADR 0015's agent-layer gaps are untouched: this track fixes two chat-surface defects and changes no tool contract.

- [ ] **Step 1: Write the doc-gate checks first**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_docs_model_names.py foundation/ops/tests/test_docs_sync.py foundation/ops/tests/test_agent_standards.py
```

These must be green **before** the edits, so a later failure is unambiguously this task's.

- [ ] **Step 2: Write the docs**

- `docs/adr/0013-inference-execution-queue.md` — one `## Amendment (2026-09-21) — Queue memory governance` section in the style of its five predecessors, covering the seven items above. Dated amendment, never a silent rewrite of the original text.
- `models/queue/README.md` — **new**: what the column is, the four-rung ladder as the queue consumes it, eviction's reach (the swept set, the protected-key rule, `unload_scope`/`residency_authority` and their safe defaults), the log vocabulary an operator will actually see, and what an unset budget does and no longer does. Match the shape of `models/registry/README.md`.
- `models/registry/README.md` — the three stored facts, the keep-the-maximum rule, the two label vocabularies and why they are separate.
- `docs/OPERATIONS.md` — a new section: each new log line and what to do about it; the failed-after-three-refusals error an operator will find on a job row; and `manage.py reconcile_turns` with its `--dry-run`.
- `docs/EXTENDING.md` — in the job-kind material: a kind declaring its `stale_after_seconds` and `default_wait_seconds`, and the rule about never releasing the exclusive slot early.
- `agents/chat/README.md` — stranded-turn reconciliation, the poll loop's failure vocabulary, and **the obligation**: when the held follow-up repoints this page onto the shared loop, change 1 must be carried onto `foundation/templates/_poller.html` or it is silently lost.
- `agents/README.md` — the module-list row for `agents/reconcile.py` and the command. *(2026-09-21 correction, Task 19: this line read "`agents/runtime/README.md` — `jobs.py`'s two new functions and the command". The reconciliation shipped in `agents/reconcile.py`, outside `agents/runtime/`, so it is documented in the column README's module list; `agents/runtime/README.md` carries only a one-line pointer from its `jobs.py` row.)*

**No model or vendor names anywhere** — engine names only ("the image engine", "the text engine", `ollama`, `comfyui`).

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest -q foundation/ops/tests/
```

- [ ] **Step 4: Commit**

```bash
git add docs/ models/queue/README.md models/registry/README.md agents/chat/README.md agents/runtime/README.md
git commit -m "docs(queue): ADR 0013's memory-governance amendment, and the docs it made stale

One dated amendment covering the four-rung ladder and the
keep-the-maximum rule, the eviction rewrite (the two engine declarations,
the protected-key rule and its one exception, the fifth ordering rule, the
redesigned barrier and its hold-off, the cross-engine sweep, the budget
gate moving, the log vocabulary), affinity ordering and not_before as the
one admission-order change, the worker lifecycle, and the kind registry's
two new declarations.

It also DISCHARGES the three follow-ups named on 2026-08-25 -- staleness,
early slot release, eviction's blindness to a live attempt -- and records
the residuals that replace them, including the three adapter-level ones
the engine steward contributed: a True that observed nothing on the
no-baseline path, machine-wide drift faking the settle rise, and the
eight-endpoint cap on the residency memo. Named, not fixed.

models/queue/README.md is new -- the column had none."
```

---

## Smoke Checklist

Driven by a human, in a browser, on **this branch's own preview stack** (`scripts/preview up <branch> --port <p> --db-port <q>`), after the code review passes and before the merge decision. `docker compose restart watcher worker` first — queue, worker and job-kind code do not auto-reload.

1. **A real cross-engine eviction.** Leave a model warm on the engine nothing is using. Submit an exclusive job on the *other* engine. → The new INFO lines name the swept endpoint count and one `accepted` unload at the idle endpoint; the Queue page's "Running now" reading falls.
2. **A barrier refusal is honoured, and told apart from an empty endpoint.**
   (i) Against a *believed-resident* endpoint that does not release: the job is requeued with the WARNING line, its waiting row reads "waiting for engine memory — retries at HH:MM", and it fails honestly **only** after three attempts spanning the minimum wall clock.
   (ii) Against a **cold, empty** endpoint: the precautionary call's `False` is logged at INFO and **the job launches on that same tick**.
   (iii) **Timing:** record the wall-clock delay the barrier adds to an ordinary exclusive admission (send one chat message and time it). Expected within a second or two on a box whose engines report residency authoritatively. **A 30 s per-turn regression means the precautionary narrowing is not working** — catch it here, not in use.
   Without (ii) and (iii) the mechanism is not proven.
3. **The barrier runs with no budget set.** Repeat 1 or 2 with `memory_budget_bytes` unset. → sweep, barrier and log lines all active.
4. **A cold load survives staleness.** A kind declaring a long `stale_after_seconds` completes a multi-minute cold load without being orphaned, and the job finishes on the page.
5. **A lowering is refused.** Take a measurement while another model is resident. → the standing footprint does not fall; the console shows the higher value with its new label; the refusal line names both numbers.
6. **Affinity batches, visibly.** Three same-model jobs and one other, all at one priority, submitted interleaved. → the same-model jobs run consecutively; the passed-over job shows "passed over twice" on the Queue page and then runs once pinned.
7. **A wait ceiling holds the slot.** A kind whose ceiling expires while its engine is still working does not go terminal: the page still shows it running, no second job is admitted, and it finishes when the engine does.
8. **The unset budget is loud.** The Queue page callout, and the settings prefill labelled "detected by the worker process on *date*".
9. **A stranded turn recovers.** Remove a turn's job row under it. → an honest failed card within one poll, instead of "Queued — waiting…" for ever. Repeat with JavaScript disabled and a plain reload.
10. **A recovering database does not strand the card.** A parseable body with no recognized state → bounded retries, then the honest note; never a silent tick.
11. **A cold boot is quiet.** Start the worker against an unmigrated database. → one waiting line, no traceback, at both read sites.

---

## The verification ladder (house rules, in order — no rung skipped)

1. **Module suites, both collection orders:**
   ```bash
   export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
   .venv/bin/pytest -q models/queue models/registry models/contracts agents/chat agents/runtime foundation/ops
   .venv/bin/pytest -q foundation/ops agents/runtime agents/chat models/contracts models/registry models/queue
   ```
2. **The four runs — the branch's gate** (both flag states × both collection orders), serial, foreground, native:
   ```bash
   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
   FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
   FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
   ```
3. **Migrations and system checks:**
   ```bash
   .venv/bin/python manage.py makemigrations --check --dry-run
   .venv/bin/python manage.py check
   ```
   Expected: "No changes detected" and "System check identified no issues". **Exactly two new migration files exist on this branch** — `models/registry/migrations/0009_*` and `models/queue/migrations/0005_*`.
4. **Preview proofs:** the Smoke Checklist above, by hand, on this branch's own stack, `watcher` and `worker` restarted first.
5. **Pull request into `dev`**, with current `dev` merged into the branch and resolved there first, in this branch's own worktree; Task 11's adapter hunk flagged to the engine steward; the two **newly authored** query-count pins named with their literals. **The merge decision is the owner's**, and no merge is offered while any issue is open.

   **One flow, and it is `dev`** (`AGENTS.md`, "Deploying"): merge current `dev` INTO the feature branch → pull request → review → the owner's merge word → `dev` → the root checkout fast-forwarded to `origin/dev` → restart → verify in a browser → announce "landed". `main` plays no part in this branch's landing — it takes only batched, validated release pull requests from `dev`, on the owner's word.
6. **Live deploy window** (announced, one at a time, on the owner's word): the root checkout — which **is** production — fast-forwards to `origin/dev` after `git status --short` there is confirmed clean (never a merge into that tree: a conflicted merge left there is picked up by the autoreloader and takes the live web server down), then `docker compose restart watcher worker` — the pool size, the heartbeat thread and the detected-memory write are all process-level, so a restart is mandatory, not optional.
7. **Live proofs:** the spec's §8 items 1–11, on the live box, captured as pixels or log excerpts.

**No success language before rung 7.** Mid-ladder reports use progress phrasing; an edit that still needs a step is "edited, not yet live".

---


## Self-review

**1. Spec coverage sweep** (spec revision 5, every section accounted for; task numbers are the post-review 19).

| Spec section | Task |
|---|---|
| §3.1 rungs 1–4, `effective_footprint_bytes`, four-value `footprint_source` | 1 |
| §3.1 `_FOOTPRINT_SOURCE_LABELS`, the named template edit, the re-pinned assertions, `_SOURCE_LABELS` untouched | 3 |
| §3.1 rung-3 written only from a positive reading, never a zero | 2 (the rule), 12 commit 1 (the call site) |
| §3.1 steward amendment S-2 — the "residency snapshot" wording and the image-engine equivalence | 3 |
| §3.2 keep-the-maximum, both columns, the refusal line and its two levels, no tolerance band | 2 |
| §3.3(a) `unload_scope` + `residency_authority`, getattr/degrade, both safe defaults | 12 commit 1 (consumption), 11 (the pre-cleared declarations) |
| §3.3(b) residency is a belief; an unload is not a harmless no-op | 12 commit 1 |
| §3.3(c) protected keys: RUNNING ∪ admitted batch ∪ live in-flight attempts; per-scope rule; the one sanctioned exception and its boundary | 12 commit 1 |
| §3.3(d)(1) barrier scope = the whole swept set | 12 commits 1–2 |
| §3.3(d)(2) protection first, before any HTTP — the fifth ordering rule | 12 commit 2 |
| §3.3(d)(3) the calls, and the precautionary narrowing keyed off `residency_authority` | 12 commit 2 |
| §3.3(d)(4) `False` honoured only for a believed-resident call | 12 commit 2 |
| §3.3(d)(5) bounded refusal: count **and** span, informative refusals only, reset on success, `not_before` hold-off, honest failure | 12 commit 2 |
| §3.3(e) `registered_endpoints()`, the console refactor, the exclusive-only widening, the stated budget consequence | 4 + 12 commit 1 |
| §3.3(f) the budget gate moves; only the capped pass stays gated | 12 commit 1 |
| §3.3(g) the log vocabulary, and what WARNING is reserved for | 12 commit 3 (queue) + 2 (the registry's refusal line) |
| §3.4(a) the heartbeat thread and its whole connection story | 5 |
| §3.4(b) `stale_after_seconds`, the two `claim_and_admit` signature changes, the grouped sweep query | 6 |
| §3.4(c) sleep detection, immediate heartbeat, `sweep_orphans=False` for one grace period | 7 |
| §3.4(d) the duplicate-submit refusal restoring the row to the live attempt, and the zero-rows fallback | 8 |
| §3.5(a) `default_wait_seconds`, `kind_wait_seconds`, `JobContext.wait_seconds`, no extra query | 16 |
| §3.5(b) the rule that a ceiling may never release the exclusive slot early | 16 (seam docstring) + 19 (ADR/EXTENDING) |
| §3.6 `affinity_order`, the ordering key, pinning, the claim-side increment, the snapshot's real staleness | 14 (+ its cache written in 12 commit 1) |
| §3.6 closing ruling — the hold-off renders on the waiting row, display only | 13 |
| §3.7 loud unset budget, worker-written detected memory, the honest label | 15 |
| §3.8 `reconcile_stranded_turn`/`_turns`, the two call sites, the null-job-id no-op, no sweeper | 17 |
| §3.9 change 1 (load-bearing) and change 2 (best-effort), the gate and the carry-forward obligation | 18 (+ 19 for the obligation) |
| §3.10 boot tolerance at both sites, both exception classes | 9 |
| §4 the ADR amendment's seven items | 19 |
| §5 every named test | each task's own test steps; §5's three query-count pins land as **one re-used** (`TestTheSingleSettingsReadPerTick`, verified untouched in 12 and 16) and **two newly authored** (the widened sweep in 12 commit 3, the pass-over `UPDATE` in 14) |
| §6 every named doc | 19 |
| §7 task order, two migrations / two apps / seven columns, the cross-column flag, the deploy note | Task ordering; Tasks 1 and 10; Task 11; the ladder's rung 6 |
| §8 done-when, all eleven live proofs | Smoke Checklist + ladder rung 7 |
| §9 author decisions 1–16 | Honoured as written; each is named in the task that implements it |
| §10 owner decisions 1–7 | All accepted (closing ruling); implemented in Tasks 15, 1, 2, 14, 16, 12, 17 respectively |
| §11 residuals | Not built — recorded in the ADR amendment (Task 19) |
| §12 out of scope | Nothing in this plan builds any of it (see below) |
| §13 review record and the steward amendment | History; S-1 → Tasks 11 and 12, S-2 → Task 3, S-3 → Task 19 |

**Deliberately not given their own task**, because the spec names them as out of scope: backfill or preemption; multi-worker coordination; a real memory manager; **probing** an engine for unload granularity or residency authority (the queue reads declarations only); automatic budget tuning; adapter internals — including the three §11 adapter residuals, which are named in the ADR and fixed nowhere; migrating the chat page onto the shared poll loop; streaming turn output; and retrying a job after a barrier refusal beyond §3.3(d)(5)'s bounded attempts.

**2. Placeholder scan.** No "TBD", no "similar to Task N", no step that describes code without showing its shape.

**Three deliberate `<literal>` markers, and only three** — Task 12 commit 3's widened-sweep pin, Task 13's Queue-page pin, and Task 14's pass-over pin. Each is a query count that **must** come from a first red run rather than from an author's guess (a guessed number is either wrong or, worse, accidentally right for the wrong reason), and each carries the instruction to fill it from that run and to record it in the commit message. Every one of them is paired with a second assertion at **the same literal** under a larger fixture, so the number cannot be vacuous. They are not placeholders for undecided behaviour.

Five places deliberately send the implementer to the real tree rather than to this plan, each naming what to run: Task 3's narrowed `grep` for the four disclosure assertions (with the expected result and what to do if it differs), Task 6's `grep` for where the `JobKind` field tests live, Task 12 commit 1's instruction to prove the shipped `TestEviction` suite still green **before** writing anything else, Task 15's `grep` for the Queue template's real class names, and Task 18's `grep` for the module that already asserts at the rendered-script level.

Task 12 gives the full docstring and rules for every new method plus the **end-state signature of all eleven**, and describes the bodies of `_unload_endpoint` and the barrier helpers in prose rather than line by line — the one place this plan trades literal code for precision about behaviour, and it does so because those bodies rewrite existing methods whose surrounding code the executor must read anyway.

**3. Type consistency.**
- `footprint_source` values are `"override" | "measured" | "engine_reported" | None` in Tasks 1, 2 and 3, in the model, the label map and every test.
- `registered_endpoints() -> list[tuple[str, str, tuple[str, ...]]]` is spelled identically in Task 4 and in Task 12's end-state contract, and its optional `connections` argument is a `list[ModelConnection]` in both.
- The model key is `tuple[str, str, str]` — `(engine, norm_endpoint(endpoint), norm_tag(model_id))` — everywhere a key appears: `_protected_keys`, `_unload_endpoint`'s **return**, `_residency_snapshot`'s `believed_resident`, `SchedModel.key`, `affinity_order`'s `resident_keys`, and `Worker._resident_keys`. Built with `norm_endpoint`/`norm_tag` at every construction site, per the scheduler's provenance contract.
- **The producer/consumer chain the review found broken is now closed:** `_residency_snapshot` produces `believed_resident: set[key]`, `_unload_endpoint` produces `released: set[key]`, and `self._resident_keys = frozenset(believed_resident - released)` consumes exactly those two. No expression in this plan names a value no interface produces.
- `_unload_scope` returns `"model" | "endpoint"`; `_residency_authority` returns `"endpoint" | "memo"`. The two vocabularies share the word "endpoint" and mean different things — the plan and the docstrings always name the attribute alongside the value.
- `_evict_to_match_plan(claimed, *, settings_row=None) -> set[int]` is the same signature from Task 12's first commit onward, returning `set()` until commit 2 has anything to put in it.
- `claim_and_admit(worker_id, *, stale_after_seconds, sweep_orphans=True, settings_row=None, resident_keys=frozenset())` — Task 6 adds the middle keyword, Task 14 the last; the final signature is stated in both.
- `affinity_order(candidates, *, resident_keys, max_passovers) -> list[SchedCandidate]` is identical in the scheduler, the claim code and the tests.
- `reconcile_stranded_turn(turn) -> bool` and `reconcile_stranded_turns(grace_seconds) -> int` are spelled identically in Task 17's implementation, its command, its call site and both test modules.
- `JobContext.wait_seconds` is `float | None` in the dataclass, the worker's stamp and the tests — matching `response_timeout_seconds` beside it.

**Resolved ambiguities, recorded so a reviewer sees the call rather than reverse-engineering it:**

- **`registered_endpoints` takes an optional connections list.** The spec says the console helper is "refactored to share this one implementation" and says nothing about queries; the console is pinned at 10 by two modules. A helper that always fetched would add an eleventh on every render.
- **`_inflight_refs` is a second map beside `_futures`.** The protected set needs the model keys a live attempt holds and a `Future` does not carry them; the only query-free alternative — re-reading `InferenceJob.model_refs` for the live job ids — puts a query on the 0.5 s tick path. Its write sits in the same statement block as the `_futures` insert, **below** the duplicate-submit refusal's early return, and `_prune_finished_futures` drops from both maps by the same key.
- **`_evict_to_match_plan` returns refused job ids.** The spec requires that a refused job is not launched this tick but names no mechanism. A return value keeps the decision where the evidence is; an ambient `self._refused` attribute read across methods would buy nothing.
- **Migration 2 is authored whole in Task 10.** The spec has `not_before` arriving in its task 3 and the other four columns in tasks 4 and 5, while insisting on exactly two migrations. Django migrations are files, so honouring both means creating all five columns at once, before the tasks that consume them.
- **Detected memory uses `os.sysconf` behind a module-level `_total_memory_bytes()`, not `psutil`.** `psutil` is not in `requirements.txt` and the offline-by-default rule makes a dependency for one prefill a poor trade; the helper exists so a test can replace this module's answer without patching the stdlib object the whole process shares.
- **`FOOTPRINT_DIP_WARNING_RATIO = 0.75` is a log-level threshold only.** The spec asks for "a documented 'obviously broken' ratio" without naming one, and is emphatic that no dip is ever written.
- **`BARRIER_HOLDOFF_SECONDS = 45`, `MIN_BARRIER_REFUSAL_SPAN_SECONDS = 300`.** The spec fixes `MAX_BARRIER_REFUSALS = 3` and requires the hold-off to be "comfortably longer than one unload timeout" (30 s) and the span to make "three attempts" mean real time.
- **Pinning uses `>=`, not `==`.** A durable counter and a `max_passovers` an operator could see lowered must not leave an over-aged job unpinned.
- **Affinity requires a non-empty key set.** "All of its keys are resident" is vacuously true for a job declaring no models, which is already effectively exclusive under rule 2c.
- **The registry's six stewarded `test_views_*.py` modules are edited only for the four re-pinned disclosure assertions**, all of which live in `test_views_tables_and_picker.py`. Every new registry test goes in `test_footprint_provenance.py`.
- **The rendered footprint date is localized before formatting.** `date_format` alone does not localize and the template's `|date` filter does; on a non-UTC box the difference is a visibly wrong day.

---

## Plan review

### Round 1 (2026-09-21) — **AMEND: 7 Major / 12 minor / 5 nit. All 24 applied; none skipped, none disputed.**

Three explicit rulings on the author's own flagged concerns came with it, all three applied as ruled.

| Finding | Applied in | Note |
|---|---|---|
| **M1** Task 12 silently reds the shipped `TestEviction` suite | Task 12, commit 1, step 1 | **Option 1 of the two offered:** the shipped stub declares `unload_scope = "model"` (which is what its per-model call counts mean) and a new `FakeEndpointScopeEngine` carries the endpoint-scope cases. **No shipped assertion changes**, and step 1 opens by proving that (`pytest -k Eviction` green on the helper additions alone) before anything else is written. |
| **M2** `_unload_endpoint -> int` cannot feed the affinity cache | Task 12 end-state contract; commit 1 | `_unload_endpoint` returns `set[key]` (the keys actually **released** — all believed-resident non-protected keys at an `"endpoint"`-scope endpoint, not the key addressed), and `_residency_snapshot` gained `believed_resident` as an explicit second output. The cache expression now names two values the interfaces produce. |
| **M3** a refused launch leaks an `_active_tokens` entry for ever | Task 12, commit 2 | Both `_requeue_refused` **and** `_fail_barrier_refused` pop `self._active_tokens[job_id]` under `_active_lock` before their conditional UPDATE, matching `_requeue_unlaunched`'s documented shape and reason. Two tests assert `_active_tokens == {}` after a refused tick and after a failed one. |
| **M4** `_inflight_refs`' pruning contract is under-specified where it leaks | Task 12 commit 1; forward note in Task 8 | The write is pinned to the same statement block as the `_futures` insert, **below** Task 8's early return (Task 8 now carries a note saying so at the return itself); `_prune_finished_futures` drops both maps by the same key set; three tests (`TestTheInFlightRefsMapNeverLeaks`). The `_active_lock` asymmetry is gone — both maps are declared tick-thread-only. |
| **M5 / Ruling A** Tasks 12–14 are one rewrite with no declared end state | **Tasks 12–14 collapsed into Task 12** | One task, three commits, headed by an **end-state contract block** giving the final signature of all eleven symbols plus the three pieces of worker state and the three constants. Commit 1 lands **with the budget gate already moved**, so its protected-key rule is live on a no-budget box rather than dead code. The falsified "ONE place an unload is called" claim is corrected in invariant 1 and restated at the precautionary call site. |
| **M6** a protection refusal could fail healthy chat turns | Task 12, commit 2, part 5 | Only `_barrier`'s believed-resident `False` calls `_record_barrier_refusal`; `_protection_refusal` never touches it, and `MAX_BARRIER_REFUSALS`' own comment says why. Paired test: three consecutive protection refusals past the span leave the job `queued` and `_barrier_refusals == {}`. |
| **M7 / Ruling B** the query-count pins this plan "moves" do not exist | Global Constraints; Task 12 commits 2–3; Task 14 | Re-pin language deleted from both commit messages and both verify steps. The Global Constraints bullet now states the measured arithmetic (`not_before` +0, pass-over +1, widened sweep +0 SQL) and that **two pins are newly authored**: the widened sweep's (Task 12 commit 3) and the pass-over `UPDATE`'s (Task 14). Both are literal and non-vacuous by construction. |
| **m1** `date_format` does not localize | Task 3 | `timezone.localtime(at)` first, with the reason in the code comment, plus a test that pins the local date under a non-UTC `TIME_ZONE`. |
| **m2** wrong path for the template-comment gate, twice | Task 3 | `models/registry/tests/test_template_comments.py`; the stray `foundation/ops` path is out of the run command. |
| **m3** Task 3's expected grep output is wrong | Task 3; File Structure | Narrowed to `grep -rn "dt>Memory footprint</dt>\|dt>Last measured</dt>"`, with the real result stated (eight hits, one module, four absence + four value). The File Structure row for `test_views_connection_edit.py` now reads **not edited**, with why. |
| **m4** the heartbeat thread's 1.0 s floor defeats its own tests | Task 5 | Floor dropped; the loop waits `HEARTBEAT_SECONDS / 2` with a comment saying a floor makes the thread unprovable. |
| **m5** the interleave test pins implementation and is self-defeating | Task 5 | Replaced with a behavioural test: hold `_active_lock` from the test thread and assert `_maybe_heartbeat` cannot complete its throttle read. |
| **m6** three test helpers that do not exist | Task 12, commit 1, step 1 | `ENDPOINT`, `_installed(...)` and `FakeEngine.list_installed_calls` are added explicitly, with the reason, before any test uses them. |
| **m7** a query count asserted against a computed baseline | Task 13 | Literal, filled from the first red run, plus a second assertion at the same literal with a different row count so it cannot be vacuous. |
| **m8** printed markup contradicts its own instruction and would trip the CSS gate | Task 15 | `class="settings-current"` printed (the class the template actually uses), the `notice` invention removed, and the grep instruction kept as a check rather than a contradiction. |
| **m9** the `F()` import is unnamed | Task 14 | Named beside the `Q` import Task 6 adds. |
| **m10** form count off; the settings-read pin is fixture-order dependent | Task 16 | "Fourth form", with the three shipped ones named; the pin now creates the singleton row first so `get_or_create`'s savepoint/INSERT cannot decide the count. |
| **m11** the ladder mixes two merge flows | Ladder rungs 5–6 | One flow, **`dev`**, matching `AGENTS.md`'s "Deploying" loop end to end — see the orchestrator ruling below, which closed this after the cutover landed. |
| **m12** the grace-period test is vacuous | Task 7 | A small positive grace, an assertion that the sweep was skipped **while** it was in force, then the resume. |
| **n1** a zero footprint renders no row | Task 1 | Stated: the scheduler's intent wins, the console's missing row is accepted, and why — recorded so nobody "fixes" the ladder to match the renderer. |
| **n2** `_protected_keys(claimed)` never reads `claimed` | Task 12 end-state contract | Parameter dropped; it is `_protected_keys(self)`. |
| **n3** no mechanism for dropping Task 11's commit | Task 11 | `git revert <sha>` before the pull request, with why a rebase is the wrong tool here (a later commit touches the same file's neighbourhood). |
| **n4** the `>= 500` branch is asserted but never printed | Task 18 | The branch is printed, and the `transportFailures = 0` move is restated as "below both". |
| **n5** monkeypatching `worker_module.os.sysconf` patches the real `os` | Task 15 | **Variant:** rather than a narrower patch target, the read moves behind a module-level `_total_memory_bytes()` helper the test replaces on the worker module. Same outcome, and it stops a future test reaching into the stdlib at all. |

**Ruling A** (collapse) — applied as required: one task, three commits, end-state contract first, commit 1 with the gate already moved.
**Ruling B** (the pins) — applied as ruled, including the widened-sweep pin spec §5 names.
**Ruling C** (`_inflight_refs` / `_resident_keys`) — applied: the pruning contract is now complete on every exit path, and `_resident_keys` is recorded as spec-mandated rather than an author addition, with its producers fixed (M2).

**m11's hedge is closed by an orchestrator ruling (2026-09-21): the `dev` cutover IS law.** The single-repo docs PR merged into `dev` and deployed while this plan was being written, and `origin/dev` was merged into this branch afterwards — so `AGENTS.md` ("Deploying") and `docs/DEV.md` rung 3 in this worktree now state the real flow, and both were read against the tree before this edit: merge current `dev` into the branch → pull request into `dev` → the owner's merge word → root checkout **fast-forwarded to `origin/dev`** (never merged into) → restart → browser. `main` takes only batched release pull requests from `dev`.

Ladder rungs 5 and 6 now name `dev` **together** — which was the review's actual complaint, that an earlier draft had half of one flow and half of another — and the conditional sentence the hedge carried is gone. Nothing else in the plan depended on it: no task, test, command or commit message names a target branch.

### Round 2 (2026-09-21) — scoped re-check: **0 Major / 3 minor / 2 nit.**

Every Major and all three round-1 rulings verified **closed** against the tree. The reviewer recorded that none of the five residuals changes a design decision or needs another round; the orchestrator adjudicated all five **ACCEPT** as a final micro-edit pass. **No round 3.**

| Finding | Applied in | Note |
|---|---|---|
| **m13** the widened-sweep pin is vacuous — three unregistered idle endpoints are dropped by `_get_engine_or_none` before the sweep reaches them, so the count stays flat however the swept set is built | Task 12, commit 3 | Each idle endpoint now registers its own fake engine, so all four genuinely enter the sweep and are genuinely probed; the test additionally asserts `idle1.list_installed_calls == 1` so the non-vacuity argument is proven rather than asserted in a docstring. |
| **m14** eight helper names are called in plan code and defined nowhere | Tasks 6, 12, 14, 17, 18 | All nine now defined where first used, each built on what the module already has (`_job`/`_ref`/`_set_budget` in `test_claim.py`, `model()`/`candidate()` in `test_scheduler.py`, `_turn_for()` in `test_jobs.py`): `_running_job`, `_queued_job`, `_running_job_holding`, `_admitted_exclusive`, `_candidate`, `_warm_keys`, `_assistant_turn`, `_stranded_assistant_turn`, `_rendered_conversation_script`. **Every name called in plan code is now defined in the plan or in the tree.** |
| **m15** "the widened sweep adds no SQL at all" is false | Global Constraints; Task 12 commit 3 | Corrected to **+1**: `_eviction_targets` calls `registered_endpoints()` without `connections=`, so it fetches the rows itself, once per exclusive-admitting tick regardless of endpoint count. The pin's framing moves from "no SQL" to "the same literal at one endpoint and at four" — which is the shape that actually catches a per-endpoint lookup — and the commit message says the same. |
| **n6** the m11 row still said "main" after the cutover ruling | this section | Fixed to `dev`, pointing at the ruling below. |
| **n7** `assert "e" in job.error` cannot fail | Task 12, commit 2 | Replaced with assertions against the specced copy: `"did not release memory"`, `"engine 'e'"`, the endpoint, and `"3 attempts"`. |

**The `dev` cutover ruling (2026-09-21), closing m11.** The single-repo docs pull request merged into `dev` and deployed while this plan was being written, and `origin/dev` was then merged into this branch — so `AGENTS.md` ("Deploying") and `docs/DEV.md` rung 3 **in this worktree** state the real flow, and both were read against the tree before the edit: merge current `dev` into the branch → pull request into `dev` → the owner's merge word → root checkout **fast-forwarded to `origin/dev`** (never merged into) → restart → browser. `main` takes only batched release pull requests from `dev`. Ladder rungs 5 and 6 name `dev` **together** — the review's actual complaint was half of one flow and half of another — and no task, test, command or commit message in this plan names a target branch, so nothing else moved.

**The plan is closed at round 2.**
