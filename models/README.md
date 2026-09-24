# models/ — model handling and execution

The model-management framework: how an engine + model gets bound to a
role, and how work gets queued and run.

- **[`contracts/`](contracts/README.md)** — pure, Django-free platform
  contracts: roles and operations (`roles.py`, `operations.py`), job
  kinds (`jobkinds.py`), the queue seam (`queue.py`), the registry
  bindings (`bindings.py`), the inference gateway (`gateway.py`), the
  model catalog (`catalog.py`), and the per-engine adapters (`engines/`).
  A rule-1 **pure leaf**: no Django models, no views, no database, no
  import of any non-pure module.
- **[`registry/`](registry/README.md)** — the model registry (Config &
  Secrets): which `ModelConnection` backs each role right now, plus the
  ACCESS console UI operators use to manage it. A Django app, label
  `inference`, and **column-private** under rule 2. As of Identity &
  Auth IA-2, also **model sets** (`ModelSet`/`ModelSetMember`/
  `ModelSetEntitlement`) and `registry/access.py::model_access_for` —
  which registered connections a principal may USE, gating the chat,
  vision and Ask pickers with the role path (`db_provider`/
  `role_primary`) deliberately exempt. See that column's README for the
  full account, including why `model_access_for` is re-exported from
  `models.registry.bindings` rather than imported from `.access`
  directly.
- **[`queue/`](queue/README.md)** — the execution queue: job kinds,
  scheduling, and the worker that runs them. A Django app, label `jobs`,
  column-private under rule 2. See that column's README for the footprint
  ladder as the queue consumes it, eviction's reach and its protected-key
  rule, the log vocabulary an operator sees, and what an unset memory
  budget does and no longer does. `JobSettings.max_queued_per_principal`
  (round-3 hardening C-7/H39) is an operator-editable cap on how many
  QUEUED-or-RUNNING jobs one principal's own payloads may hold at once
  — null (the shipped default) means no cap, matching `memory_budget_
  bytes`'s own "honestly unknown, not silently assumed" convention. Set
  on the "Job execution" settings page's own form (fix round 1), not
  by editing the row directly — the same page `max_concurrent_jobs`/
  `retention_limit`/`default_priority` are already set on.
  Enforced once, at `models.queue.backend.enqueue` (`models.contracts.
  queue.QueueQuotaExceeded`), never per-caller — jobs stamped
  `SERVICE_PRINCIPAL` (the watcher, the registry's rematerialize
  callback, every `manage.py` shell command) are exempt, since that
  identity is the operator's own work, not a principal's.

## Why a workstream concept lives in this column

`registry/access.py::model_access_for` (and its `models.registry.
bindings` re-export) takes a keyword-only `wall: frozenset[int] =
frozenset()`, added by the Workstreams phase. It is the ONE change that
phase makes in this column: `wall` narrows the `held` half of the
returned `ModelAccess` by intersection with whatever entitlements the
acting principal already holds — never by union, so a wall can only
remove access, never grant it — and it is empty for every caller outside
a workstream turn, which is what keeps every existing call site's
meaning exactly as it was. The value itself is a plain `frozenset[int]`
of entitlement ids, not a workstream object or an import of anything
under `agents/`; the caller (`agents.entitlements.wall_for`, reading
`agents.workstreams.wall_ids`) is the one read for three of the four
call sites of §6.3 -- `agents.runtime.jobs`, `agents.runtime.preflight`,
`agents.chat.views.thread` -- and for `agents.runtime.loop` on the one
anomalous path where this turn's `workstream_scope` came back `None`
(not admitted, or no stream) but the wall must still bind; on the
common, ADMITTED path `agents.runtime.loop` reuses the `.wall` its own
`workstream_scope` call already read, rather than asking `wall_for`
again for the same table. Either way the SAME value threads here and
into `agents.entitlements.tool_access_for` alike, so a reader who finds
a workstream-shaped parameter in this column's own access function is
seeing the same "wall is a scope somebody chose" rule the tool axis
enforces, applied to the model axis for the identical reason.

## Never `import models`

**Convention, with no exception anywhere in this codebase: never write a
bare `import models`. Always `from models.<sub> import ...`.** Every
Django `models.py` in the repo opens `from django.db import models`; the
two forms never conflict on their own, because `from X.Y import Z`
resolves through `sys.modules` and never through a module-level name —
but a bare `import models` followed by `models.contracts....` WOULD be
shadowed by that local name, silently picking up whichever `models`
happens to be bound in that scope. See spec §3.7
(`docs/superpowers/specs/2026-08-25-agents-and-tools-design.md`).

## The import law

- **Rule 1 — pure leaves are universally importable.** `models/contracts/`
  (like `foundation/format.py`, `foundation/files.py`, `agents/contracts/`,
  and `identity/contracts/`). Any column, any direction.
- **Rule 2 — Django apps are column-private.** `registry/` and `queue/`
  are not importable from `tools/`, `agents/`, or `foundation/`. Three
  sanctioned exceptions exist in the whole repo: `models.registry.bindings`,
  importable by a `tools/*` or `agents/*` app and only that module;
  `models.queue.visibility` (IA-1), importable by a `tools/*` or `agents/*`
  app and only that module -- the rows-versus-content visibility answers
  (`visible_jobs`, `visible_rows`, `may_see_job_id`, `may_read_job_content`)
  a poll route needs without ever seeing another principal's payload; and
  `models.queue.models`, importable by `foundation.ops`, and only for the
  active-work refusal (RUNNING jobs), and nothing else. `models.registry.models`
  and `models.registry.views` stay off-limits to every other column, with
  no exception.

  `identity/` is also a Django app, column-private under the same rule --
  **with named seams**: `identity.contracts.*`, `identity.access`
  (posture, admin-ness, `owner_fields`), `identity.request` (this HTTP
  request's principal) and `identity.audit` (write one audit event) are
  the four modules every column may import -- and only those four:
  enforced as an ALLOWLIST, `foundation/ops/tests/
  test_import_law.py::IDENTITY_PERMITTED`, not a blocklist of today's
  known offenders. Every other module under `identity/` -- `identity.
  models`, `identity.views`, `identity.services`, `identity.forms`,
  `identity.middleware`, and any later addition -- is off-limits to
  every other column with no exception and no separate list to update,
  because a module absent from the allowlist is closed by construction.
  See [`identity/README.md`](../identity/README.md) for the column as a
  whole.
- **Rule 3 — cross-column *work* goes through a seam, never an import:**
  the queue (`models.contracts.queue.enqueue`/`get_job`) and the gateway
  (`models.contracts.gateway.get_llm*`/`get_embed_model*`/
  `get_image_generator*`/`get_transcriber*`) are both defined here, and
  are how every other column reaches model-management work without
  importing `registry/` or `queue/` directly.
