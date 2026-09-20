"""Unit tests for `models/contracts/testing.py`'s `registry_reset_fixture`
(C-59) -- the snapshot/clear/restore factory shared by every registry
dict this column owns (`jobkinds._JOB_KINDS`, `roles._ROLES`) and by
every consumer app that resets one of them between tests.

MOVED FROM `models/registry/tests/test_jobkinds.py` (C-58). Task 34
parked these there because `models/contracts/tests/` did not exist yet;
that file's own comment said Task 36 would move them once it did. No DB,
no Django models -- pure in-memory registry mechanics.

`_FakeRegistryModule` below is a throwaway stand-in, not `jobkinds` or
`roles`: `registry_reset_fixture` only ever calls `getattr(module,
attribute)` and dict methods on the result, so any object with one dict
attribute proves the factory as well as a real registry would.

ISOLATION TRICK. `registry_reset_fixture` always returns an
`autouse=True` fixture (matching every real call site -- the point of
this whole factory is that autouse-ness). Binding one at MODULE scope
would make it fire around EVERY test in this file, including the ones
below meant to observe what its teardown left behind WITHOUT
re-triggering its setup. Assigning it as a `staticmethod` class
attribute instead scopes it to that class's own tests only (pytest
resolves autouse by the scope a fixture is DEFINED in, same as an
ordinary fixture) -- so a sibling test/class, not itself in that
class, is untouched by it and sees exactly what that class's one test's
teardown left behind.

ORDERING. The four tests below rely on pytest's default file-order
collection (no randomization plugin is installed -- see pytest.ini):
each "observer" test/class is defined immediately after the producer
class whose teardown it inspects.
"""
from __future__ import annotations

from models.contracts import jobkinds, roles
from models.contracts.testing import registry_reset_fixture

reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS")


class _FakeRegistryModule:
    """Stands in for a real module -- one dict attribute is all
    `registry_reset_fixture` needs."""

    def __init__(self, **seed):
        self._REGISTRY = dict(seed)


_seed_content = {"seed": "present"}


class TestFixtureEmptiesTheRegistry:
    """`_reset` is autouse only within this class (see the isolation
    trick above) -- its one test proves the registry is empty DURING the
    fixture's window."""

    _fake_module = _FakeRegistryModule(**_seed_content)
    _reset = staticmethod(registry_reset_fixture(_fake_module, "_REGISTRY"))

    def test_the_fixture_empties_the_registry_for_the_test(self):
        assert self._fake_module._REGISTRY == {}


def test_the_fixture_restores_exactly_what_was_there():
    """Runs immediately after `TestFixtureEmptiesTheRegistry`'s one test
    and is not itself inside that class, so its `_reset` fixture does not
    apply here. That test never wrote to the registry, so finding the
    original seed content here -- not an empty dict -- pins that
    teardown restores it, unchanged."""
    assert TestFixtureEmptiesTheRegistry._fake_module._REGISTRY == _seed_content


class TestRegistersSomethingMidTest:
    """Same isolation trick, over its own fake module -- independent of
    `TestFixtureEmptiesTheRegistry`'s so the two pairs can't interfere."""

    _fake_module = _FakeRegistryModule(**_seed_content)
    _reset = staticmethod(registry_reset_fixture(_fake_module, "_REGISTRY"))

    def test_a_test_that_registers_something_does_not_leak_it(self):
        self._fake_module._REGISTRY["added_mid_test"] = "should not survive"


class TestWorksOverEitherRegistryDict:
    """Runs immediately after `TestRegistersSomethingMidTest`'s one test
    and is not itself in that class, so it observes exactly what that
    test's teardown left behind: the original seed content, with no
    trace of the mid-test addition -- proving a test that registers
    something does not leak it into the next.

    It also exercises the factory over TWO real, different registries at
    once: `reset_registry` (this file's own module-level autouse
    fixture) is the same factory built over `jobkinds._JOB_KINDS`;
    `_reset_roles` here, built the same way over `roles._ROLES`, proves
    the factory works identically over a completely different (module,
    attribute) pair.
    """

    _reset_roles = staticmethod(registry_reset_fixture(roles, "_ROLES"))

    def test_it_works_over_either_registry_dict(self, reset_registry):
        assert TestRegistersSomethingMidTest._fake_module._REGISTRY == _seed_content
        assert jobkinds._JOB_KINDS == {}
        assert roles._ROLES == {}
