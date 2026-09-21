"""Soft delete, restore, purge, and the sweep."""
from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from identity import retention as service
from identity.contracts import cascades as cascades_module
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED,
)
from identity.contracts.cascades import RetentionHandler, register_retention_handler
from identity.contracts.retention import KIND_ASK, KIND_CONVERSATION
from identity.models import AuditEvent, DeletionTicket, IdentitySettings
from identity.tests._helpers import (
    make_admin, make_conversation, make_user, posture, user_principal,
)

pytestmark = pytest.mark.django_db

REMOVED: list[str] = []


def ask_handler(key: str) -> int:
    REMOVED.append(key)
    return 1


def boom(key: str) -> int:
    raise RuntimeError("not finished")


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Save, clear, register, restore -- the shape
    `identity/tests/test_cascades.py::_isolated_registry` established,
    for the same reason: the registry is a module-level dict with no
    reset path, `boom` below names a handler that raises, and a
    registration that escaped this module would reach every later purge
    in the same pytest process. Both collection orders are the gate, so
    ordering luck cannot cover it.
    """
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    REMOVED.clear()
    register_retention_handler(RetentionHandler(
        kind=KIND_ASK, key="t.ask", label="Ask records",
        handler=f"{__name__}.ask_handler"))
    yield
    REMOVED.clear()
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


def _owner(user):
    """An item row's owner columns, which is all `delete_content` reads."""
    return make_conversation(owner_kind="user", owner_key=str(user.pk))


class TestSoftDelete:
    def test_it_writes_one_ticket_with_the_promised_date_and_purges_nothing(self):
        user = make_user()
        item = _owner(user)
        ticket = service.delete_content(
            user_principal(user), kind=KIND_ASK, key="5", owner=item, label="A question")

        assert ticket.purge_on == timezone.localdate() + datetime.timedelta(days=30)
        assert ticket.owner_kind == "user" and ticket.owner_key == str(user.pk)
        assert ticket.deleted_by_key == str(user.pk)
        assert ticket.label == "A question"
        assert REMOVED == []
        assert AuditEvent.objects.filter(action=CONTENT_DELETED).count() == 1

    def test_zero_days_purges_before_the_call_returns(self):
        row = IdentitySettings.get_solo()
        row.retention_days = 0
        row.save()
        user = make_user()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="5",
                               owner=_owner(user))
        assert REMOVED == ["5"]
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(action=CONTENT_PURGED).count() == 1

    def test_a_second_delete_of_the_same_item_is_a_no_op(self):
        user = make_user()
        item = _owner(user)
        first = service.delete_content(user_principal(user), kind=KIND_ASK,
                                       key="5", owner=item)
        second = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=item)
        assert first.pk == second.pk
        assert DeletionTicket.objects.count() == 1
        assert AuditEvent.objects.filter(action=CONTENT_DELETED).count() == 1


class TestRestore:
    def test_it_deletes_the_ticket_and_records_the_event(self):
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        service.restore_content(user_principal(user), ticket)
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(action=CONTENT_RESTORED).count() == 1
        assert REMOVED == []


class TestPurge:
    def test_a_completed_purge_leaves_no_ticket(self):
        """The invariant the whole "a ticket means restorable" reading
        rests on (spec section 3.1), asserted directly."""
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        removed = service.purge_ticket(user_principal(user), ticket)
        assert removed == {"Ask records": 1}
        assert DeletionTicket.objects.count() == 0

    def test_a_purge_that_rolled_back_leaves_a_ticket_restore_still_accepts(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.boom", label="Boom",
            handler=f"{__name__}.boom"))
        user = make_user()
        item = _owner(user)
        ticket = service.delete_content(user_principal(user),
                                        kind=KIND_CONVERSATION,
                                        key=str(item.pk), owner=item)
        with pytest.raises(RuntimeError):
            service.purge_ticket(user_principal(user), ticket)
        ticket.refresh_from_db()
        service.restore_content(user_principal(user), ticket)
        assert DeletionTicket.objects.count() == 0

    def test_the_audit_detail_toggle_moves_the_label_and_suppresses_nothing(self):
        user = make_user()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="5",
                               owner=_owner(user), label="A question")
        off = AuditEvent.objects.filter(action=CONTENT_DELETED).first()
        assert off.target_label == ""
        assert off.target_type == KIND_ASK and off.target_key == "5"

        row = IdentitySettings.get_solo()
        row.audit_detail = True
        row.save()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="6",
                               owner=_owner(user), label="Another question")
        on = AuditEvent.objects.filter(action=CONTENT_DELETED,
                                       target_key="6").first()
        assert on.target_label == "Another question"
        assert AuditEvent.objects.filter(action=CONTENT_DELETED).count() == 2


class TestTicketedKeys:
    def test_it_costs_one_query_and_answers_a_list(self, django_assert_num_queries):
        user = make_user()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="5",
                               owner=_owner(user))
        with django_assert_num_queries(1):
            keys = service.ticketed_keys(KIND_ASK)
        assert keys == ["5"]
        assert isinstance(keys, list)

    def test_it_answers_only_its_own_kind(self):
        user = make_user()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="5",
                               owner=_owner(user))
        assert service.ticketed_keys(KIND_CONVERSATION) == []


class TestTheSweep:
    def test_it_picks_up_a_ticket_whose_date_has_arrived(self):
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        assert service.sweep() == 1
        assert REMOVED == ["5"]

    def test_it_skips_a_held_ticket(self):
        """Nothing in this delivery WRITES a hold, so the ticket is
        constructed with one directly -- the clause ships now so the
        deferred enterprise slice is a control and a refusal, not a
        change to this query."""
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1),
            hold_by_kind="user", hold_by_key="1")
        assert service.sweep() == 0
        assert DeletionTicket.objects.count() == 1

    def test_it_is_bounded_by_the_limit(self):
        """All four tickets are created FIRST, while none of them is due
        (the shipped default is 30 days), and backdated together in ONE
        queryset update AFTER every create has already run. Interleaving
        a create with a backdate, one ticket at a time, would let each
        later `delete_content`'s own unconditional prune-on-write sweep
        purge the earlier, now-overdue ticket before this test ever
        calls `sweep` itself -- exactly the behaviour
        `test_deleting_anything_purges_what_has_already_fallen_due`
        below pins on purpose. This test is about the LIMIT, so its own
        fixtures must not be eaten by the thing it is not testing."""
        user = make_user()
        item = _owner(user)
        tickets = [
            service.delete_content(user_principal(user), kind=KIND_ASK,
                                   key=str(index), owner=item)
            for index in range(4)
        ]
        DeletionTicket.objects.filter(pk__in=[t.pk for t in tickets]).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        assert service.sweep(limit=2) == 2
        assert DeletionTicket.objects.count() == 2

    def test_one_failing_ticket_does_not_stop_the_batch(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.boom", label="Boom",
            handler=f"{__name__}.boom"))
        user = make_user()
        item = _owner(user)
        bad = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                     key=str(item.pk), owner=item)
        good = service.delete_content(user_principal(user), kind=KIND_ASK,
                                      key="5", owner=item)
        DeletionTicket.objects.filter(pk__in=[bad.pk, good.pk]).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        assert service.sweep() == 1
        assert DeletionTicket.objects.filter(pk=bad.pk).exists()
        assert not DeletionTicket.objects.filter(pk=good.pk).exists()

    def test_it_always_acts_as_the_service_principal(self):
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        service.sweep()
        purged = AuditEvent.objects.filter(action=CONTENT_PURGED).first()
        assert (purged.actor_kind, purged.actor_key) == ("service", "local")

    def test_deleting_anything_purges_what_has_already_fallen_due(self):
        """Prune-on-write (spec section 3.9, "Three callers"): the
        shipped default keeps a box that is used at all clean with no
        scheduler, because `delete_content` runs a bounded `sweep()`
        unconditionally, not only when the item it just deleted is
        itself due. `a` sits on the ordinary 30-day policy, already
        overdue by the time anybody deletes `b` -- a different item,
        with nothing else in common -- and `a`'s content is gone
        (purged, not merely swept up) as a side effect of that unrelated
        call, while `b`'s own ticket, freshly written and nowhere near
        its own cliff, stands untouched."""
        user = make_user()
        item = _owner(user)
        stale = service.delete_content(user_principal(user), kind=KIND_ASK,
                                       key="a", owner=item)
        DeletionTicket.objects.filter(pk=stale.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        fresh = service.delete_content(user_principal(user), kind=KIND_ASK,
                                       key="b", owner=item)

        assert not DeletionTicket.objects.filter(pk=stale.pk).exists()
        assert DeletionTicket.objects.filter(pk=fresh.pk).exists()
        assert AuditEvent.objects.filter(action=CONTENT_PURGED,
                                         target_key="a").exists()
        assert REMOVED == ["a"]


class TestStanding:
    def test_an_owner_and_a_sees_all_content_principal_may_purge_and_a_stranger_may_not(self):
        with posture("personal"):
            owner, stranger, admin = make_user(), make_user(), make_admin()
            item = _owner(owner)
            ticket = service.delete_content(user_principal(owner), kind=KIND_ASK,
                                            key="5", owner=item)
            assert service.may_purge(user_principal(owner), ticket) is True
            assert service.may_purge(user_principal(stranger), ticket) is False
            assert service.may_purge(user_principal(admin), ticket) is False

            row = IdentitySettings.get_solo()
            row.admin_sees_content = True
            row.save()
            assert service.may_purge(user_principal(admin), ticket) is True

    def test_visible_tickets_narrows_for_a_member_and_not_on_an_open_box(self):
        with posture("personal"):
            mine, theirs = make_user(), make_user()
            service.delete_content(user_principal(mine), kind=KIND_ASK, key="1",
                                   owner=_owner(mine))
            service.delete_content(user_principal(theirs), kind=KIND_ASK, key="2",
                                   owner=_owner(theirs))
            assert [t.key for t in service.visible_tickets(user_principal(mine))] == ["1"]
        with posture("open"):
            assert len(service.visible_tickets(user_principal(mine))) == 2
