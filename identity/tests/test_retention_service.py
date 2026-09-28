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
# A DELIBERATELY UNREAL OWNER: no test in this module ever creates a
# `user` whose primary key is this value, so a child stamped with it
# can never coincide with a real deleting principal's own owner columns
# by accident -- the two-owner tests below need that guarantee to be a
# real test, not a coin flip.
_OTHER_OWNER = ("user", "999999999")


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


def fake_children(key: str) -> list[tuple[str, str, str, str]]:
    """Recomputed from the parent's own rows in production; here, the
    KEYS are a constant (`CHILDREN`) because what this module tests is
    what the SERVICE does with the pairs, not how a column finds them --
    but the OWNER is read off the real conversation row `key` names, the
    same conversation `_owner(user)` built for this delete, so the
    ordinary, same-owner case every OTHER test in this module exercises
    keeps behaving exactly as it did before the resolver contract
    carried an owner at all. The cross-owner tests below register their
    OWN resolver (`cross_owner_children`) instead, naming a REAL, DIFFERENT
    owner explicitly -- this default could not do that generically
    without knowing, at import time, which user a given test will
    create."""
    CALLED.append(key)
    from django.apps import apps
    conversation = apps.get_model("agents.Conversation").objects.get(pk=key)
    return [(kind, doc_key, conversation.owner_kind, conversation.owner_key)
            for kind, doc_key in CHILDREN]


def cross_owner_children(key: str) -> list[tuple[str, str, str, str]]:
    """ONE child whose owner is `_OTHER_OWNER` -- REAL, and DEFINITELY
    NOT the deleting conversation's own owner (see that constant's own
    comment) -- for the tests that need a child key carrying a REAL
    owner that DIFFERS from the parent's, so the assertion would fail if
    stamping ever regressed to copying the parent item's owner again."""
    CALLED.append(key)
    return [(KIND_DOCUMENT, "doc-1", *_OTHER_OWNER)]


def blank_owner_children(key: str) -> list[tuple[str, str, str, str]]:
    """ONE child with GENUINELY BLANK owner columns -- the shape every
    `GenerationJob` written before `tools/vision/migrations/
    0006_generationjob_owner.py` carries to this day, since that
    migration's bare `AddField` backfilled nothing. No principal can
    own `("", "")` (`Principal.__post_init__` forbids a blank key
    outright), which is the fixture the owner ruling (2026-09-28)
    exists for: a permanent delete must still destroy this child
    through the conversation's own owner, not silently skip it
    forever."""
    CALLED.append(key)
    return [(KIND_DOCUMENT, "doc-1", "", "")]


def transposed_owner_children(key: str) -> list[tuple[str, str, str, str]]:
    """The exact shape a resolver that swapped `owner_kind`/`owner_key`
    at the last leg would answer: a real key ("42") sitting where a
    principal kind belongs, and a real principal kind ("user") sitting
    where a key belongs. Before `delete_content`'s validation existed,
    this wrote a ticket with `owner_kind="42"` and no error ever --
    invisible on every Deleted page, reachable only by the sweep."""
    CALLED.append(key)
    return [(KIND_DOCUMENT, "doc-1", "42", "user")]


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

    def test_a_rows_band_refusal_does_not_mark(self):
        """A `RetentionRefused` from a ROWS-band handler reaches no
        files band at all, so `record_failed_purge` -- called here too
        now, the same as the exception branch beside it -- is a no-op:
        it is self-guarding on `_files_band_reached`, never on which
        exception ended the call."""
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

    def test_a_files_band_refusal_marks_the_ticket(self):
        """THE CASE `test_a_rows_band_refusal_does_not_mark` ABOVE DOES
        NOT COVER: a files-band handler that refuses INSTEAD of raising
        an ordinary exception. `purge_ticket`'s own docstring already
        says a child's `RetentionRefused` propagates uncaught exactly
        like any other exception, and a single files-band handler can
        destroy bytes and then refuse in the same call -- so the
        refusal branch must mark exactly when the exception branch
        beside it would, and this is the flip of the previous test: if
        the refusal branch stopped calling `record_failed_purge`, this
        assertion would read `False` again."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.refused_files", label="RefusedFiles",
            handler=f"{__name__}.refused", order=ORDER_FILES))
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


class TestTheFailedPurgeMarkAndChildOwnership:
    """A failed purge's mark must cover only the children THIS ATTEMPT
    could actually have reached -- `_purge_child` is only ever called
    for `children` (`identity.retention.purge_ticket`'s own loop), never
    for `children_not_owned` or a held child, so marking either of those
    because a SIBLING's files-band handler failed would refuse Restore
    for an item this attempt was structurally incapable of touching.
    Every test here overrides key `"t.conversation"` with a files-band
    handler that raises, so `_files_band_reached` is set before the
    children loop is ever reached, and drives the failure through
    `service.purge_ticket` directly -- the user's own click, not the
    sweep -- calling `service.record_failed_purge` the same way both
    real callers (`identity.views.deleted_purge`, `_purge_due`) do, from
    outside the rolled-back transaction."""

    def test_a_failed_purge_does_not_mark_a_child_the_clicker_does_not_own(self):
        """If `record_failed_purge` still filtered on `parent_id=ticket.
        pk` alone, `child` would still be linked to `parent` at rollback
        time (the detach only happens on a SUCCESSFUL purge, which this
        is not) and `child.content_unrecoverable` below would read
        `True` -- stripping Restore from an item this click never
        reached."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns",
            handler=f"{__name__}.files_boom", order=ORDER_FILES,
            children=f"{__name__}.cross_owner_children"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=item.pk, owner=item)
        child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")

        with pytest.raises(RuntimeError):
            service.purge_ticket(user_principal(user), parent)
        service.record_failed_purge(parent)

        parent.refresh_from_db()
        child.refresh_from_db()
        assert parent.content_unrecoverable is True
        assert child.content_unrecoverable is False
        assert service.may_restore(child) is True

    def test_a_failed_purge_does_not_mark_a_held_child(self):
        """The held case beside the not-owned one above: a held child
        is excluded from `candidate_children` before either partition
        even runs, so it must never enter the attempted set either --
        the same stale-link reasoning applies, since the detach that
        would otherwise unlink it only happens on success."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns",
            handler=f"{__name__}.files_boom", order=ORDER_FILES,
            children=f"{__name__}.fake_children"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=item.pk, owner=item)
        held = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-2")
        DeletionTicket.objects.filter(pk=held.pk).update(
            hold_by_kind="user", hold_by_key="1")

        with pytest.raises(RuntimeError):
            service.purge_ticket(user_principal(user), parent)
        service.record_failed_purge(parent)

        parent.refresh_from_db()
        held.refresh_from_db()
        assert parent.content_unrecoverable is True
        assert held.content_unrecoverable is False
        assert service.may_restore(held) is True

    def test_a_failed_purge_still_marks_a_child_the_clicker_does_own(self):
        """C1's neighbour, keeping the fix honest: an owned child's mark
        is unchanged by the new filter -- both of the clicker's own
        children are still marked when the family's files band was
        entered, exactly as `test_a_second_childs_files_band_failure_
        marks_the_whole_family` already pins for the sweep's own call
        path. If the fix over-corrected to "never mark a child", both
        assertions below would read `False`."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns",
            handler=f"{__name__}.files_boom", order=ORDER_FILES,
            children=f"{__name__}.fake_children"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=item.pk, owner=item)
        first_child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")
        second_child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-2")

        with pytest.raises(RuntimeError):
            service.purge_ticket(user_principal(user), parent)
        service.record_failed_purge(parent)

        parent.refresh_from_db()
        first_child.refresh_from_db()
        second_child.refresh_from_db()
        assert parent.content_unrecoverable is True
        assert first_child.content_unrecoverable is True
        assert second_child.content_unrecoverable is True


class TestTheMarkOutsideAnyAmbientTransaction:
    """Every test in `TestTheFailedPurgeMark` above runs under the
    module's own `pytestmark = pytest.mark.django_db` -- the
    NON-transactional fixture, which wraps the whole test body in one
    outer atomic block. Inside that block, `purge_ticket`'s own `with
    transaction.atomic():` is a SAVEPOINT, not the outermost transaction
    -- `record_failed_purge`'s `.update()` still lands there, but by
    savepoint-rollback semantics, a DIFFERENT mechanism than the one
    production actually runs: no view or management command in this
    codebase opens a transaction of its own (no `ATOMIC_REQUESTS`, no
    `@transaction.atomic` on `deleted_purge`/`sweep`'s three call sites),
    so `purge_ticket`'s block is the OUTERMOST transaction there, and its
    rollback is a real `connection.rollback()` followed by an ordinary,
    separately-committed write. `@pytest.mark.django_db(transaction=True)`
    on this one test removes the ambient block so the same assertion
    proves the topology the box actually ships into, not only the one
    every other test in this module happens to run under.

    THE CONSTRAINT THIS RELIES ON: nothing between this write and the
    view or command that triggers it may open a further `transaction.
    atomic(savepoint=False)` around the purge -- doing so would turn
    `record_failed_purge`'s `.update()` into a write inside a block
    Django has already marked for rollback, raising
    `TransactionManagementError` from inside a never-500 handler. Named
    here, and at `purge_ticket`'s own `with transaction.atomic():` line,
    so a future wrapper does not add one without reading this first.
    """

    @pytest.mark.django_db(transaction=True)
    def test_a_files_band_failure_marks_the_ticket_with_no_ambient_transaction(self):
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


class TestMayRestore:
    def test_an_ordinary_ticket_may_be_restored(self):
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        assert service.may_restore(ticket) is True

    def test_a_marked_ticket_may_not(self):
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            content_unrecoverable=True)
        ticket.refresh_from_db()
        assert service.may_restore(ticket) is False


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

    def test_a_refused_childs_own_ticket_is_named_not_the_parents(self, caplog):
        """An operator watching this log is diagnosing a FAMILY that will
        not purge; the ticket that actually refused is `doc-1` (`fake_
        children`'s first pair, and `refused` raises unconditionally, so
        it is always the one `_purge_child` reaches first), not the
        conversation the click addressed. Naming the parent here tells
        the operator the wrong row to go look at."""
        register_retention_handler(RetentionHandler(
            kind=KIND_DOCUMENT, key="t.document", label="Document",
            handler=f"{__name__}.refused"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        DeletionTicket.objects.filter(pk=parent.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        with caplog.at_level(logging.WARNING):
            assert service.sweep() == 0

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "document:doc-1" in message
        assert f"conversation:{parent.key}" not in message

    def test_a_second_childs_raising_ticket_is_named_not_the_parents(self, caplog):
        """Same diagnosis for the OTHER exception branch (`logger.
        exception`, not `logger.warning`): `document_handler_second_
        child_raises` only raises on `doc-2`, so the name in the log
        must be `doc-2` -- neither the parent conversation nor `doc-1`,
        which purged cleanly before the sibling failed."""
        register_retention_handler(RetentionHandler(
            kind=KIND_DOCUMENT, key="t.document", label="Document",
            handler=f"{__name__}.document_handler_second_child_raises"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        DeletionTicket.objects.filter(pk=parent.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        with caplog.at_level(logging.WARNING):
            assert service.sweep() == 0

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert "document:doc-2" in message
        assert "document:doc-1" not in message
        assert f"conversation:{parent.key}" not in message


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
    def test_a_delete_tickets_the_children_with_the_same_date(self):
        """THE SAME-OWNER CASE -- the ordinary one, where the content a
        conversation's images name belongs to the same principal as the
        conversation itself. This does NOT prove ownership is read from
        the CONTENT rather than copied from the parent: the two happen
        to coincide here by construction (`fake_children` reads the real
        conversation row's own owner). `test_a_child_owned_by_another_
        principal_is_ticketed_under_that_principal` below is the test
        that tells the two stamping strategies apart."""
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
            assert child.deleted_by_key == str(user.pk)
            # THE PARENT'S NAME IS NOT THE CHILD'S: a child ticket
            # carries no label at all, so the page shows its kind.
            assert child.label == ""
        assert REMOVED == []

    def test_a_child_owned_by_another_principal_is_ticketed_under_that_principal(self):
        """THE REAL TEST OF THE STAMPING RULE: `cross_owner_children`
        names ONE child whose owner (`_OTHER_OWNER`) is REAL and is
        DEFINITELY NOT `item`'s own owner. If `delete_content` still
        stamped a child from the PARENT's owner columns -- the bug this
        wave fixes -- `child.owner_key` here would read `str(user.pk)`,
        not `_OTHER_OWNER[1]`, and the final `!=` assertion against the
        parent's own owner would fail too."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns",
            handler=f"{__name__}.conversation_handler",
            children=f"{__name__}.cross_owner_children"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=item.pk,
            owner=item, label="A thread")

        child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")
        assert child.parent_id == parent.pk
        assert child.purge_on == parent.purge_on
        assert (child.owner_kind, child.owner_key) == _OTHER_OWNER
        assert (child.owner_kind, child.owner_key) != (
            parent.owner_kind, parent.owner_key)

    def test_the_clickers_permanent_delete_skips_and_detaches_a_child_they_do_not_own(self):
        """Owner ruling (2026-09-28): a permanent delete of the PARENT
        skips a child the clicker does not own, detaching it rather than
        destroying it. `_OTHER_OWNER` is REAL and definitely not
        `user`'s own -- if this skip ever regressed to "purge everyone's
        children regardless", `child`'s row and its file would both be
        gone afterwards, and both assertions below would fail."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns",
            handler=f"{__name__}.conversation_handler",
            children=f"{__name__}.cross_owner_children"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=item.pk,
            owner=item, label="A thread")
        child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")

        service.purge_ticket(user_principal(user), parent)

        assert not DeletionTicket.objects.filter(pk=parent.pk).exists()
        child.refresh_from_db()
        assert child.parent_id is None
        assert (child.owner_kind, child.owner_key) == _OTHER_OWNER
        assert service.may_restore(child) is True
        assert "document:doc-1" not in REMOVED

    def test_the_sweep_still_takes_a_child_the_clicker_did_not_own(self):
        """THE OTHER HALF OF THE SAME RULING: nothing outlives the date
        it was promised. The sweep always acts as `SERVICE_PRINCIPAL`,
        so the same family the click above leaves standing is fully
        destroyed once it falls due -- if the skip in `purge_ticket`
        were ever keyed on ownership alone rather than on WHO is acting,
        the sweep would leak this child forever, and `sweep()` would
        answer `1`, not `2`."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns",
            handler=f"{__name__}.conversation_handler",
            children=f"{__name__}.cross_owner_children"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=item.pk,
            owner=item, label="A thread")
        DeletionTicket.objects.filter(kind__in=[KIND_CONVERSATION, KIND_DOCUMENT]).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        assert service.sweep() == 2

        assert DeletionTicket.objects.count() == 0
        assert "document:doc-1" in REMOVED

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

    def test_a_second_childs_files_band_failure_marks_the_whole_family(self):
        """`record_failed_purge`'s own attribute (`_files_band_reached`)
        is ONE PYTHON FLAG on the object the caller holds, set the
        instant ANY files-band handler in the family is about to run --
        it cannot say WHICH member's own bytes are actually gone, only
        that the family's files band was entered. `doc-1`'s own
        FILES-band handler destroys real bytes and returns; `doc-2`'s
        then raises, in the SAME transaction, rolling every ROW back --
        `doc-1`'s ticket comes back looking untouched even though its
        file is truly gone. THE ASSERTION THAT WOULD FLIP if this still
        marked only the ticket `record_failed_purge` was called with (the
        parent): `doc-1`'s own `content_unrecoverable` would read `False`
        here, and `may_restore` would still offer Restore for an item
        whose file no longer exists -- exactly the harm this fix
        closes."""
        register_retention_handler(RetentionHandler(
            kind=KIND_DOCUMENT, key="t.document", label="Document",
            handler=f"{__name__}.document_handler_second_child_raises",
            order=ORDER_FILES))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        DeletionTicket.objects.filter(pk=parent.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        assert service.sweep() == 0

        parent.refresh_from_db()
        first_child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")
        second_child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-2")
        assert parent.content_unrecoverable is True
        assert first_child.content_unrecoverable is True
        assert second_child.content_unrecoverable is True
        assert service.may_restore(parent) is False
        assert service.may_restore(first_child) is False
        assert service.may_restore(second_child) is False
        # THE ROLLBACK IS REAL: `doc-1`'s row is still there, standing,
        # even though its own handler already ran and "removed" it (the
        # fake handler's own bookkeeping) before the sibling raised.
        assert "document:doc-1" in REMOVED

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

    def test_restoring_the_parent_skips_and_detaches_a_marked_child(self):
        """The `content_unrecoverable` sibling of `test_restoring_the_
        parent_skips_and_detaches_a_held_child` above, and the pin for
        the sequence `may_restore` exists to prevent: B's child ticket
        is marked the way a failed purge marks one -- `record_failed_
        purge` -- while it is still linked to A's conversation, because
        that detach only happens on a SUCCESSFUL purge. `cross_owner_
        children` (used elsewhere in this class) stamps the child with
        `_OTHER_OWNER`, a REAL principal that is DEFINITELY NOT the
        conversation's own owner -- the actual two-principal sequence
        the Critical this test pins describes, not a same-owner stand-in
        for it. (The restore loop itself asks no ownership question, so
        a same-owner child would run byte-identical code; this fixture
        is chosen to MATCH the scenario, not to reach a different code
        path.) A then restores the parent. Before this fix, restoring
        the parent deleted the child's ticket along with the rest of the
        family -- resurrecting B's half-destroyed content, under A's
        restore, as ordinary live content, and destroying the one
        column that recorded the bytes were gone. If `restore_content`'s
        child loop still filtered on `hold_by_kind` alone, this
        assertion would fail: the child's ticket would be gone, not
        surviving."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns", handler=f"{__name__}.conversation_handler",
            children=f"{__name__}.cross_owner_children"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                        key=item.pk, owner=item)
        marked = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")
        assert (marked.owner_kind, marked.owner_key) == _OTHER_OWNER
        DeletionTicket.objects.filter(pk=marked.pk).update(
            content_unrecoverable=True)

        service.restore_content(user_principal(user), parent)

        assert not DeletionTicket.objects.filter(pk=parent.pk).exists()
        marked.refresh_from_db()
        assert marked.parent_id is None
        assert marked.content_unrecoverable is True
        assert (marked.owner_kind, marked.owner_key) == _OTHER_OWNER
        assert service.may_restore(marked) is False
        assert AuditEvent.objects.filter(
            action=CONTENT_RESTORED, target_type=KIND_DOCUMENT,
            target_key="doc-1").count() == 0

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


class TestTheChildOwnerValidation:
    """`delete_content` validates a child's `owner_kind` against
    `PRINCIPAL_KINDS` (plus the blank pair) immediately before writing
    its ticket -- the minimum fix for a `children` resolver that
    transposes the owner pair, which used to write silently and mint a
    ticket nobody could ever see."""

    def test_a_transposed_owner_pair_raises_instead_of_minting_a_ticket(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns", handler=f"{__name__}.conversation_handler",
            children=f"{__name__}.transposed_owner_children"))
        user = make_user()
        item = _owner(user)

        with pytest.raises(ValueError, match="owner_kind"):
            service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                   key=item.pk, owner=item)

        # NOTHING WAS WRITTEN: the whole delete is one transaction, so
        # the parent's own ticket the call started to write is rolled
        # back along with the child that failed validation. If the
        # validation only warned instead of raising, or ran after the
        # write, this would find a ticket -- the parent's, the child's,
        # or both.
        assert DeletionTicket.objects.count() == 0

    def test_an_ordinary_owner_still_writes_the_child_exactly_as_before(self):
        """The non-vacuous other half of the assertion above: a VALID
        owner_kind must NOT be refused. Without this, a validation bug
        that rejected every pair -- not only a transposed one -- would
        pass the test above and break every ordinary delete with
        children, undetected."""
        user = make_user()
        item = _owner(user)

        service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                               key=item.pk, owner=item)

        assert DeletionTicket.objects.filter(
            kind=KIND_DOCUMENT, owner_kind="user",
            owner_key=str(user.pk)).count() == 2

    def test_a_genuinely_blank_owner_pair_is_still_the_one_exception(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns", handler=f"{__name__}.conversation_handler",
            children=f"{__name__}.blank_owner_children"))
        user = make_user()
        item = _owner(user)

        service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                               key=item.pk, owner=item)

        assert DeletionTicket.objects.filter(
            kind=KIND_DOCUMENT, key="doc-1", owner_kind="", owner_key="").exists()


class TestTheBlankOwnerRuling:
    """Owner ruling (2026-09-28): a child ticket with GENUINELY BLANK
    owner columns is treated as belonging to the conversation's own
    owner, so an explicit permanent delete destroys it exactly as it
    always did before ownership was tracked at all. This is deliberately
    NOT `may_purge`'s own `sees_all_content or may_read_owned_row`
    mirror -- that would hand a content-reading administrator power over
    other people's genuinely OWNED content, the shape this ruling exists
    to deny -- so the second test below pins that a REAL, DIFFERENT
    owner is still skipped even for an administrator who reads content."""

    def test_a_blank_owner_child_is_destroyed_by_the_conversations_owner(self):
        """If the blank-owner allowance were missing (the gate reading
        `may_read_owned_row(actor, child)` alone), no principal could
        ever match `("", "")` and this permanent delete would silently
        skip `doc-1` on every posture -- `"document:doc-1"` would never
        reach `REMOVED` and the ticket would survive, detached, forever."""
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.conversation",
            label="Conversation and turns",
            handler=f"{__name__}.conversation_handler",
            children=f"{__name__}.blank_owner_children"))
        user = make_user()
        item = _owner(user)
        parent = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=item.pk, owner=item)
        child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")
        assert (child.owner_kind, child.owner_key) == ("", "")

        service.purge_ticket(user_principal(user), parent)

        assert "document:doc-1" in REMOVED
        assert not DeletionTicket.objects.filter(pk=child.pk).exists()

    def test_a_child_owned_by_someone_else_is_still_skipped_by_a_content_reading_admin(self):
        """The ruling's other half, restated for the case it exists to
        deny: an administrator with `admin_sees_content` on may READ
        another member's content but must not gain the power to DESTROY
        it through somebody else's conversation. If the blank-owner
        check above had been implemented as `sees_all_content or may_
        read_owned_row` instead of its own narrow condition, this admin
        would destroy `_OTHER_OWNER`'s real, non-blank-owned child and
        both assertions below would fail."""
        with posture("personal"):
            register_retention_handler(RetentionHandler(
                kind=KIND_CONVERSATION, key="t.conversation",
                label="Conversation and turns",
                handler=f"{__name__}.conversation_handler",
                children=f"{__name__}.cross_owner_children"))
            admin = make_admin()
            row = IdentitySettings.get_solo()
            row.admin_sees_content = True
            row.save()
            item = _owner(admin)
            parent = service.delete_content(
                user_principal(admin), kind=KIND_CONVERSATION, key=item.pk, owner=item)
            child = DeletionTicket.objects.get(kind=KIND_DOCUMENT, key="doc-1")

            service.purge_ticket(user_principal(admin), parent)

            assert not DeletionTicket.objects.filter(pk=parent.pk).exists()
            child.refresh_from_db()
            assert child.parent_id is None
            assert (child.owner_kind, child.owner_key) == _OTHER_OWNER
            assert "document:doc-1" not in REMOVED


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
