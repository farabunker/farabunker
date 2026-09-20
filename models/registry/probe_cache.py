"""A 30-second, process-local TTL cache for engine reachability probes.

WHY THIS EXISTS (C-07). `_build_context` probes every registered engine
over HTTP -- `_check_health` per endpoint, plus `discovery.discover` --
and it runs on every console GET *and* on every POST, because
`_redirect_console()` is what forty mutating handlers return and
each 302 lands back in `ConsoleView.get_context_data`. An operator who
edits three roles pays six round trips to a model server for one piece of
work.

THE PATTERN IS NOT NEW HERE: `models/registry/availability.py:92-125`
already runs a 30-second process-local TTL over the role-binding read, with
a generation counter so a read that started before an invalidation cannot
write its stale answer afterwards. This is that mechanism, keyed, over an
HTTP probe instead of a database read.

WHAT IS DELIBERATELY DIFFERENT from `availability.py`:

* KEYED. `availability` caches one value; this caches one per probe key
  (an endpoint, or a discovery argument tuple), because two model servers
  are two independent facts.
* NO `connection.in_atomic_block` GUARD. That guard exists in
  `availability` because a cached database read taken inside a transaction
  can outlive a rollback. An HTTP probe is not a database read; there is
  no transaction whose rollback could make this answer wrong.
* PROCESS-LOCAL, NOT SHARED. Same as `availability`: a worker and a web
  process hold their own, which is correct -- reachability is a property
  of the asking process's own network path.

ONE MECHANISM, NOT TWO. `cached()` is the whole cache -- a key to an
`(answer, expires_at)` pair, generation-guarded the same way
`availability.bound_role_keys()` is. `health()` is `cached()` narrowed to
a `bool` return type, for `_check_health`'s call site; both share the
same `_CACHE` dict and the same `_GENERATION` counter, so one
`invalidate()` (direct, or via a signal through `invalidate_after_commit`)
clears every cached probe answer at once, health or discovery alike.

NOT SHARED WITH `tools/vision`'s equivalent (C-07's other half), and that
is the import law, not an oversight: `models.registry` is column-private
to `tools/*` under rule 2, and vision's cache answers a different question
(one bound generation model's preflight) with its own invalidation events.
Two fifteen-line caches beat one cross-column import.
"""
from __future__ import annotations

from time import monotonic
from typing import Any, Callable

CACHE_TTL_SECONDS = 30.0

# {key: (value, expires_at)}. Process-local; never shared, never
# persisted. `value` is a `bool` for a `health()` key and whatever
# `discover()` returns for a `"discover:..."` key -- one dict, one
# generation counter, one invalidation path for both.
_CACHE: dict[str, tuple[Any, float]] = {}

# Bumped by `invalidate()`. A probe snapshots this before it runs and
# refuses to write its answer if it changed while the probe was in flight
# -- `availability.py:121-124`'s own reasoning, for the same reason.
_GENERATION = 0


def invalidate() -> None:
    """Drop every cached answer. Called directly by tests, and by
    `invalidate_after_commit` from the registry's own post_save/
    post_delete signals, so an operator who edits a connection sees a
    fresh probe on the redirect rather than a 30-second-old one."""
    global _GENERATION

    _GENERATION += 1
    _CACHE.clear()


def invalidate_after_commit(sender=None, **kwargs) -> None:
    """Signal receiver: invalidate once the writing transaction commits.

    Connected in `models/registry/apps.py::ready()` to FOUR signals --
    `RoleBinding`'s `post_save`/`post_delete` AND `ModelConnection`'s
    `post_save`/`post_delete` -- one more than `availability.py`'s own
    three. `availability` needs no `ModelConnection.post_save` because
    creating a connection binds no role; a probe cache does, because
    `connection_add`/`machine_model_add` create a `ModelConnection` and
    redirect straight back into a fresh `_build_context` -- mirroring
    `availability.py`'s receiver set exactly would leave that path
    reading a stale reachability/discovery answer for up to 30 seconds.

    Outside a transaction `transaction.on_commit` runs the callback
    immediately, so a write on the plain path invalidates at once.
    """
    from django.db import transaction

    transaction.on_commit(invalidate)


def cached(key: str, probe: Callable[[str], Any]) -> Any:
    """`probe(key)`'s answer, at most `CACHE_TTL_SECONDS` old.

    `probe` is passed in rather than imported so this module knows nothing
    about `ENGINES` or `discover`, which keeps it testable without a
    Django app registry and keeps each probe's own exception handling
    where it already lives (`_check_health`'s per-engine try/except,
    `_build_context`'s try/except around `discover`).
    """
    global _GENERATION

    now = monotonic()
    entry = _CACHE.get(key)
    if entry is not None and entry[1] > now:
        return entry[0]

    # Snapshot the generation BEFORE the probe, compare after: an
    # `invalidate()` that landed while the probe was in flight makes the
    # answer we just got already-stale, and storing it would undo the
    # invalidation.
    generation = _GENERATION
    value = probe(key)
    if generation == _GENERATION:
        _CACHE[key] = (value, monotonic() + CACHE_TTL_SECONDS)
    return value


def health(key: str, probe: Callable[[str], bool]) -> bool:
    """`cached()`, narrowed to a `bool` -- `_check_health`'s call site."""
    return cached(key, probe)
