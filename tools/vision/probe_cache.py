"""A 30-second, process-local TTL cache for the vision engine's reachability
probe.

WHY THIS EXISTS (C-07, half B). Every chat turn that offers `vision.generate`
narrows its spec: `agents/runtime/loop.py` resolves and calls
`tools.vision.tools.narrowed_generate_spec`, which calls
`services.preflight(resolved=None)`, which health-checks the bound engine
over HTTP (`services._health_check`). One round trip to the generation
server per chat turn, on the turn's critical path.

THE PATTERN IS NOT NEW: `models/registry/probe_cache.py` (C-07's other
half) already runs this exact 30-second process-local TTL shape over the
console's own engine probe. This is that same mechanism, applied here.

THREE THINGS THIS MODULE DIFFERS ON FROM THE REGISTRY'S:

* THE KEY IS `f"{engine}|{endpoint}"`, NOT A BARE ENDPOINT. A vision
  reachability answer is asked about per BOUND engine -- the same endpoint
  string is never reused across two different engine adapters in practice,
  but the key says what is actually being asked ("is THIS engine reachable
  at THIS endpoint") rather than leaning on an assumption that endpoints
  are globally unique.

* IT IS NOT THE REGISTRY'S MODULE, AND CANNOT BE. `models.registry` is
  column-private to `tools/*` under import law rule 2 -- the only
  sanctioned cross-column import out of it is `models.registry.bindings`,
  and this is not that. Importing `models.registry.probe_cache` from
  `tools/vision` would be a rule 2 violation dressed up as a shortcut.
  Two fifteen-line caches beat one cross-column import: do not "fix" this
  into a shared module later.

* WHAT INVALIDATES IT. `models.registry`'s own signals (a `RoleBinding` or
  `ModelConnection` save/delete) are not reachable from this column at
  all, so this cache cannot subscribe to them. It invalidates on the two
  events `tools/vision` CAN see instead: `services.preflight()` being
  called with an explicit `resolved` (an operator picked a model for one
  generation, and that pick deserves a live answer, not a chat turn's
  30-second-old one), and the start of `services.submit_job()` (this
  column's own "run one generation" entry point -- both
  `tools.vision.jobs.run_generate` and `tools.vision.tools.run_generate`
  funnel through it). Absent either event, the 30-second TTL alone is the
  backstop -- and a 30-second-stale "the generation server is up" answer
  on a chat turn is a materially smaller problem than the same staleness
  on the console, because the generation itself still fails honestly a
  moment later either way.
"""
from __future__ import annotations

from time import monotonic
from typing import Any, Callable

CACHE_TTL_SECONDS = 30.0

# {key: (value, expires_at)}. Process-local; never shared, never
# persisted. `key` is `f"{engine}|{endpoint}"`.
_CACHE: dict[str, tuple[Any, float]] = {}

# Bumped by `invalidate()`. A probe snapshots this before it runs and
# refuses to write its answer if it changed while the probe was in flight
# -- the registry's own `probe_cache.py` reasoning, mirrored here.
_GENERATION = 0


def invalidate() -> None:
    """Drop every cached answer. Called directly by tests, and from
    `services.preflight()` (an explicit `resolved` means an operator's
    own per-generation pick) and `services.submit_job()` (the start of
    actually running a generation), so neither reads a stale reachability
    answer for up to 30 seconds."""
    global _GENERATION

    _GENERATION += 1
    _CACHE.clear()


def cached(key: str, probe: Callable[[str], Any]) -> Any:
    """`probe(key)`'s answer, at most `CACHE_TTL_SECONDS` old.

    `probe` is passed in rather than imported so this module knows nothing
    about engines or `is_healthy`, which keeps it testable standalone and
    keeps the probe's own exception handling where it already lives
    (`services._health_check`'s try/except).
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
