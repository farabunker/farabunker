"""Which roles have a model BOUND to them right now -- the ONE signal the
shared shell and the landing page gate their entries on (UI-1 decision 2:
"availability = model bound").

BOUND, NOT REACHABLE. This asks the registry's own tables whether a role
has a `ModelConnection` attached, and nothing else: no engine is resolved,
no health check is run, no endpoint is dialled. That is the owner's
explicit choice for UI-1 -- a nav bar that disappeared whenever a
workstation was asleep would be a worse lie than one that offers a page
which then says, in its own words, that the model is unreachable. Every
page behind these entries already carries that second, honest answer
(`tools/rag/views.py::_precheck_embed_role`,
`agents/chat/views/...::preflight_turn`, `tools/vision`'s 503 body).

THE DB HALF ONLY. `models.contracts.bindings.resolve()` is a db -> env
CHAIN, and a role an operator pinned from the environment
(`LLM_MODEL`/`EMBED_MODEL`) is every bit as bound as one with a
`RoleBinding` row -- the Models console already reports it as such
(`models/registry/views.py`'s unbound-role wording names "no
`RoleBinding`/environment override"). This module deliberately answers
only the DB half, because the DB half is the part that needs a query and
therefore the part that needs a cache; the env half is a settings read
that costs nothing and must never enter the cached set (a test that
overrides `settings.LLM_MODEL` has to take effect on the next render, not
in thirty seconds). `models.registry.context_processors.availability`
unions the two -- that function, not this one, is the whole answer.

WHY A CACHE. `models.registry.context_processors.availability` runs on
EVERY rendered page, including the pages `identity/tests/test_zero_queries
.py` and `identity/tests/test_middleware.py::TestTheSingleRowRead` pin.
Neither pin counts `inference_rolebinding` (they police the identity /
permission tables and the one settings-row read), so a per-request read
here would not turn either red -- but "one more query on every page
forever" is exactly the kind of cost that is invisible until it is
everywhere, and the answer changes only when an operator presses Apply in
the model console. So: read once per process, invalidate on a write, and
expire after `CACHE_TTL_SECONDS` so a SECOND web worker (which never saw
the write) converges by itself rather than serving a stale nav until it
is restarted.

WHY THE CACHE IS BYPASSED INSIDE A TRANSACTION. `bound_role_keys()`
refuses to read from -- or write to -- the process cache while
`connection.in_atomic_block` is true. Two reasons, and both are about
correctness, not about tests:

  * a set read inside an open transaction may be rolled back, and
    promoting it to a process-wide cache would publish a binding that was
    never committed;
  * inside a transaction that has just written a binding, the process
    cache is stale by construction -- the writer's own request would
    render a nav that disagrees with what it just saved.

The convenient consequence is that this module needs no test-only branch
and no `conftest.py` (the repo forbids those anyway): `pytest.mark.
django_db` wraps every test in an atomic block, so ordinary tests always
read live and a value cached by one test can never leak into the next.
`django_db(transaction=True)` -- which runs with no wrapping atomic block
-- is how `models/registry/tests/test_availability.py` exercises the
cached path for real.

Invalidation is registered in `models/registry/apps.py::ready()`, on
commit rather than at save time, so a `RoleBinding` written inside
`transaction.atomic` clears the cache only once the write is actually
durable.
"""
from __future__ import annotations

from time import monotonic

# Thirty seconds. Not a correctness mechanism -- the signal handlers are
# that, for the process that did the writing -- but the convergence
# window for every OTHER process: a second `web` worker, the queue
# `worker`, or the `watcher` never sees `post_save` for a row a different
# process wrote, and would otherwise serve a stale nav until it restarted.
CACHE_TTL_SECONDS = 30.0

# `(value, expires_at)`, or None for "nothing cached".
_CACHE: tuple[frozenset[str], float] | None = None

# Bumped by every `invalidate()`. WITHOUT IT the read-then-store below is
# racy in a way the TTL only bounds rather than prevents: thread A reads a
# pre-write snapshot, the operator's Apply commits and invalidates, and
# then A stores its stale set -- republishing it for a full TTL, and doing
# so AFTER the invalidation that was supposed to clear it. Comparing the
# generation across the read is what makes a store that was invalidated
# mid-flight get dropped instead of winning. (An earlier version of this
# comment claimed the worst case was two threads storing the same answer.
# It was wrong; this is the real one.)
_GENERATION = 0


def bound_role_keys() -> frozenset[str]:
    """Every role key with a `ModelConnection` currently attached,
    lowercased. The DB half of "which roles are live" -- see the module
    docstring for why the environment-pinned half lives in the context
    processor instead.

    LOWERCASED because `RoleBinding.role_key` is CI-unique (see the model)
    and `models.registry.bindings._bound_connection` matches it with
    `iexact`; a caller comparing against the lowercase literals in
    `models.contracts.roles` must get the same answer this module's
    membership test does, whatever casing an old row happens to carry.
    """
    global _CACHE

    from django.db import connection

    if connection.in_atomic_block:
        return _read_bound_role_keys()

    cached = _CACHE
    if cached is not None and cached[1] > monotonic():
        return cached[0]

    # Snapshot the generation BEFORE the read, compare after: an
    # `invalidate()` that landed while this query was in flight makes the
    # value we just read already-stale, and storing it would undo the
    # invalidation. Return it (it is the freshest answer this thread has,
    # and the writer's own request reads live inside its transaction
    # anyway), but do not publish it.
    generation = _GENERATION
    value = _read_bound_role_keys()
    if generation == _GENERATION:
        _CACHE = (value, monotonic() + CACHE_TTL_SECONDS)
    return value


def invalidate() -> None:
    """Drop the cached set; the next `bound_role_keys()` re-reads it.

    Bumps `_GENERATION` too, so a read already in flight cannot store the
    set it fetched before this call -- see that global's own comment.
    """
    global _CACHE, _GENERATION

    _GENERATION += 1
    _CACHE = None


def invalidate_after_commit(sender=None, **kwargs) -> None:
    """Signal receiver: invalidate once the writing transaction commits.

    Connected in `models/registry/apps.py::ready()` to `RoleBinding`'s
    `post_save`/`post_delete` AND to `ModelConnection`'s `post_delete` --
    that third one is not belt-and-braces. `RoleBinding.connection` is
    `on_delete=SET_NULL`, and Django clears it with a BULK UPDATE, which
    emits no `post_save` for the rows it touches; without this receiver,
    deleting the connection that backed every role would leave the cache
    claiming those roles are still bound until the TTL expired.

    Outside a transaction `transaction.on_commit` runs the callback
    immediately, so a write on the plain path invalidates at once.
    """
    from django.db import transaction

    transaction.on_commit(invalidate)


def _read_bound_role_keys() -> frozenset[str]:
    """The one query: a single `values_list("role_key")` over
    `RoleBinding`, filtered to rows that actually have a connection.

    Degrades to the empty set on `DatabaseError` -- the same reason
    `identity.context_processors.identity` swallows it: this runs while
    rendering the nav of EVERY page, and a box mid-`migrate` (no
    `inference_rolebinding` table yet) must still be able to render the
    Install guides page that explains how to finish. "Nothing is bound"
    is also the honest answer there.
    """
    from django.db import DatabaseError

    from models.registry.models import RoleBinding

    try:
        return frozenset(
            key.lower()
            for key in RoleBinding.objects
            .filter(connection__isnull=False)
            .values_list("role_key", flat=True)
        )
    except DatabaseError:
        return frozenset()
