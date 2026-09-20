"""C-07 half B. `narrowed_generate_spec` runs on every chat turn that
offers `vision.generate`, and its preflight paid one HTTP round trip to
the generation server each time -- on the turn's critical path.
`models/registry/probe_cache.py` (C-07's other half) already runs a
30-second process-local TTL over the console's own engine probe; this is
that same mechanism, applied here, keyed on `f"{engine}|{endpoint}"`
pairs rather than a bare endpoint.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.vision import probe_cache

ENGINE_KEY = "stubengine|http://stub:9999"


def test_a_second_probe_inside_the_ttl_does_not_reach_the_engine():
    probe_cache.invalidate()
    probe = MagicMock(return_value=True)
    assert probe_cache.cached(ENGINE_KEY, probe) is True
    assert probe_cache.cached(ENGINE_KEY, probe) is True
    assert probe.call_count == 1


def test_a_probe_after_the_ttl_expires_reaches_the_engine_again():
    probe_cache.invalidate()
    probe = MagicMock(return_value=True)
    with patch.object(probe_cache, "monotonic", side_effect=[0.0, 0.0, 100.0, 100.0]):
        probe_cache.cached(ENGINE_KEY, probe)
        probe_cache.cached(ENGINE_KEY, probe)
    assert probe.call_count == 2


def test_two_engine_endpoint_pairs_do_not_share_an_answer():
    probe_cache.invalidate()
    probe = MagicMock(side_effect=[True, False])
    assert probe_cache.cached("stubengine|http://a:1", probe) is True
    assert probe_cache.cached("comfyui|http://b:2", probe) is False


def test_invalidate_clears_every_entry():
    probe_cache.invalidate()
    probe = MagicMock(return_value=True)
    probe_cache.cached(ENGINE_KEY, probe)
    probe_cache.invalidate()
    probe_cache.cached(ENGINE_KEY, probe)
    assert probe.call_count == 2


def test_a_probe_that_started_before_an_invalidation_does_not_write_its_answer():
    """The generation-snapshot half, copied from the registry's own
    `probe_cache.py`. An operator who changes the bound engine while a
    probe is in flight must not have the pre-change answer cached over
    the change."""
    probe_cache.invalidate()

    def _probe_then_invalidate(_key):
        probe_cache.invalidate()
        return True

    probe_cache.cached(ENGINE_KEY, _probe_then_invalidate)
    second = MagicMock(return_value=False)
    assert probe_cache.cached(ENGINE_KEY, second) is False
    assert second.call_count == 1


def test_a_cached_down_answer_also_expires_after_the_ttl():
    """A cached DOWN must expire like a cached UP -- the preflight must
    keep refusing honestly once the TTL has elapsed, not freeze a stale
    "unreachable" answer past the window either."""
    probe_cache.invalidate()
    probe = MagicMock(return_value=False)
    with patch.object(probe_cache, "monotonic", side_effect=[0.0, 0.0, 100.0, 100.0]):
        assert probe_cache.cached(ENGINE_KEY, probe) is False
        assert probe_cache.cached(ENGINE_KEY, probe) is False
    assert probe.call_count == 2
