"""
The `rag.reencode` job kind (T8, the execution-queue build) -- the SECOND
job kind registered against the queue's kind registry
(`models.contracts.jobkinds`), after `tools.rag.jobs`'s `rag.ask`. Proves
the registry generalizes beyond the RAG tool: this kind is registered
from `models.registry.apps.InferenceConfig.ready()`, not from a
`tools/*` app.

Column-privacy (why this lives in `models/registry`, not in
`tools/rag/jobs.py` alongside `rag.ask`): a re-encode's actual work
(`models.registry.drift.run_rematerialize`, which stamps
`models.registry.models.Materialization` on success) lives in
`models/registry`, and the import law's rule 2 forbids a
`tools/*` app from importing `models/registry` beyond the one
sanctioned seam (`models.registry.bindings`) -- `models.registry.drift`
is not that seam. Rather than either (a) forcing `tools/rag` to reach
past its one allowed import, or (b) moving `run_rematerialize` itself into
`models/contracts/` (it is fundamentally registry-owned: it reads/writes
`models.registry.models.Materialization`, a registry-side table), this
kind is registered from `models/registry` instead. `models.registry` may
import `models.contracts` freely and may import its OWN `drift`/`bindings`
modules without restriction -- only `tools/*` is fenced in. The job-kind
KEY, `rag.reencode`,
still names the RAG role it re-encodes (`models.contracts.roles.
RAG_EMBED_ROLE`) -- a key is just a string, not an import, so this crosses
no boundary at all; only the registering *module* moved.

Payload shape: `{"role_key": str}` -- generic on purpose, even though
`rag.embed` is this kind's only caller today (the console's "Re-encode
now" button, `models/registry/views.py::role_reencode`).
`models.registry.drift.run_rematerialize(role_key)` is already written
generically (it works for any registered role with a `rematerialize`
callback configured, not only `rag.embed`), so this kind rides that same
genericity rather than hard-coding the one role it happens to serve today
-- a future stale-able role (another embeddings-backed index, say) reuses
this exact kind, unmodified, just by naming itself in the payload.

`exclusive=True` (both `plan_reencode`'s return AND the registered
`JobKind`'s posture -- the row's own `exclusive` column, not a scheduler
inference): a re-encode drops and rebuilds the very chunk table
`rag.ask` reads from mid-run (`tools.rag.services.reencode_all`'s
same-dim delete-then-reingest path deletes each document's chunks before
re-ingesting it; its differing-dim path drops the whole table up front) --
concurrent Ask traffic reading a half-rebuilt or momentarily-empty table
would see wrong or missing citations, not a clean "try again" failure. A
re-encode must have the machine to itself for its full duration, which is
exactly what `exclusive=True` buys it (`models/queue/scheduler.py` rule
2a/3/5: a declared-exclusive job blocks all other admissions while
running, and is itself only ever admitted into a genuinely idle machine).
"""
from __future__ import annotations

from models.registry.drift import format_tally, run_rematerialize
from models.contracts.bindings import resolve
from models.contracts.jobkinds import JobContext, ModelRef


def plan_reencode(payload: dict) -> tuple[list[ModelRef], bool]:
    """Resolve the model `payload["role_key"]` needs re-encoded, at ENQUEUE
    time. Uses `models.contracts.bindings.resolve` directly (not
    `models.registry.bindings.resolve_connection*`, `rag.ask`'s planner's
    own seam for a caller-supplied override) -- a re-encode always targets
    the role's OWN durable binding, never a picker-style substitute; there
    is no per-request override for what a role re-encodes against.

    Returns `(model_refs, exclusive)`: one `ModelRef` for the resolved role
    (`footprint_bytes` left `None` -- claim-time code fills that in fresh,
    per `models/queue/scheduler.py`'s provenance contract, matching
    `tools.rag.jobs.plan_ask`) and `exclusive=True` -- see the module
    docstring for why a re-encode must run alone.

    Raises `ValueError` (propagated from `resolve`) for an unbound role --
    a caller bug in production: `models.registry.views.role_reencode`
    only ever enqueues for a role that already resolves (the drift banner
    that triggers this button is itself computed FROM a resolved binding).
    """
    role_key = payload["role_key"]
    resolved = resolve(role_key)

    model_refs = [
        ModelRef(
            role=role_key,
            engine=resolved.engine,
            endpoint=resolved.endpoint,
            model_id=resolved.model_id,
        )
    ]
    return model_refs, True


def run_reencode(payload: dict, models: list[ModelRef], ctx: JobContext) -> dict:  # noqa: ARG001 - handler signature
    """Run a `rag.reencode` job: `models.registry.drift.run_rematerialize`
    for `payload["role_key"]`, returning its tally as this job's stored
    result.

    `models` (the job's claim-time `ModelRef` snapshot) is unused --
    required by the job-kind handler signature
    (`models.contracts.jobkinds.JobKind.handler`), but `run_rematerialize`
    re-resolves the role's binding itself (capturing the fingerprint it
    stamps `Materialization` with fresh, at the moment the callback
    actually runs) rather than trusting an enqueue-time snapshot, matching
    `tools.rag.jobs.run_ask`'s own re-resolve-at-run-time posture.

    `ctx` (T3, `models.contracts.jobkinds.JobContext`) is unused too --
    `run_rematerialize` is one opaque call from this handler's point of
    view (it owns `tools.rag.services.reencode_all`'s own per-document
    loop internally, with no progress/checkpoint callback wired through
    it today); left un-wired, matching this task's default for a handler
    with no genuinely trivial progress source to report from.

    `ValueError` (the role isn't registered, or has no `rematerialize`
    callback configured) and `RuntimeError` (the callback ran but couldn't
    re-encode everything, e.g. `tools.rag.services.reencode_all`'s
    raise-on-partial) both propagate unchanged -- `models.queue.worker.
    Worker._execute` catches a raising handler and stores `str(exc)`
    verbatim as the job row's `error`, the exact same operator-facing copy
    the old synchronous `role_reencode` view used to show as a red
    `messages.error` -- only where it now surfaces (the Queue page's
    Finished section) has changed, not the words themselves.

    Returns:
        `{"summary": str}` -- `models.registry.drift.format_tally`'s
        one-line rendering of `run_rematerialize`'s own return value (e.g.
        `"documents=5, reencoded=5"`), the same tally the old synchronous
        view used to fold into its green message. No model names in this
        copy, matching house style -- `format_tally` only ever echoes the
        rematerialize callback's own count fields.
    """
    result = run_rematerialize(payload["role_key"])
    return {"summary": format_tally(result)}


def summarize_reencode(payload: dict) -> str:
    """One-line, operator-facing row summary for the job queue's listing:
    `"Re-encode {role_key}"` -- no model names (house style: the Queue
    page's listing is meant to be readable without exposing which engine/
    model backs a role), naming only the role being re-encoded."""
    return f"Re-encode {payload.get('role_key', '')}"
