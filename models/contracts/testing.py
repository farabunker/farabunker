"""Shared test scaffolding for the `models` column's registries.

**Production code must never import this module.** It mirrors
`identity/testing.py`'s pattern -- a plain importable module, not a
`conftest.py` (this repo forbids those anywhere) -- and each package's own
`tests/_helpers.py` re-exports what it uses from here.

WHY HERE AND NOT `identity/testing.py` (C-59). The audit named that module
as the sanctioned shared home, and for `make_user`/`posture`/`grant` it
is. But its own docstring records that identity imports no other column,
and both registries this file resets live in `models.contracts` -- putting
a `models.contracts`-scoped helper there would invert the dependency for
the sake of reusing a filename. `models.contracts` is a pure leaf under
the import law's rule 1, so this module is importable from anywhere that
needs it.

WHY A FACTORY AND NOT A FIXTURE. Five test modules carried a
snapshot-clear-yield-restore fixture: four over `jobkinds._JOB_KINDS` and
one over `roles._ROLES`. The bodies are identical in shape and differ in
which dict they hold, so what is shared is the mechanism, not the fixture.
"""
from __future__ import annotations

import pytest


def registry_reset_fixture(module, attribute: str, *, autouse: bool = True):
    """A pytest fixture that empties `module.<attribute>` for the
    duration of a test and restores exactly what was there before.

    Use it as::

        reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS")

    at module level in a test file -- the name it is bound to is the
    fixture's name, the way `pytest.fixture` always works.

    RESTORE, NOT JUST CLEAR: these registries are populated at app-ready
    time, so a test that emptied one and did not put it back would leave
    every later test in the run looking at an empty registry -- which is
    the class of order-dependent failure `docs/DEV.md`'s
    reversed-collection-order run exists to catch.

    `autouse` defaults `True` (every existing call site passes only the
    first two, positional, arguments, so their behaviour is unchanged).
    Pass `autouse=False` for a module where only SOME tests want the
    registry emptied and the rest must keep seeing the real app-ready
    registrations -- `models/queue/tests/test_claim.py` is that case:
    most of its tests exercise `claim_and_admit` against real,
    production-registered job kinds (e.g. `rag.ingest`'s `on_terminal`
    hook), while its `TestKindAwareStaleness` tests want a clean registry
    to register their own fake kinds into. With `autouse=False`, only a
    test that names `reset_registry` as an explicit parameter gets the
    clear/restore; every other test in the module sees the registry
    exactly as app startup left it.
    """
    @pytest.fixture(autouse=autouse)
    def _reset():
        registry = getattr(module, attribute)
        original = dict(registry)
        registry.clear()
        yield
        registry.clear()
        registry.update(original)
    return _reset
