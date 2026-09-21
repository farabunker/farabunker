"""`manage.py purge_deleted` -- the operator's cron door, for a box
where prune-on-write is not enough on its own."""
from __future__ import annotations

import datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from identity.contracts import cascades as cascades_module
from identity.contracts.actions import CONTENT_PURGED
from identity.contracts.cascades import RetentionHandler, register_retention_handler
from identity.contracts.retention import KIND_ASK
from identity.models import AuditEvent, DeletionTicket
from identity.tests._helpers import make_conversation, make_user, user_principal
from identity import retention as service

pytestmark = pytest.mark.django_db


def noop_handler(key: str) -> int:
    return 0


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Save, clear, register, restore -- `identity/tests/
    test_cascades.py::_isolated_registry`'s shape, for its reason: the
    registry is a module-level dict with no reset path, and a
    registration that escaped this module would reach every later purge
    in the same pytest process. Both collection orders are the gate."""
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    register_retention_handler(RetentionHandler(
        kind=KIND_ASK, key="t.cli", label="Ask records",
        handler=f"{__name__}.noop_handler"))
    yield
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


def _overdue(count: int):
    user = make_user()
    item = make_conversation(owner_kind="user", owner_key=str(user.pk))
    tickets = [
        service.delete_content(user_principal(user), kind=KIND_ASK,
                               key=str(index), owner=item)
        for index in range(count)
    ]
    # ALL tickets are created first, then backdated together in ONE queryset
    # update: backdating one at a time would let each later `delete_content`
    # call's own unconditional prune-on-write sweep purge the earlier,
    # already-overdue ticket before this helper -- or the test that called
    # it -- ever gets to see it.
    DeletionTicket.objects.filter(pk__in=[ticket.pk for ticket in tickets]).update(
        purge_on=timezone.localdate() - datetime.timedelta(days=1))


class TestPurgeDeleted:
    def test_it_purges_due_tickets_and_says_how_many(self):
        _overdue(2)
        out = StringIO()
        call_command("purge_deleted", stdout=out)
        assert DeletionTicket.objects.count() == 0
        assert "2" in out.getvalue()

    def test_its_events_are_recorded_as_command_line(self):
        _overdue(1)
        call_command("purge_deleted", stdout=StringIO())
        event = AuditEvent.objects.filter(action=CONTENT_PURGED).first()
        assert event.source == "cli"
        assert (event.actor_kind, event.actor_key) == ("service", "local")

    def test_the_limit_bounds_one_run(self):
        _overdue(3)
        call_command("purge_deleted", "--limit", "1", stdout=StringIO())
        assert DeletionTicket.objects.count() == 2

    def test_a_box_with_nothing_due_is_not_an_error(self):
        out = StringIO()
        call_command("purge_deleted", stdout=out)
        assert "0" in out.getvalue()
