"""C-07 half A. The console re-probed every registered engine over HTTP on
every GET and on every POST -> redirect round trip -- 40 mutating handlers
return `_redirect_console()`, and each 302 lands back in a fresh
`_build_context`. `models/registry/availability.py:92-125` is this repo's
own 30-second process-local TTL pattern; this is that pattern applied to
the probe instead of to a database read.

TestProbeCacheSignalWiring runs `django_db(transaction=True)` on purpose
(matching `test_availability.py::TestTheProcessCache`'s own reasoning): a
real `.save()`/`.delete()` is what actually walks through
`models/registry/apps.py::ready()`'s wiring, so a receiver silently
dropped from that list would turn one of these red rather than being
covered only by calling `probe_cache.invalidate_after_commit` directly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from models.contracts.roles import CHAT_CONVERSE_ROLE
from models.registry import probe_cache
from models.registry.tests._helpers import ENDPOINT, bind, make_chat_connection


def test_a_second_probe_inside_the_ttl_does_not_reach_the_engine():
    probe_cache.invalidate()
    probe = MagicMock(return_value=True)
    assert probe_cache.health(ENDPOINT, probe) is True
    assert probe_cache.health(ENDPOINT, probe) is True
    assert probe.call_count == 1


def test_a_probe_after_the_ttl_expires_reaches_the_engine_again():
    probe_cache.invalidate()
    probe = MagicMock(return_value=True)
    with patch.object(probe_cache, "monotonic", side_effect=[0.0, 0.0, 100.0, 100.0]):
        probe_cache.health(ENDPOINT, probe)
        probe_cache.health(ENDPOINT, probe)
    assert probe.call_count == 2


def test_two_endpoints_do_not_share_an_answer():
    probe_cache.invalidate()
    probe = MagicMock(side_effect=[True, False])
    assert probe_cache.health("http://a:1", probe) is True
    assert probe_cache.health("http://b:2", probe) is False


def test_invalidate_clears_every_entry():
    probe_cache.invalidate()
    probe = MagicMock(return_value=True)
    probe_cache.health(ENDPOINT, probe)
    probe_cache.invalidate()
    probe_cache.health(ENDPOINT, probe)
    assert probe.call_count == 2


def test_a_probe_that_started_before_an_invalidation_does_not_write_its_answer():
    """The generation-snapshot half, copied from `availability.py:121-124`.
    An operator who edits a connection while a probe is in flight must not
    have the pre-edit answer cached over their edit."""
    probe_cache.invalidate()

    def _probe_then_invalidate(_endpoint):
        probe_cache.invalidate()
        return True

    probe_cache.health(ENDPOINT, _probe_then_invalidate)
    second = MagicMock(return_value=False)
    assert probe_cache.health(ENDPOINT, second) is False
    assert second.call_count == 1


def test_cached_shares_the_same_store_and_generation_as_health():
    """`health` is `cached` narrowed to `bool` (Step 6's collapse) -- one
    `invalidate()` must clear both a `health()` key and a plain `cached()`
    key, and a value-typed answer must round-trip untouched (not coerced
    to a bool)."""
    probe_cache.invalidate()
    discover_probe = MagicMock(return_value=["row-a", "row-b"])
    assert probe_cache.cached("discover:x", discover_probe) == ["row-a", "row-b"]
    assert probe_cache.cached("discover:x", discover_probe) == ["row-a", "row-b"]
    assert discover_probe.call_count == 1

    probe_cache.invalidate()
    assert discover_probe.call_count == 1
    probe_cache.cached("discover:x", discover_probe)
    assert discover_probe.call_count == 2


@pytest.mark.django_db(transaction=True)
class TestProbeCacheSignalWiring:
    """The four receivers `models/registry/apps.py::ready()` connects to
    `probe_cache.invalidate_after_commit` -- proven with real signals
    fired by a real `.save()`/`.delete()`, not by calling the module
    function directly, so a receiver dropped from `apps.py` turns one of
    these red the same way it would leave an operator's edit stale."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        probe_cache.invalidate()
        yield
        probe_cache.invalidate()

    def test_saving_a_role_binding_invalidates_it(self):
        probe_cache.health(ENDPOINT, MagicMock(return_value=True))
        bind(CHAT_CONVERSE_ROLE, make_chat_connection())

        probe = MagicMock(return_value=False)
        assert probe_cache.health(ENDPOINT, probe) is False
        assert probe.call_count == 1

    def test_deleting_a_role_binding_invalidates_it(self):
        binding = bind(CHAT_CONVERSE_ROLE, make_chat_connection())
        probe_cache.health(ENDPOINT, MagicMock(return_value=True))

        binding.delete()

        probe = MagicMock(return_value=False)
        assert probe_cache.health(ENDPOINT, probe) is False
        assert probe.call_count == 1

    def test_creating_a_model_connection_invalidates_it(self):
        """The receiver `availability.py` has no use for (creating a
        connection binds no role) but a probe cache needs: registering a
        connection is exactly what `connection_add`/`machine_model_add`
        redirect back into a fresh `_build_context` from."""
        probe_cache.health(ENDPOINT, MagicMock(return_value=True))

        make_chat_connection()

        probe = MagicMock(return_value=False)
        assert probe_cache.health(ENDPOINT, probe) is False
        assert probe.call_count == 1

    def test_deleting_a_model_connection_invalidates_it(self):
        connection = make_chat_connection()
        probe_cache.health(ENDPOINT, MagicMock(return_value=True))

        connection.delete()

        probe = MagicMock(return_value=False)
        assert probe_cache.health(ENDPOINT, probe) is False
        assert probe.call_count == 1
