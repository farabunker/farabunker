"""Unit tests for `models.contracts.queue.resolve_client_priority` (C-4) --
the one place a caller's own request body decides an `enqueue()` call's
`priority`: nowhere. Review round 1 removed the earlier `is_admin` gate
(an administrator's request-body value passed through) -- NO
caller-chosen priority survives, whoever the caller is.

Pure, DB-free (C-58: a module testing only `models.contracts.*` lives
beside it, not in whichever column happened to be the first consumer --
`tools.rag.views.AskView` is that first consumer, and its own
`tools/rag/tests/test_views_ask_priority.py` covers the HTTP-level gate;
this module covers the policy function itself, in isolation from the
view, the queue backend, and the database).
"""
from __future__ import annotations

import pytest

from models.contracts.queue import resolve_client_priority


class TestResolveClientPriority:
    @pytest.mark.parametrize("requested", [1, 5, 100, -1, 0])
    def test_no_requested_value_ever_survives_whatever_it_is(self, requested):
        assert resolve_client_priority(requested) is None

    def test_an_absent_priority_resolves_to_none_too(self):
        """`None` in, `None` out -- there was nothing to drop; the job
        kind's own default chain (`models.queue.backend.
        _resolve_priority`) takes over exactly as it always has."""
        assert resolve_client_priority(None) is None
