"""
Embedding-drift detection + guarded re-encode orchestration.

A role's data (e.g. a RAG index built with embeddings) can silently go
stale when its binding changes -- an operator repoints `rag.embed` at a
different model/dimension, and the previously encoded data no longer
matches what the role is now configured to produce. This module answers
"has this role drifted?" and, if so, runs the guarded re-encode:

- `active_fingerprint(role)` -- the fingerprint of the role's binding right
  now (`models.contracts.bindings.resolve(role).fingerprint`).
- `is_drifted(role)` -- compares that against
  `models.registry.models.Materialization.fingerprint`, the fingerprint
  the role's data was last materialized against. No `Materialization` row
  means "never materialized" -- treated as NOT drifted (there is nothing
  materialized yet to have drifted from; the console should offer a first
  materialization, not a false drift alarm).
- `run_rematerialize(role)` -- runs the role's `rematerialize` callback
  (`models.contracts.roles.RoleSpec.rematerialize`, a dotted-path string
  resolved lazily via `import_string`, matching
  `models.contracts.bindings.resolve()`'s own lazy-import convention) and,
  only if the callback returns without raising, upserts `Materialization`
  with the fingerprint captured *before* the callback ran -- the binding
  the data was actually encoded against. A failed rematerialize propagates
  its exception and leaves `Materialization` untouched, so the drift banner
  stays up instead of being cleared by a run that didn't complete. Returns
  the callback's own return value unchanged (e.g. a reencoded-document
  count) so callers can report on what happened.
- `format_tally(result)` -- renders that return value as a one-line tally
  string (`"added=3, skipped=1"` for a dict, `str(result)` otherwise) for
  the console view and the `reencode` management command to report.

A role with no `rematerialize` configured (or not registered at all) can't
be rematerialized -- `run_rematerialize` raises `ValueError` rather than
silently no-op'ing.
"""
from __future__ import annotations

from django.utils.module_loading import import_string

from models.registry.models import Materialization
from models.contracts.bindings import resolve
from models.contracts.roles import get_role


def format_tally(result) -> str:
    """Render a rematerialize callback's return value as a one-line tally.

    A dict result (e.g. `{"added": 3, "skipped": 1}`) becomes
    `"added=3, skipped=1"`, in the dict's own order; anything else is just
    `str(result)`. Shared by the console's `role_reencode` view and the
    `reencode` management command so both report a run's outcome in
    exactly the same shape instead of each hand-rolling the same join.
    """
    if isinstance(result, dict):
        return ", ".join(f"{key}={value}" for key, value in result.items())
    return str(result)


def active_fingerprint(role: str) -> str:
    """Return the fingerprint of `role`'s currently resolved binding."""
    return resolve(role).fingerprint


def is_drifted(role: str) -> bool:
    """True if `role`'s active fingerprint differs from its last materialization.

    No `Materialization` row for `role` means "never materialized", which
    is treated as NOT drifted -- see module docstring.
    """
    try:
        materialization = Materialization.objects.get(role_key=role)
    except Materialization.DoesNotExist:
        return False

    return active_fingerprint(role) != materialization.fingerprint


def run_rematerialize(role: str):
    """Run `role`'s rematerialize callback and stamp `Materialization`.

    Looks up `role`'s `RoleSpec`, captures the role's active fingerprint
    *before* running anything (the data is encoded under the binding
    active when the run starts -- a rebind racing the rematerialize must
    not get credited with data encoded under the old binding), imports the
    `rematerialize` dotted path fresh (not cached at module level,
    matching `models.contracts.bindings.resolve()`'s convention), and calls
    it with no arguments. Only if the callback returns without raising is
    `Materialization(role_key=role)` upserted -- with that pre-captured
    fingerprint. A raising callback propagates and leaves
    `Materialization` untouched, so `is_drifted()` keeps reporting drift
    for a run that didn't complete.

    Returns:
        Whatever the rematerialize callback returns (e.g. a count of
        documents re-encoded).

    Raises:
        ValueError: `role` is not a registered role, or is registered
            without a `rematerialize` callback configured.
        Exception: whatever the callback itself raises (e.g.
            `tools.rag.services.reencode_all`'s `RuntimeError` on a
            partial re-encode) -- propagated unchanged, without stamping.
    """
    spec = get_role(role)
    if spec is None or not spec.rematerialize:
        raise ValueError(f"Role {role!r} has no rematerialize callback configured")

    fingerprint = active_fingerprint(role)
    callback = import_string(spec.rematerialize)
    result = callback()

    Materialization.objects.update_or_create(
        role_key=role, defaults={"fingerprint": fingerprint}
    )

    return result
