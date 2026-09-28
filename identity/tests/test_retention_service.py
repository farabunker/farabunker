"""Soft delete, restore, purge, and the sweep."""
from __future__ import annotations

import datetime
import logging

import pytest
from django.utils import timezone

from identity import audit as audit_module
from identity import retention as service
from identity.contracts import cascades as cascades_module
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED,
)
from identity.contracts.cascades import ORDER_FILES, RetentionHandler, register_retention_handler
from identity.contracts.retention import KIND_ASK, KIND_CONVERSATION, KIND_DOCUMENT, RetentionRefused
from identity.models import AuditEvent, DeletionTicket, IdentitySettings
from identity.tests._helpers import (
    make_admin, make_conversation, make_user, posture, user_principal,
)

pytestmark = pytest.mark.django_db

REMOVED: list[str] = []
CHILDREN: list[tuple[str, str]] = [(KIND_DOCUMENT, "doc-1"), (KIND_DOCUMENT, "doc-2")]
CALLED: list[str] = []


def ask_handler(key: str) -> int:
    REMOVED.append(key)
    return 1


def boom(key: str) -> int:
    raise RuntimeError("not finished")


def files_boom(key: str) -> int:
    """A FILES-band handler that raises. Registered at `order=ORDER_FILES`
    by the tests that use it -- what matters to those tests is the band,
    not that this stub actually touches a filesystem."""
    raise RuntimeError("disk is unavailable after removing bytes")


def refused(key: str) -> int:
    raise RetentionRefused("a worker still holds this item")


def conversation_handler(key: str) -> int:
    REMOVED.append(f"conversation:{key}")
    return 1


def document_handler(key: str) -> int:
    REMOVED.append(f"document:{key}")
    return 1


def document_handler_second_child_raises(key: str) -> int:
    """`doc-1` is `children`'s first pair, so its purge runs before
    `doc-2`'s -- this fails on the SECOND child on purpose, so the test
    that registers it can prove the first child's own row deletion and
    `content.purged` event are undone with everything else when the
    second one raises."""
    if key == "doc-2":
        raise RuntimeError("this child cannot finish")
    REMOVED.append(f"document:{key}")
    return 1


def fake_children(key: str) -> list[tuple[str, str]]:
    """Recomputed from the parent's own rows in production; a constant
    here, because what this module tests is what the SERVICE does with
    the pairs, not how a column finds them."""
    CALLED.append(key)
    return list(CHILDREN)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Save, clear, register, restore -- the shape
    `identity/tests/test_cascades.py::_isolated_registry` established,
    for the same reason: the registry is a module-level dict with no
    reset path, `boom` below names a handler that raises, and a
    registration that escaped this module would reach every later purge
    in the same pytest process. Both collection orders are the gate, so
    ordering luck cannot cover it.

    `CALLED` is this module's own list (`identity/tests/
    test_retention_runner.py` has one of its own; nothing is shared
    between the two modules).
    """
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    REMOVED.clear()
    CALLED.clear()
    register_retention_handler(RetentionHandler(
        kind=KIND_ASK, key="t.ask", label="Ask records",
        handler=f"{__name__}.ask_handler"))
    register_retention_handler(RetentionHandler(
        kind=KIND_CONVERSATION, key="t.conversation",
        label="Conversation and turns",
        handler=f"{__name__}.conversation_handler",
        children=f"{__name__}.fake_children"))
    register_retention_handler(RetentionHandler(
        kind=KIND_DOCUMENT, key="t.document", label="Document",
        handler=f"{__name__}.document_handler"))
    yield
    REMOVED.clear()
    CALLED.clear()
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

    def test_restoring_an_already_gone_ticket_writes_no_event(self):
        """A raced purge or a double-click leaves the caller holding a
        `DeletionTicket` instance whose row is already gone -- restoring
        it must not log a restore that never happened, and must not
        raise either."""
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).delete()

        service.restore_content(user_principal(user), ticket)

        assert AuditEvent.objects.filter(action=CONTENT_RESTORED).count() == 0


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

    def test_a_purge_of_an_already_gone_ticket_is_a_silent_no_op(self):
        """Two sweeps can overlap by design (prune-on-write on every
        delete, the cron command, the Deleted page's own GET) and a
        person can double-click "Delete permanently" -- the second
        `purge_ticket` on the same ticket must not run a handler twice
        or write a second event."""
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        first = service.purge_ticket(user_principal(user), ticket)
        assert first == {"Ask records": 1}

        second = service.purge_ticket(user_principal(user), ticket)
        assert second == {}
        assert REMOVED == ["5"]

        events = [e for e in audit_module.by_action([CONTENT_PURGED])
                  if e.target_key == "5"]
        assert len(events) == 1


class TestTheFailedPurgeMark:
    """`identity.retention.record_failed_purge`, called from the sweep's
    own `except Exception` (`_purge_due` below `sweep` in this module),
    the same site `test_a_refusal_is_a_warning_not_an_error_and_does_not_
    stop_the_batch` already drives through `service.sweep()`."""

    def test_a_files_band_failure_marks_the_ticket(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.filesboom", label="FilesBoom",
            handler=f"{__name__}.files_boom", order=ORDER_FILES))
        user = make_user()
        item = _owner(user)
        ticket = service.delete_content(user_principal(user),
                                        kind=KIND_CONVERSATION,
                                        key=str(item.pk), owner=item)
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        assert service.sweep() == 0

        ticket.refresh_from_db()
        assert ticket.content_unrecoverable is True

    def test_a_rows_band_failure_before_any_files_band_handler_does_not_mark(self):
        """The contrast case, over the SAME `boom` handler
        `test_a_purge_that_rolled_back_leaves_a_ticket_restore_still_
        accepts` above already pins as rows-band (no `order=` given, so
        it defaults to `ORDER_ROWS`): that rollback is clean, nothing on
        disk was ever touched, and this must not mark it."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.boom", label="Boom",
            handler=f"{__name__}.boom"))
        user = make_user()
        item = _owner(user)
        ticket = service.delete_content(user_principal(user),
                                        kind=KIND_CONVERSATION,
                                        key=str(item.pk), owner=item)
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        assert service.sweep() == 0

        ticket.refresh_from_db()
        assert ticket.content_unrecoverable is False

    def test_a_refusal_never_marks(self):
        """`RetentionRefused` means nothing was attempted -- caught
        separately, before the bare `except Exception`, and never
        reaching `record_failed_purge`."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.refused", label="Refused",
            handler=f"{__name__}.refused"))
        user = make_user()
        item = _owner(user)
        ticket = service.delete_content(user_principal(user),
                                        kind=KIND_CONVERSATION,
                                        key=str(item.pk), owner=item)
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        assert service.sweep() == 0

        ticket.refresh_from_db()
        assert ticket.content_unrecoverable is False


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

    def test_two_overlapping_sweeps_over_one_due_ticket_report_one_purge_in_total(self):
        """Two sweep passes can each already be holding this SAME
        ticket as due before either has purged it -- this delete's own
        prune-on-write sweep and, say, `manage.py purge_deleted`
        landing in the same window -- and `purge_ticket` on the ticket
        the other pass already purged is a correct silent no-op
        (`{}`, `test_a_purge_of_an_already_gone_ticket_is_a_silent_
        no_op` above). The second pass must not count that no-op as a
        purge of its own, or an operator running the command would be
        told two items were destroyed when only one was. Modelled
        directly, without threads: `_purge_due` is the sweep's own
        per-ticket pass, given the SAME ticket twice -- exactly the two
        due-list snapshots two genuinely overlapping `sweep()` calls
        would each already be holding, since a normal SECOND `sweep()`
        call's own due query would not even see a ticket the first
        call had, by then, already purged for real."""
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        seen_by_pass_one = DeletionTicket.objects.get(pk=ticket.pk)
        seen_by_pass_two = DeletionTicket.objects.get(pk=ticket.pk)

        total = service._purge_due([seen_by_pass_one, seen_by_pass_two])

        assert total == 1
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

    def test_the_default_limit_is_twenty_five(self):
        """`docs/OPERATIONS.md` now states this number to the operator
        reading `manage.py purge_deleted --help`, so a change here must
        be a deliberate, visible re-pin, not a silent drift between the
        code and the doc."""
        assert service.SWEEP_LIMIT == 25

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

    def test_a_refusal_is_a_warning_not_an_error_and_does_not_stop_the_batch(self, caplog):
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.refused", label="Refused",
            handler=f"{__name__}.refused"))
        register_retention_handler(RetentionHandler(
            kind=KIND_DOCUMENT, key="t.boom", label="Boom",
            handler=f"{__name__}.boom"))
        user = make_user()
        item = _owner(user)
        refused_ticket = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=str(item.pk), owner=item)
        errored_ticket = service.delete_content(
            user_principal(user), kind=KIND_DOCUMENT, key="doc-1", owner=item)
        DeletionTicket.objects.filter(
            pk__in=[refused_ticket.pk, errored_ticket.pk]).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        with caplog.at_level(logging.WARNING):
            assert service.sweep() == 0

        assert DeletionTicket.objects.filter(pk=refused_ticket.pk).exists()
        assert DeletionTicket.objects.filter(pk=errored_ticket.pk).exists()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(warnings) == 1 and warnings[0].exc_info is None
        assert len(errors) == 1 and errors[0].exc_info is not None


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


class TestChildTickets:
    def test_a_delete_tickets_the_children_with_the_same_date_and_owner(self):
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=item.pk,
            owner=item, label="A thread")

        children = DeletionTicket.objects.filter(kind=KIND_DOCUMENT).order_by("key")
        assert [t.key for t in children] == ["doc-1", "doc-2"]
        for child in children:
            assert child.parent_id == parent.pk
            assert child.purge_on == parent.purge_on
            assert child.owner_kind == parent.owner_kind
            assert child.owner_key == parent.owner_key
            assert child.deleted_by_key == str(user.pk)
            # THE PARENT'S NAME IS NOT THE CHILD'S: a child ticket
            # carries no label at all, so the page shows its kind.
            assert child.label == ""
        assert REMOVED == []

    def test_each_child_gets_its_own_content_free_event(self):
        user = make_user()
        item = _owner(user)
        service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                               key=item.pk, owner=item, label="A thread")
        events = AuditEvent.objects.filter(action=CONTENT_DELETED,
                                           target_type=KIND_DOCUMENT)
        assert events.count() == 2
        assert {e.target_label for e in events} == {""}
        assert {e.actor_key for e in events} == {str(user.pk)}

    def test_a_child_that_already_has_a_ticket_keeps_its_date_and_stays_its_own(self):
        """Unique on (kind, key): an image deleted from the gallery
        yesterday keeps ITS date, is NOT adopted by this delete, and
        gets no second event."""
        user = make_user()
        item = _owner(user)
        existing = service.delete_content(user_principal(user), kind=KIND_DOCUMENT,
                                          key="doc-1", owner=item, label="Its own")
        DeletionTicket.objects.filter(pk=existing.pk).update(
            purge_on=timezone.localdate() + datetime.timedelta(days=90))
        service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                               key=item.pk, owner=item)
        existing.refresh_from_db()
        assert existing.label == "Its own"
        assert existing.parent_id is None
        assert existing.purge_on == timezone.localdate() + datetime.timedelta(days=90)
        assert AuditEvent.objects.filter(
            action=CONTENT_DELETED, target_type=KIND_DOCUMENT,
            target_key="doc-1").count() == 1

    def test_a_handler_with_no_children_is_unchanged(self):
        user = make_user()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="5",
                               owner=_owner(user))
        assert DeletionTicket.objects.count() == 1

    def test_the_resolver_is_asked_at_the_delete_and_nowhere_else(self):
        """ASKED ONCE, AT EACH DELETE. Restore and permanent delete
        follow the link the delete wrote instead of asking again, so a
        column whose rows have since changed cannot make either of them
        reach a ticket this delete never created."""
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        assert CALLED == [str(item.pk)]
        service.restore_content(user_principal(user), parent)
        assert CALLED == [str(item.pk)]
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        service.purge_ticket(user_principal(user), parent)
        assert CALLED == [str(item.pk), str(item.pk)]

    def test_restoring_the_parent_removes_its_children(self):
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        service.restore_content(user_principal(user), parent)
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(
            action=CONTENT_RESTORED, target_type=KIND_DOCUMENT).count() == 2
        assert REMOVED == []

    def test_a_child_restored_on_its_own_is_simply_absent(self):
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")
        service.restore_content(user_principal(user), child)
        service.restore_content(user_principal(user), parent)
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(
            action=CONTENT_RESTORED, target_type=KIND_DOCUMENT).count() == 2

    def test_a_restore_leaves_a_ticket_this_delete_did_not_write(self):
        """The image was deleted from the gallery on its own date. A
        chat that happens to reference it is restored; the image is not
        put back, because nobody said to put it back."""
        user = make_user()
        item = _owner(user)
        own = service.delete_content(user_principal(user), kind=KIND_DOCUMENT,
                                     key="doc-1", owner=item, label="Its own")
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        service.restore_content(user_principal(user), parent)
        assert DeletionTicket.objects.filter(pk=own.pk).exists()
        assert DeletionTicket.objects.filter(kind=KIND_DOCUMENT,
                                             key="doc-2").count() == 0

    def test_permanent_delete_runs_this_items_own_handlers_before_any_childs(self):
        """ORDER IS THE POINT, but not the stronger claim it can look
        like. This item's own registered handlers run first, in their
        own band order, and only then does each child's handler run --
        never the other way round. That is NOT "every row this click
        touches is gone before the first byte is": `conversation_handler`
        here fakes the real `conversation` kind's registered handlers,
        which are themselves ORDER_FILES and destroy bytes, so this
        item's own bytes are already gone by the time a child's rows are
        even touched. The filesystem-last rule (ORDER_ROWS before
        ORDER_FILES) holds WITHIN each item's own `run_retention` call,
        not ACROSS the cascade -- what this test actually pins is the
        cascade order: item first, children after, never interleaved."""
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        service.purge_ticket(user_principal(user), parent)
        assert REMOVED == [f"conversation:{item.pk}",
                           "document:doc-1", "document:doc-2"]
        assert DeletionTicket.objects.count() == 0

    def test_the_parents_event_counts_the_children_under_their_own_label(self):
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        removed = service.purge_ticket(user_principal(user), parent)
        assert removed == {"Conversation and turns": 1, "Document": 2}
        event = AuditEvent.objects.get(action=CONTENT_PURGED,
                                       target_type=KIND_CONVERSATION)
        assert event.detail["removed"] == {"Conversation and turns": 1,
                                           "Document": 2}

    def test_every_destroyed_ticket_writes_its_own_event(self):
        """ONE EVENT PER TICKET DESTROYED, because the Deletion log
        lists tickets: an image that was listed with a date of its own
        is a line of its own when that date is spent."""
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        service.purge_ticket(user_principal(user), parent)
        assert AuditEvent.objects.filter(
            action=CONTENT_PURGED, target_type=KIND_DOCUMENT).count() == 2
        assert AuditEvent.objects.filter(
            action=CONTENT_PURGED, target_type=KIND_CONVERSATION).count() == 1

    def test_a_child_restored_alone_survives_the_parents_permanent_delete(self):
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        service.restore_content(
            user_principal(user),
            DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1"))
        service.purge_ticket(user_principal(user), parent)
        assert "document:doc-1" not in REMOVED
        assert "document:doc-2" in REMOVED

    def test_a_ticket_this_delete_did_not_write_survives_the_permanent_delete(self):
        """Its own date was printed for it, and this click is not that
        date."""
        user = make_user()
        item = _owner(user)
        own = service.delete_content(user_principal(user), kind=KIND_DOCUMENT,
                                     key="doc-1", owner=item, label="Its own")
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        service.purge_ticket(user_principal(user), parent)
        assert DeletionTicket.objects.filter(pk=own.pk).exists()
        assert "document:doc-1" not in REMOVED

    def test_the_sweep_counts_every_ticket_it_addressed(self):
        """A parent and two children are three items destroyed and three
        rows gone; reporting one would tell an operator two pictures are
        still there."""
        user = make_user()
        item = _owner(user)
        service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                               key=item.pk, owner=item)
        DeletionTicket.objects.all().update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        assert service.sweep() == 3
        assert DeletionTicket.objects.count() == 0
        assert sorted(REMOVED) == sorted(
            ["document:doc-1", "document:doc-2", f"conversation:{item.pk}"])

    def test_a_child_the_sweep_reached_first_is_simply_gone(self):
        """Children are ordinary due tickets. If a pass purges one
        before its parent, the parent's own purge finds one child fewer
        and completes."""
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")
        service.purge_ticket(user_principal(user), child)
        service.purge_ticket(user_principal(user), parent)
        assert DeletionTicket.objects.count() == 0
        assert sorted(REMOVED) == sorted(
            ["document:doc-1", "document:doc-2", f"conversation:{item.pk}"])

    def test_zero_days_purges_the_children_too(self):
        """THE ZERO-DAY PROOF. `delete_content` runs no immediate purge
        of its own: the children are written inside the same
        transaction with the SAME `purge_on`, so the unconditional
        prune-on-write sweep at the end of every delete finds parent
        and children all due today, and the parent's own purge destroys
        all three -- its rows first, then each child's bytes."""
        row = IdentitySettings.get_solo()
        row.retention_days = 0
        row.save()
        user = make_user()
        item = _owner(user)
        service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                               key=item.pk, owner=item)
        assert REMOVED == [f"conversation:{item.pk}",
                           "document:doc-1", "document:doc-2"]
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(action=CONTENT_PURGED).count() == 3

    def test_a_second_childs_raising_handler_rolls_back_the_whole_purge(self):
        """A half-purged cascade -- one child's content and ticket gone,
        the other child's handler never even reached, but the parent
        still standing -- is precisely what the one transaction around
        `purge_ticket` exists to prevent. `doc-1` (the first child
        `_purge_child` reaches) succeeds and is deleted with its own
        `content.purged` event BEFORE `doc-2`'s handler raises, so this
        proves the transaction undoes work already done inside it, not
        only work that never started."""
        register_retention_handler(RetentionHandler(
            kind=KIND_DOCUMENT, key="t.document", label="Document",
            handler=f"{__name__}.document_handler_second_child_raises"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)

        with pytest.raises(RuntimeError, match="this child cannot finish"):
            service.purge_ticket(user_principal(user), parent)

        assert DeletionTicket.objects.filter(pk=parent.pk).exists()
        assert DeletionTicket.objects.filter(kind=KIND_DOCUMENT,
                                             key="doc-1").exists()
        assert DeletionTicket.objects.filter(kind=KIND_DOCUMENT,
                                             key="doc-2").exists()
        assert DeletionTicket.objects.count() == 3
        assert not AuditEvent.objects.filter(action=CONTENT_PURGED).exists()

    def test_a_childs_refusal_also_propagates_to_the_caller(self):
        """`purge_ticket`'s docstring says a child's `RetentionRefused`
        aborts the whole click and is never swallowed -- this is that
        claim, pinned: the refusal is the child's, not the parent's, and
        it still reaches the caller as a refusal, with every ticket in
        the family left standing for the next attempt."""
        register_retention_handler(RetentionHandler(
            kind=KIND_DOCUMENT, key="t.document", label="Document",
            handler=f"{__name__}.refused"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)

        with pytest.raises(RetentionRefused):
            service.purge_ticket(user_principal(user), parent)

        assert DeletionTicket.objects.count() == 3
        assert not AuditEvent.objects.filter(action=CONTENT_PURGED).exists()

    def test_a_held_child_is_skipped_by_the_parents_permanent_delete(self):
        """Nothing in this delivery WRITES a hold (identity/tests/
        test_retention_service.py::TestTheSweep::test_it_skips_a_held_
        ticket makes the same point for the sweep's own due query), so
        the hold is set directly here -- the clause ships now so the
        deferred enterprise slice is a control and a refusal, not a
        change to this cascade."""
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        held = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-2")
        DeletionTicket.objects.filter(pk=held.pk).update(
            hold_by_kind="user", hold_by_key="1")

        service.purge_ticket(user_principal(user), parent)

        assert DeletionTicket.objects.filter(pk=held.pk).exists()
        assert not DeletionTicket.objects.filter(pk=parent.pk).exists()
        assert not DeletionTicket.objects.filter(kind=KIND_DOCUMENT,
                                                  key="doc-1").exists()
        assert "document:doc-1" in REMOVED
        assert "document:doc-2" not in REMOVED

    def test_restoring_the_parent_skips_and_detaches_a_held_child(self):
        """Mirrors `test_a_held_child_is_skipped_by_the_parents_permanent_
        delete` above, on the restore side: a hold is placed on the
        CHILD's ticket, and restoring some OTHER item (the parent) must
        not be able to lift it. The unheld sibling still comes back with
        its own event, exactly as before; the held one keeps its ticket,
        loses its link to the parent (the CASCADE would otherwise destroy
        it with the parent row), and gets no event -- nothing happened to
        it."""
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        held = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-2")
        DeletionTicket.objects.filter(pk=held.pk).update(
            hold_by_kind="user", hold_by_key="1")

        service.restore_content(user_principal(user), parent)

        assert not DeletionTicket.objects.filter(pk=parent.pk).exists()
        assert not DeletionTicket.objects.filter(kind=KIND_DOCUMENT,
                                                  key="doc-1").exists()
        held.refresh_from_db()
        assert held.parent_id is None
        assert held.hold_by_kind == "user"
        assert AuditEvent.objects.filter(
            action=CONTENT_RESTORED, target_type=KIND_DOCUMENT,
            target_key="doc-1").count() == 1
        assert AuditEvent.objects.filter(
            action=CONTENT_RESTORED, target_type=KIND_DOCUMENT,
            target_key="doc-2").count() == 0

    def test_the_sweep_excludes_a_held_child_from_its_own_count(self):
        """The held-sibling variant of `test_the_sweep_counts_every_
        ticket_it_addressed` above: a parent and TWO children are three
        items due, but one child is held, so the sweep destroys and
        counts only two -- itself and the unheld sibling -- and the held
        child's own ticket is left standing, exactly as `purge_ticket`
        itself would leave it."""
        user = make_user()
        item = _owner(user)
        service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                               key=item.pk, owner=item)
        held = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-2")
        DeletionTicket.objects.filter(pk=held.pk).update(
            hold_by_kind="user", hold_by_key="1")
        DeletionTicket.objects.exclude(pk=held.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        assert service.sweep() == 2

        assert DeletionTicket.objects.count() == 1
        assert DeletionTicket.objects.filter(pk=held.pk).exists()
        assert "document:doc-1" in REMOVED
        assert "document:doc-2" not in REMOVED


class TestTheOrganisationPostureRefusesAnEarlyDestroy:
    def test_the_owner_may_not_purge(self):
        user = make_user()
        with posture("enterprise"):
            ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                            key="5", owner=_owner(user))
            assert service.may_purge(user_principal(user), ticket) is False

    def test_nor_may_a_principal_who_sees_all_content(self):
        """THE PREDICATE IS NOT ABOUT STANDING. On this posture nobody
        destroys content early -- not the owner, not an administrator
        who may already read it."""
        admin = make_admin()
        with posture("enterprise"):
            ticket = service.delete_content(user_principal(admin), kind=KIND_ASK,
                                            key="5", owner=_owner(admin))
            assert service.may_purge(user_principal(admin), ticket) is False

    def test_restore_is_untouched(self):
        user = make_user()
        with posture("enterprise"):
            ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                            key="5", owner=_owner(user))
            service.restore_content(user_principal(user), ticket)
            assert DeletionTicket.objects.count() == 0

    def test_the_sweep_still_purges_on_the_date(self):
        """The refusal is about destroying it EARLY. The promised date
        arrives on this posture exactly as on any other."""
        user = make_user()
        with posture("enterprise"):
            ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                            key="5", owner=_owner(user))
            DeletionTicket.objects.filter(pk=ticket.pk).update(
                purge_on=timezone.localdate() - datetime.timedelta(days=1))
            assert service.sweep() == 1

    @pytest.mark.parametrize("name", ["personal", "open"])
    def test_the_other_postures_are_unchanged(self, name):
        user = make_user()
        with posture(name):
            ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                            key="5", owner=_owner(user))
            assert service.may_purge(user_principal(user), ticket) is True
