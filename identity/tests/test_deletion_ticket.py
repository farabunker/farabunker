"""The ticket table and the three retention policy fields."""
from __future__ import annotations

import datetime

import pytest
from django.db.utils import IntegrityError

from identity.contracts import retention
from identity.models import DeletionTicket, IdentitySettings

pytestmark = pytest.mark.django_db


def _ticket(**overrides):
    fields = dict(kind=retention.KIND_CONVERSATION, key="k-1",
                  owner_kind="user", owner_key="7",
                  deleted_by_kind="user", deleted_by_key="7",
                  purge_on=datetime.date(2026, 10, 21))
    fields.update(overrides)
    return DeletionTicket.objects.create(**fields)


class TestTheKindIsAClosedVocabulary:
    def test_an_unknown_kind_raises_at_save_like_an_unknown_audit_action(self):
        with pytest.raises(ValueError, match="Unknown deletion kind"):
            _ticket(kind="workstream")

    def test_every_declared_kind_saves(self):
        for index, kind in enumerate(retention.RETENTION_KINDS):
            assert _ticket(kind=kind, key=f"k-{index}").pk


class TestOneTicketPerItem:
    def test_a_second_ticket_for_the_same_item_is_refused_by_the_constraint(self):
        _ticket()
        with pytest.raises(IntegrityError):
            _ticket()

    def test_the_same_key_under_a_different_kind_is_a_different_item(self):
        _ticket(kind=retention.KIND_CONVERSATION, key="7")
        assert _ticket(kind=retention.KIND_ASK, key="7").pk


class TestTheHoldColumnsShipEmpty:
    def test_nothing_in_this_delivery_writes_them(self):
        ticket = _ticket()
        assert ticket.hold_by_kind == ""
        assert ticket.hold_by_key == ""
        assert ticket.hold_note == ""


class TestTheFailureMarkShipsFalse:
    def test_an_ordinary_ticket_is_not_marked(self):
        """`content_unrecoverable` is written by exactly one path
        (`identity.retention.record_failed_purge`, only after a purge
        destroyed content and then failed) -- an ordinary ticket, created
        the normal way, never carries it."""
        assert _ticket().content_unrecoverable is False


class TestTheShippedPolicyNeedsNoConfiguration:
    def test_a_freshly_migrated_row_carries_the_documented_defaults(self):
        row = IdentitySettings.get_solo()
        assert row.retention_days == retention.RETENTION_DAYS_DEFAULT
        assert row.queue_retention_days == retention.QUEUE_RETENTION_DAYS_DEFAULT
        assert row.audit_detail is False

    def test_zero_and_null_are_both_storable(self):
        """The "zero stays expressible" constraint, for two fields:
        `retention_days=0` is "no grace period", `queue_retention_days=
        None` is "no age cliff"."""
        row = IdentitySettings.get_solo()
        row.retention_days = 0
        row.queue_retention_days = None
        row.save()
        row.refresh_from_db()
        assert row.retention_days == 0
        assert row.queue_retention_days is None


class TestOrderingAndIndexes:
    def test_tickets_come_back_newest_first(self):
        first = _ticket(key="a")
        second = _ticket(key="b")
        assert list(DeletionTicket.objects.all()) == [second, first]


class TestTheParentLink:
    def test_a_ticket_has_no_parent_unless_one_was_given(self):
        assert _ticket().parent_id is None

    def test_deleting_a_parent_removes_its_children(self):
        """CASCADE: a child ticket cannot outlive the ticket it arrived
        with, so there is no orphan row to render or reason about."""
        parent = _ticket(key="chat-1")
        _ticket(kind=retention.KIND_VISION_JOB, key="job-1", parent=parent)
        DeletionTicket.objects.filter(pk=parent.pk).delete()
        assert DeletionTicket.objects.count() == 0

    def test_deleting_a_child_leaves_its_parent(self):
        parent = _ticket(key="chat-1")
        child = _ticket(kind=retention.KIND_VISION_JOB, key="job-1", parent=parent)
        DeletionTicket.objects.filter(pk=child.pk).delete()
        assert list(DeletionTicket.objects.all()) == [parent]
