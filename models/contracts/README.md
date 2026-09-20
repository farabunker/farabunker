# models/contracts/ — the platform contracts

The stable model-management contracts every module builds against: roles
and operations (`roles.py`, `operations.py`), job kinds (`jobkinds.py`),
the queue seam (`queue.py`), the registry bindings (`bindings.py`), the
inference gateway (`gateway.py`), the model catalog (`catalog.py`), and
the per-engine adapters (`engines/`).

Planned services (see [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#4-the-core-services)):

- **Inference Gateway** — OpenAI-compatible chat + embeddings; model routing
- **Vector Store** — embedding storage + similarity search
- **Storage** — objects, documents, module state
- **Identity & Auth** — local users, roles, per-module capability grants
- **Service Bus / Registry** — module discovery, routing, events
- **Config & Secrets** — posture profiles, model registry (realized in
  [`models/registry/`](../registry/README.md), [ADR 0010](../../docs/adr/0010-model-management-framework.md)), local secrets vault
- **Airlock** — verify & ingest signed update packages
- **Observability** — local-only logs, metrics, audit trail

The rule-1 pure leaves that used to sit beside these contracts —
`format.py` and `files.py` — now live in
[`../../foundation/README.md`](../../foundation/README.md).

## A client request never outranks queue policy (C-4)

`queue.py::resolve_client_priority(requested)` is the one place a caller's own request body
decides an `enqueue()` call's `priority` — nowhere. `models.queue.backend._resolve_priority`
treats an explicit `priority` as the TOP rung of its chain, above the job kind's own default
and above the operator's queue-wide `JobSettings.default_priority` — so a caller that may set
one at all can outrank every other job on the machine, and priority is otherwise something an
administrator sets on an admin-class page (the Queue page's settings form), never a property
of one request. `requested` is parsed/range-checked by the caller first (so a malformed value
is still an honest 400) and then handed to this function, which always returns `None` — NO
CALLER-CHOSEN PRIORITY SURVIVES, not an anonymous caller's, not a member's, not an
administrator's; an administrator who wants a different priority uses the settings form
instead, which changes the job kind's default for every job of that kind, audited, rather
than one request's own say-so. `requested` is accepted and otherwise unused only so a call
site reads as "the request's value goes in, the resolved value comes out", and so a future
policy that genuinely does vary by the acting principal's posture, role, or route has one
seam to change rather than every call site's signature; nothing in this tree varies a job's
priority that way yet, so today the resolution is a constant. `tools.rag.views.AskView` is
this function's first, and so far only, caller.

## Engine responses are read against a byte budget (S11, S25)

`engines/engine_http.py` is the one home for two things every adapter in `engines/` needs and
must not each reinvent: `get_bounded`, a `GET` that reads a response through `httpx.stream(...)`,
raising `httpx.HTTPError` the moment the running total of bytes passes a budget instead of
materialising the whole body first and complaining about its size after (`truncate=True` instead
returns the first `max_bytes` read, no exception, for a caller that only wants a bounded PREFIX of
a body it doesn't control the length of — the one health-probe marker sniff below uses it; every
other caller keeps the default, raising, behaviour); and `safe_engine_ref`, which refuses (with
`ValueError`) an engine-supplied identifier that is not a bare token before it is ever interpolated
into a request path.

**Exactly three reads go through `get_bounded` today** — `whisper.py`'s health probe
(`truncate=True`, capped at the small `HEALTH_MARKER_SEARCH_BYTES` a marker sniff actually needs),
and `comfyui.py`'s `fetch_outputs` (`/view`, also capped per-call at `MAX_OUTPUTS_PER_JOB` output
images per history entry) and `_history` (`/history/<id>`, whose `prompt_id` is additionally
validated through `safe_engine_ref` before it reaches the request path). A hostile or merely
misbehaving engine reading through one of these three — including one reached through an
SSRF-adjacent endpoint override — reads as unreachable (or, for the health probe, is sniffed on a
bounded prefix rather than refused) rather than as unbounded memory pressure inside the worker
process; `_history`'s guard also means it cannot redirect that request at a different path on
itself by shaping the `prompt_id` it hands back. **Every other engine response read in this
package is not yet bounded** — closing the rest is tracked as a follow-up (H11b), not silently
assumed complete.

`models.contracts.testing` (test support, `models/contracts/testing.py`)
mirrors `identity/testing.py`'s pattern: a plain importable module, never
a `conftest.py`, that production code must never import. It holds
`registry_reset_fixture`, a factory for the snapshot/clear/restore
autouse fixture that resets one of this package's own module-level
registry dicts (`jobkinds._JOB_KINDS`, `roles._ROLES`) for the duration
of a test — used by `models/queue/tests/` and `models/registry/tests/`,
each re-exporting it from their own `tests/_helpers.py`.

## `tests/` — this package's own unit tests (C-58)

Pure, DB-free coverage of the contract types and pure functions above:
no Django models, no HTTP, no `pytest.mark.django_db`. Every one of these
modules lived in a consumer column's `tests/` directory until it moved
here — `models.contracts` is a rule-1 pure leaf, universally importable,
so the next consumer of one of its modules should find its tests beside
it rather than in whichever column happened to be the first consumer.

- `test_engines_base.py` — the additive engine-contract types
  (`models/contracts/engines/base.py`): image generation and setup
  (`Asset`, `JobStatus`, `SetupGuide`, `ImageGenerator`) on one half,
  transcription (`TranscriptSegment`, `TranscriptResult`, `Transcriber`)
  on the other.
- `test_operations.py` — `models/contracts/operations.py`'s param schema
  and validation (spec §4.1).
- `test_setup_guides.py` — the engine-declared setup guides every
  registered adapter's `setup_guide` must satisfy.
- `test_comfyui_workflows.py` — the ComfyUI graph templates
  (`models/contracts/engines/comfyui_workflows/`, spec §4.4).
- `test_testing.py` — this module's own `registry_reset_fixture` (C-59).

`foundation/ops/tests/test_column_boundaries.py::
test_a_contract_module_keeps_its_tests_beside_itself` pins this
arrangement: a `tools/vision` or `tools/rag` test module that imports
only `models.contracts` (and never its own column) fails the gate.
