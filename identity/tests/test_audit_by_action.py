"""`identity.audit.by_action` -- the Purged tab's one read."""
from __future__ import annotations

import pytest

from identity import audit
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED, LOGIN,
)
from identity.contracts.principals import OPEN_PRINCIPAL

pytestmark = pytest.mark.django_db


class TestByAction:
    def test_it_returns_only_the_named_actions_newest_first(self):
        audit.record(OPEN_PRINCIPAL, LOGIN)
        first = audit.record(OPEN_PRINCIPAL, CONTENT_DELETED,
                             target_type="conversation", target_key="a")
        second = audit.record(OPEN_PRINCIPAL, CONTENT_PURGED,
                              target_type="conversation", target_key="a")
        rows = audit.by_action((CONTENT_PURGED, CONTENT_DELETED, CONTENT_RESTORED))
        assert [row.pk for row in rows] == [second.pk, first.pk]

    def test_it_honours_the_limit(self):
        for index in range(3):
            audit.record(OPEN_PRINCIPAL, CONTENT_DELETED,
                         target_type="ask", target_key=str(index))
        assert len(audit.by_action((CONTENT_DELETED,), limit=2)) == 2

    def test_an_empty_action_sequence_answers_an_empty_list(self):
        audit.record(OPEN_PRINCIPAL, CONTENT_DELETED, target_type="ask", target_key="1")
        assert audit.by_action(()) == []

    def test_it_returns_a_list_not_a_queryset(self):
        assert isinstance(audit.by_action((CONTENT_DELETED,)), list)
