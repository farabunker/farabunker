"""
Queue dispatch seam.

Feature apps enqueue work through this module and never import
`models.queue` directly -- the actual queue (Postgres-backed, memory-aware,
priority-ordered; ADR 0013) lives in the `models/` column and is reached
only via `settings.INFERENCE_QUEUE_BACKEND`, a dotted path to a module
exposing `enqueue(kind, payload, *, priority=None)` and `get_job(job_id)`.
That module is imported fresh on every call (no caching at module level) --
the same contract as `models.contracts.bindings.resolve()`'s provider
dispatch -- so a settings change (including an `override_settings` in
tests) takes effect immediately, with no stale import to invalidate.

An unset or blank `INFERENCE_QUEUE_BACKEND` raises a clear error naming the
setting rather than silently no-op'ing or guessing a default backend --
there is no bindings-style pure fallback the way `bindings.resolve()` has
`env_provider`, because there is nothing pure a job can run against; a job
kind's `handler` is worker-side by construction.

No `cancel()` here: cancellation is console-internal (an operator action
against a stored `Job` row), not a seam a feature app needs.

`QueueUnavailable` is defined HERE, not in `models.queue.backend`, even
though the failure it names (the jobs tables missing or unreachable -- an
unmigrated window, or the DB itself down) is entirely a `models.queue.
backend` concern -- see that module's docstring for the two failure
domains. A `tools/*` caller (e.g. `tools.rag.views.AskView`) needs
to catch this specific exception to render its own honest "run migrations"
503, but import law rule 2 forbids a `tools/*` app from importing
`models.registry`/`models.queue` directly (`models.registry.bindings` is
the one sanctioned exception, per every `tools/rag` docstring that
mentions this). Defining the exception here and having `models.queue.
backend` import (not redefine) it resolves that cleanly: `models.contracts`
-> `models.queue` imports are always legal (`models.queue` already depends
on `models.contracts` throughout), so `models.queue.backend` importing
`QueueUnavailable` from here is an ordinary downward import, while every
`tools/*` caller catches `models.contracts.queue.QueueUnavailable` without
ever reaching into `models.queue` at all -- the backend module raises this
exact class, so no translation/wrapping happens at this seam's `enqueue`/
`get_job` below; they are plain passthroughs.
"""
from __future__ import annotations

from importlib import import_module

from django.conf import settings

from models.contracts.jobkinds import get_job_kind


# The job-state vocabulary, mirrored here from `models.queue.models`
# (`QUEUED`/`RUNNING`/`SUCCEEDED`/`FAILED`/`CANCELLED`, `models/queue/
# models.py:15-19`) so a caller outside the `models/` column -- e.g. an
# `agents/management/` command polling `get_job` -- has something to
# compare a `JobStatus.state` against WITHOUT importing `models.queue.*`
# (cross-column; `agents/` may only reach this seam, not the backend it
# dispatches to). This module stays a pure leaf: it does NOT import
# `models.queue.models` and re-export its constants, because `models.
# queue` imports `models.contracts` (never the other way -- see module
# docstring above); these five literals are declared fresh here and
# PINNED equal to the backend's own by `models/queue/tests/test_queue_
# vocabulary_pin.py`, so the two can never quietly drift apart.
#
# A caller must never spell a state literal itself: "done" (a state this
# vocabulary has never had) shipped in `agents/management/commands/
# agent_turn.py`'s poll loop and its own test fixtures for exactly that
# reason -- the fixtures agreed with the command's typo instead of with
# the queue's real vocabulary, and nothing caught it until production.
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

# A job in any of these states is done and will never be claimed, run, or
# cancelled again -- same meaning as `models.queue.models.TERMINAL_STATES`,
# pinned equal to it by the same test named above. A `frozenset`, not the
# backend's `tuple`: this seam's copy is compared against with `in` only,
# never iterated in a guaranteed order.
TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, CANCELLED})


class QueueUnavailable(Exception):
    """The jobs tables are missing or unreachable (an unmigrated window, or
    the DB itself is down). Raised by `models.queue.backend` (which imports
    this class rather than defining its own -- see module docstring above)
    and passed straight through by `enqueue`/`get_job` below, uncaught and
    untranslated, so callers here always see this exact type regardless of
    which backend module is configured."""


class QueueQuotaExceeded(Exception):
    """This payload's own actor already holds `JobSettings.
    max_queued_per_principal` QUEUED-or-RUNNING jobs (C-7, round-3
    hardening). Raised by `models.queue.backend.enqueue` -- imported here,
    not redefined, for the identical reason `QueueUnavailable` lives here
    and not in `backend.py` (see module docstring above): a `tools/*`
    caller may catch it without ever importing `models.queue` directly.

    DELIBERATELY NOT A `QueueUnavailable` -- the two mean different facts
    with different operator advice. `QueueUnavailable` says "the queue
    tables cannot be reached, run migrations"; this says "the queue is
    working fine and this caller has simply asked for too much of it,
    wait for one of your own jobs to finish". Conflating them would send
    an operator chasing a migration that was never the problem, and
    **fifteen** `except QueueUnavailable` sites across three columns
    (`tools/vision/views.py`, `tools/rag/`, `agents/chat/`, and others in
    `models/`) already branch on that exact type -- this class is
    NEVER a subclass of it, so none of those sites catches this by
    accident."""


def _backend():
    """Resolve `settings.INFERENCE_QUEUE_BACKEND` fresh, or raise clearly.

    The setting names a *module* (`enqueue`/`get_job` live at module scope
    on it), not a single callable, so this uses `importlib.import_module`
    rather than `django.utils.module_loading.import_string` (which expects
    a dotted path ending in the attribute name) -- the same fresh-per-call,
    not-cached-at-module-level contract as `models.contracts.bindings.
    resolve()`'s provider dispatch still applies: every call re-imports.
    """
    path = settings.INFERENCE_QUEUE_BACKEND
    if not path:
        raise ValueError(
            "No queue backend configured (settings.INFERENCE_QUEUE_BACKEND is unset)"
        )
    return import_module(path)


def enqueue(kind: str, payload: dict, *, priority: int | None = None) -> int:
    """Enqueue `payload` under job kind `kind`, returning the new job id.

    Validates `kind` is a registered `JobKind` (raises `ValueError` naming
    it, via `get_job_kind`, before the backend is even imported) then
    dispatches to the configured backend's `enqueue` with the same
    signature. `priority` of `None` is passed straight through -- the
    backend (or the job kind's own `default_priority`) decides what that
    means, not this seam.
    """
    get_job_kind(kind)
    backend = _backend()
    return backend.enqueue(kind, payload, priority=priority)


def get_job(job_id: int):
    """Return the backend's job-status object for `job_id`.

    A thin passthrough to the configured backend's `get_job` -- `core`
    neither defines nor constrains the shape of what comes back (a
    `JobStatus`-like object defined console-side); it is the backend's
    return value, unchanged.
    """
    backend = _backend()
    return backend.get_job(job_id)


def resolve_client_priority(requested: int | None) -> int | None:
    """The one place a caller's own request decides an `enqueue()` call's
    `priority` (C-4) -- which is nowhere. Always returns `None`.

    Priority is QUEUE POLICY: which job runs before which other job on
    this box. `models.queue.backend._resolve_priority` already treats an
    explicit `priority` argument as the TOP rung of its chain, above the
    job kind's own default and above the operator's own queue-wide
    `JobSettings.default_priority` -- so a caller that may supply one at
    all can outrank every other job on the machine. Setting queue policy
    is otherwise something an administrator does on an admin-class page
    (the Queue page's settings form, `JobSettings`); a value typed into
    an ordinary request body is not that, no matter who typed it or how
    the field parsed -- NO CALLER-CHOSEN PRIORITY SURVIVES, not an
    anonymous caller's, not a member's, not an administrator's. An
    earlier version of this function took an `is_admin` bool and let an
    administrator's value through; review round 1 reversed that: an
    administrator who wants to change how `rag.ask` jobs are scheduled
    already has the tool for it -- the Queue page's own settings form,
    which is audited and applies to every job of that kind, not to one
    request an administrator happened to be looking at.

    `requested` has already been parsed and range-checked by the caller
    (a non-integer or non-positive value is the caller's own 400, raised
    before this function is ever reached) -- it is accepted here, and
    otherwise unused, only so a call site reads as "the request's value
    goes in, the resolved value comes out" and so a future policy change
    has a value to work from without every call site's signature
    changing again. Today that resolution is a CONSTANT (`None`, which
    leaves the job kind's own default -- and the operator's own
    queue-wide default -- in charge, exactly as every other `enqueue()`
    call site in the tree that never forwards a client value already
    behaves): nothing in this tree yet varies a job's priority by the
    acting principal's posture, role, or the route it came in on, so
    there is nothing for this function to derive from yet. The name and
    the seam exist so that WHEN something does, it changes in this one
    place -- never by a caller reaching for the request body again.
    """
    del requested  # accepted for the seam's shape; never consulted (see docstring)
    return None
