"""The registry that lets an entitlement delete reach two other columns.

`identity/` may not import `agents/` or `tools/` (import-law rule 4),
and deleting an entitlement must remove its document labels and its
tool labels AND repair the chunk-metadata cache. The columns register a
DOTTED-PATH STRING at `AppConfig.ready()`, exactly as job kinds, roles,
tools and `OwnedRows` already do, and this module resolves it at delete
time.
"""
from __future__ import annotations

import pytest

from identity.contracts import cascades as cascades_module
from identity.contracts.cascades import (
    EntitlementCascade, all_entitlement_cascades, register_entitlement_cascade,
)

_CALLS: list[tuple[int, bool]] = []


@pytest.fixture(autouse=True)
def _isolated_registry():
    """THE REGISTRY IS A MODULE-LEVEL DICT with no reset path, exactly
    like `identity.contracts.ownership._OWNED`, so every test that
    registers one needs this -- matching how every other registry in this
    codebase is isolated in tests (`identity/tests/test_ownership.py:14`,
    `agents/tests/_helpers.py::isolated_tool_registry`).

    Without it, `test.broken` below -- whose handler names a function
    that does not exist -- survives this module and makes every later
    `run_cascades()`/`cascade_counts()` in the same pytest process raise
    `ImportError`: `identity/tests/test_entitlement_services.py`'s delete
    test, `identity/tests/test_entitlement_pages.py`'s
    delete-confirmation test, and the whole owner column of the route
    matrix.
    """
    saved = dict(cascades_module._CASCADES)
    cascades_module._CASCADES.clear()
    yield
    cascades_module._CASCADES.clear()
    cascades_module._CASCADES.update(saved)


def _fake_handler(entitlement_id: int, *, commit: bool) -> int:
    _CALLS.append((entitlement_id, commit))
    return 3


class TestTheRegistryIsPure:
    def test_a_cascade_needs_a_key_a_label_and_a_dotted_handler(self):
        with pytest.raises(ValueError):
            EntitlementCascade("", "Labels", "identity.tests.test_cascades._fake_handler")
        with pytest.raises(ValueError):
            EntitlementCascade("rag.labels", "", "identity.tests.test_cascades._fake_handler")
        with pytest.raises(ValueError):
            EntitlementCascade("rag.labels", "Labels", "not_dotted")

    def test_registration_is_idempotent(self):
        """The same shape every sibling registry has, so re-importing a
        module that registers at import time is safe."""
        before = len(all_entitlement_cascades())
        spec = EntitlementCascade("test.twice", "Twice",
                                  "identity.tests.test_cascades._fake_handler")
        register_entitlement_cascade(spec)
        register_entitlement_cascade(spec)
        assert len(all_entitlement_cascades()) == before + 1

    # The anti-vacuous pin that this registry is not silently empty --
    # `{"rag.document_labels", "agents.tool_labels"} <= keys` -- lands in
    # Task 10, WITH the second of the two registrations it asserts (the
    # rag one, whose handler module Task 10 creates). Written here it
    # would be a red test committed on a green branch, which is the one
    # thing a suite gate cannot tolerate.


class TestRunningThem:
    def test_counting_never_commits_and_running_does(self):
        from identity.cascades import cascade_counts, run_cascades
        register_entitlement_cascade(EntitlementCascade(
            "test.counted", "Counted", "identity.tests.test_cascades._fake_handler"))
        _CALLS.clear()
        counts = cascade_counts(7)
        assert counts["Counted"] == 3
        assert (7, False) in _CALLS
        _CALLS.clear()
        assert run_cascades(7)["Counted"] == 3
        assert (7, True) in _CALLS

    def test_an_unresolvable_handler_raises_rather_than_being_skipped(self):
        """A cascade that silently did nothing would leave orphan labels
        and a stale chunk cache behind a delete that reported success --
        the one failure mode this registry exists to prevent."""
        from identity.cascades import run_cascades
        register_entitlement_cascade(EntitlementCascade(
            "test.broken", "Broken", "identity.tests.test_cascades.does_not_exist"))
        with pytest.raises(ImportError):
            run_cascades(7)


# Captured at IMPORT time, before `_isolated_registry` clears anything:
# Django has already run every `AppConfig.ready()` by the time this
# module is imported, so this is what the three columns really registered.
#
# NOT by calling `ready()` again from inside the test. Those methods are
# not narrow: `tools/rag/apps.py::ready()` also registers two-to-four
# roles, two job kinds, three tools and an `OwnedRows`, and
# `agents/apps.py::ready()` a role, a job kind, every agent-as-tool spec
# and three `OwnedRows`. Re-running them would mutate four module-level
# registries this file protects none of -- reintroducing exactly the
# class of cross-test bleed `_isolated_registry` above exists to stop,
# and the reason the suite is run in two collection orders.
_REGISTERED_AT_IMPORT = frozenset(spec.key for spec in all_entitlement_cascades())


class TestBothColumnsRegister:
    def test_the_two_real_cascades_are_registered_by_their_columns(self):
        """Anti-vacuous pin: this registry is not silently empty, and the
        four keys are the ones the columns actually register at
        `AppConfig.ready()` -- `inference.model_sets` (IA-2 T14) and
        `agents.runnable_labels` (IA-2 T15) joined the original two."""
        assert {"rag.document_labels", "agents.tool_labels",
                "inference.model_sets", "agents.runnable_labels"} <= _REGISTERED_AT_IMPORT
