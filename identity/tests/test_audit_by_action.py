"""`identity.audit.by_action` -- the Deletion log's one read."""
from __future__ import annotations

import pytest

from identity import audit
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED, LOGIN,
)
from identity.contracts.principals import OPEN_PRINCIPAL, Principal

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

    def test_an_actor_narrows_before_the_limit_not_after(self):
        """A caller that sliced the newest `limit` rows box-wide and left
        scoping to itself would leave a viewer's own event out of that
        slice entirely -- on any box where another principal (an
        administrator, a busy colleague, the service principal's own
        purge events) produced `limit` or more recent rows. `actor`
        filters BEFORE the slice, so the viewer's own event survives
        regardless of how many other principals' events are newer."""
        other = Principal("user", "999")
        viewer = Principal("user", "1")
        for index in range(120):
            audit.record(other, CONTENT_DELETED, target_type="ask", target_key=str(index))
        mine = audit.record(viewer, CONTENT_DELETED, target_type="ask", target_key="mine")

        rows = audit.by_action((CONTENT_DELETED,), actor=viewer)

        assert [row.pk for row in rows] == [mine.pk]

    def test_with_no_actor_the_read_stays_unscoped(self):
        audit.record(OPEN_PRINCIPAL, CONTENT_DELETED, target_type="ask", target_key="1")
        audit.record(Principal("user", "2"), CONTENT_DELETED, target_type="ask", target_key="2")
        assert len(audit.by_action((CONTENT_DELETED,))) == 2
