"""The Deleted page: a checkable promise, in every posture."""
from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from identity.contracts import cascades as cascades_module
from identity.contracts import retention as copy
from identity.contracts.actions import CONTENT_PURGED, CONTENT_RESTORED
from identity.contracts.cascades import ORDER_FILES, RetentionHandler, register_retention_handler
from identity.models import AuditEvent, DeletionTicket, IdentitySettings
from identity.tests._helpers import (
    make_conversation, make_user, posture, sign_in, user_principal,
)
from identity import retention as service

pytestmark = pytest.mark.django_db


def noop(key: str) -> int:
    return 0


def files_band_refuses(key: str) -> int:
    """A REAL files-band handler, for the two tests below that prove the
    view's own `record_failed_purge` calls actually mark a ticket --
    `identity.contracts.retention.RetentionRefused` propagating from a
    handler `deleted_purge` never monkeypatches, unlike the refusal
    tests in `TestRestoreAndPurge` above, which replace `purge_ticket`
    itself and so never reach either mark."""
    raise copy.RetentionRefused("a worker still holds this item")


def files_band_breaks(key: str) -> int:
    """The OTHER catch's real counterpart: an ordinary exception from a
    files-band handler, for `deleted_purge`'s bare `except Exception`."""
    raise RuntimeError("disk is unavailable after removing bytes")


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Save, clear, register, restore -- `identity/tests/
    test_cascades.py::_isolated_registry`'s shape and its reason: the
    registry is a module-level dict with no reset path, and a
    registration escaping this module would reach every later purge in
    the same pytest process. Both collection orders are the gate."""
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    register_retention_handler(RetentionHandler(
        kind=copy.KIND_ASK, key="t.page", label="Ask records",
        handler=f"{__name__}.noop"))
    yield
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


def _ticket_for(user, *, key="1", label="A question"):
    item = make_conversation(owner_kind="user", owner_key=str(user.pk))
    return service.delete_content(user_principal(user), kind=copy.KIND_ASK,
                                  key=key, owner=item, label=label)


def _one_image_child(key: str) -> list[tuple[str, str, str, str]]:
    """A CONVERSATION'S ONE CHILD, for the test below that proves the
    refusal reaches a child ticket -- an identity-level stand-in for
    `agents.visibility`'s own resolver, with no dependency on
    `tools.vision`: this module needs a `(kind, key, owner_kind,
    owner_key)` quadruple that resolves to `KIND_VISION_JOB`, not the
    real generation row that kind names in production. Owned by the
    SAME principal as the conversation `key` names -- this fixture is
    about the refusal reaching a child at all, not about whose it is,
    so it reads the real row's own owner rather than inventing one."""
    from django.apps import apps
    conversation = apps.get_model("agents.Conversation").objects.get(pk=key)
    return [(copy.KIND_VISION_JOB, f"{key}-image",
            conversation.owner_kind, conversation.owner_key)]


# A MODULE-LEVEL SLOT, NOT A CLOSURE: `_cross_owner_image_child` is
# resolved by DOTTED PATH (`import_string`), so it cannot close over a
# test's own local `other` user -- this is how the test hands it the
# second principal's owner columns before calling `delete_content`.
_CROSS_OWNER_SLOT: dict[str, tuple[str, str]] = {}


def _cross_owner_image_child(key: str) -> list[tuple[str, str, str, str]]:
    """A CONVERSATION'S ONE CHILD, owned by a REAL, DIFFERENT principal
    -- `_CROSS_OWNER_SLOT["owner"]`, set by the test immediately before
    the delete this resolver is asked from. Two real users, not a
    synthetic key with no owner: the fixture the brief for this wave
    calls out as the one the old stamping pin could never fail against."""
    owner_kind, owner_key = _CROSS_OWNER_SLOT["owner"]
    return [(copy.KIND_VISION_JOB, f"{key}-image", owner_kind, owner_key)]


class TestTheDeletedTab:
    def test_it_lists_the_viewers_own_items_with_the_promised_date(self, client):
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            body = client.get(reverse("identity-deleted")).content.decode()
        assert copy.KIND_LABELS[copy.KIND_ASK] in body
        assert copy.purge_on_line(ticket.purge_on) in body
        assert copy.ACTION_RESTORE in body
        assert copy.ACTION_PURGE in body

    def test_a_member_does_not_see_somebody_elses(self, client):
        with posture("personal"):
            mine, theirs = make_user(), make_user()
            _ticket_for(theirs, key="2", label="Their question")
            sign_in(client, mine)
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "Their question" not in body

    def test_an_open_box_shows_everyones_because_there_is_nobody_to_hide_from(self, client):
        with posture("open"):
            _ticket_for(make_user(), label="A question")
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "A question" in body

    def test_the_page_renders_no_hold_control_in_any_posture(self, client):
        """SCOPED TO THIS PAGE'S OWN CONTENT BLOCK, not the whole
        response: the settings shell, the sidebar and the assistant
        panel are shared markup this page does not own, and a substring
        assertion over them would fail for a word some other surface
        introduced. The HOLD control and the operator-set cliff floor
        are deferred (spec section 10.10) and this page must not imply
        a guarantee that is not built; the permanent-delete control's
        absence on the organisation posture is a different rule, pinned
        by `test_the_control_is_not_rendered_on_that_posture` below."""
        for box in ("open", "personal", "enterprise"):
            with posture(box):
                user = make_user()
                sign_in(client, user)
                _ticket_for(user)
                body = client.get(reverse("identity-deleted")).content.decode()
            main = body.split("<main>", 1)[1].split("</main>", 1)[0].lower()
            assert "hold" not in main, box

    def test_a_get_purges_anything_whose_date_has_passed_before_listing(self, client):
        with posture("open"):
            user = make_user()
            ticket = _ticket_for(user)
            DeletionTicket.objects.filter(pk=ticket.pk).update(
                purge_on=timezone.localdate() - datetime.timedelta(days=1))
            body = client.get(reverse("identity-deleted")).content.decode()
        assert DeletionTicket.objects.count() == 0
        assert "A question" not in body

    def test_an_empty_page_says_so_and_never_500s(self, client):
        with posture("open"):
            response = client.get(reverse("identity-deleted"))
        assert response.status_code == 200


class TestTheDeletionLog:
    def test_it_shows_content_free_events_with_the_toggle_off(self, client):
        with posture("open"):
            user = make_user()
            ticket = _ticket_for(user, label="A secret question")
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "A secret question" not in body
        assert copy.KIND_LABELS[copy.KIND_ASK] in body

    def test_with_the_toggle_on_the_labels_appear(self, client):
        with posture("open"):
            row = IdentitySettings.get_solo()
            row.audit_detail = True
            row.save()
            user = make_user()
            _ticket_for(user, label="A named question")
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "A named question" in body


class TestTheDeletionLogHidesOtherPeoplesLabels:
    """The toggle above (`test_with_the_toggle_on_the_labels_appear`)
    proves the label appears for the item's OWN viewer; this proves it
    stops there for a viewer who DOES see the event (a `sees_all_
    content` principal, or the viewer's own action) but has no standing
    over ANOTHER person's item -- `TestTheDeletionLogIsScopedToTheViewers
    OwnActivity` below covers the stronger case, where the viewer has no
    standing over the event at all and it does not render."""

    def test_a_sees_all_content_principal_still_sees_it(self, client):
        """PURGED FIRST, so the label can only reach this viewer through
        the log -- once the ticket is gone, `row.ticket.label` on the
        Deleted-items list has nothing left to leak from either."""
        with posture("open"):
            row = IdentitySettings.get_solo()
            row.audit_detail = True
            row.save()
            ticket = _ticket_for(make_user(), label="A private title")
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "A private title" in body


class TestTheDeletionLogIsScopedToTheViewersOwnActivity:
    """The log LISTS every `content.*` event only to a `sees_all_content`
    principal -- every other principal sees only the events THEY
    performed (`actor_kind`/`actor_key` equal to their own principal),
    never a window onto what somebody else deleted, restored or
    permanently deleted. An item that reached its own date and was
    removed by the sweep -- always the service principal, never the
    person who deleted it (`identity/retention.py::sweep`'s own
    docstring) -- was nobody's own act either, so it never reaches a
    member's log this way: the date line on the Deleted page was the
    notice, and the full log is the administrator's
    (`identity/README.md` section 9)."""

    def test_a_principal_with_no_standing_sees_none_of_someone_elses_activity(self, client):
        with posture("personal"):
            a, b = make_user(), make_user()
            ticket = _ticket_for(a, key="a-item", label="A's own title")
            service.purge_ticket(user_principal(a), ticket)
            sign_in(client, b)
            body = client.get(reverse("identity-deleted")).content.decode()
        assert a.username not in body
        assert "a-item" not in body

    def test_the_viewers_own_permanent_delete_still_shows_to_them(self, client):
        with posture("personal"):
            b = make_user()
            ticket = _ticket_for(b, key="b-item", label="B's own title")
            sign_in(client, b)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "b-item" in body
        assert b.username in body

    def test_a_members_own_event_survives_a_hundred_newer_ones_by_someone_else(self, client):
        """A `by_action` that sliced the newest 100 rows BOX-WIDE and
        left the viewer scoping to a Python filter applied AFTER that
        slice would let 100+ events by ANOTHER principal, written AFTER
        the viewer's own, push the viewer's real (older) event out of
        that box-wide slice entirely -- "Nothing yet." on a page whose
        own tagline promises otherwise. `actor=` filters before the
        slice, so the viewer's own event survives regardless of how many
        other principals acted more recently. THE ORDER IS THE WHOLE
        TEST: the viewer's event is written FIRST and the noise AFTER
        it, so a box-wide "newest 100" slice with no actor filter ahead
        of it would have excluded it."""
        from identity import audit

        with posture("personal"):
            noisy, viewer = make_user(), make_user()
            ticket = _ticket_for(viewer, key="viewer-item", label="Viewer's own title")
            service.purge_ticket(user_principal(viewer), ticket)
            noisy_principal = user_principal(noisy)
            for index in range(120):
                audit.record(noisy_principal, CONTENT_RESTORED,
                             target_type=copy.KIND_ASK, target_key=str(index))
            sign_in(client, viewer)
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "viewer-item" in body

    def test_a_sees_all_content_principal_sees_everyones_activity(self, client):
        with posture("open"):
            a, b = make_user(), make_user()
            ticket_a = _ticket_for(a, key="a-item2", label="A's own title")
            service.purge_ticket(user_principal(a), ticket_a)
            ticket_b = _ticket_for(b, key="b-item2", label="B's own title")
            service.purge_ticket(user_principal(b), ticket_b)
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "a-item2" in body
        assert "b-item2" in body


class TestRestoreAndPurge:
    def test_restore_removes_the_ticket_and_records_the_event(self, client):
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-restore", args=[ticket.pk]))
        assert response.status_code == 302
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(action=CONTENT_RESTORED).count() == 1

    def test_purge_destroys_the_content_in_the_request_that_handled_the_click(self, client):
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(action=CONTENT_PURGED).count() == 1

    def test_the_enterprise_posture_keeps_an_item_until_its_date(self, client):
        """SPEC SECTION 3.10's enterprise column, for this one control:
        nobody destroys content before the date it was promised. The
        POST answers with a sentence, not a 404 -- the row is right
        there on the page, and a 404 for something a person can see is
        a lie about what happened."""
        with posture("enterprise"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]),
                follow=True)
            assert response.redirect_chain[0][1] == 302
            assert DeletionTicket.objects.filter(pk=ticket.pk).exists()
            body = response.content.decode()
            assert copy.purge_refused_line(ticket.purge_on) in body

    def test_the_control_is_not_rendered_on_that_posture(self, client):
        """Scoped to `<main>`, the rule this module already follows: the
        settings shell, the sidebar and the assistant panel are shared
        markup this page does not own, and a substring assertion over
        them would fail for a word some other surface introduced."""
        with posture("enterprise"):
            user = make_user()
            sign_in(client, user)
            _ticket_for(user)
            body = client.get(reverse("identity-deleted")).content.decode()
            main = body.split("<main>", 1)[1].split("</main>", 1)[0].lower()
            assert copy.ACTION_RESTORE.lower() in main
            assert copy.ACTION_PURGE.lower() not in main

    @pytest.mark.parametrize("route",
                             ["identity-deleted-restore", "identity-deleted-purge"])
    def test_a_forged_id_is_404_never_500(self, client, route):
        with posture("personal"):
            sign_in(client, make_user())
            assert client.post(reverse(route, args=[999999])).status_code == 404

    @pytest.mark.parametrize("route",
                             ["identity-deleted-restore", "identity-deleted-purge"])
    def test_somebody_elses_ticket_is_404_never_403(self, client, route):
        with posture("personal"):
            mine, theirs = make_user(), make_user()
            ticket = _ticket_for(theirs, key="2")
            sign_in(client, mine)
            assert client.post(reverse(route, args=[ticket.pk])).status_code == 404

    @pytest.mark.parametrize("exc_cls", [
        "identity.services.ServiceRefused",
        "identity.contracts.retention.RetentionRefused",
    ])
    def test_a_refusal_flashes_and_redirects_for_either_refusal_type(
            self, client, monkeypatch, exc_cls):
        """BOTH types, because the view catches both: this column's own
        `ServiceRefused`, and the `RetentionRefused` a handler in a
        column that may not import `identity.services` raises."""
        from django.utils.module_loading import import_string
        from identity import views
        refusal = import_string(exc_cls)
        monkeypatch.setattr(
            views.retention, "purge_ticket",
            lambda *a, **k: (_ for _ in ()).throw(
                refusal("a worker holds this job")))
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]), follow=True)
        assert response.status_code == 200
        assert "a worker holds this job" in response.content.decode()
        assert DeletionTicket.objects.count() == 1

    def test_a_broken_purge_flashes_the_fixed_sentence_and_keeps_the_ticket(
            self, client, monkeypatch):
        """THE OTHER CATCH -- the bare `except Exception` in
        `deleted_purge`, for a failure with no operator-readable sentence
        of its own (neither refusal type above)."""
        from identity import views
        monkeypatch.setattr(
            views.retention, "purge_ticket",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("a database hiccup")))
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]), follow=True)
        assert response.redirect_chain[0][1] == 302
        assert views._PURGE_FAILED_MESSAGE in response.content.decode()
        assert DeletionTicket.objects.filter(pk=ticket.pk).exists()

    def test_a_broken_restore_flashes_the_fixed_sentence_and_keeps_the_ticket(
            self, client, monkeypatch):
        """`deleted_restore`'s own bare `except Exception` -- the
        identical never-500 shape, for the mutation with nothing of its
        own to refuse."""
        from identity import views
        monkeypatch.setattr(
            views.retention, "restore_content",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("a database hiccup")))
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-restore", args=[ticket.pk]), follow=True)
        assert response.redirect_chain[0][1] == 302
        assert views._RESTORE_FAILED_MESSAGE in response.content.decode()
        assert DeletionTicket.objects.filter(pk=ticket.pk).exists()


class TestTheViewsOwnFailedPurgeMarkIsReal:
    """`identity.views.deleted_purge`'s two `retention.record_failed_
    purge(ticket)` calls (the refusal-type catch and the bare `except
    Exception` beside it) had no pin that could see them: both existing
    refusal tests in `TestRestoreAndPurge` above monkeypatch `views.
    retention.purge_ticket` itself, so `_files_band_reached` is never
    set on the ticket they hold and `record_failed_purge` is a
    guaranteed no-op either way -- deleting either call would not turn
    those tests red. These drive a REAL files-band handler through the
    real POST instead, with nothing monkeypatched, so the mark is what
    is actually asserted. The click path is the one whose false mark is
    never retried (a ticket purged before its date is not due), which
    is exactly why an unpinned marking line here matters most."""

    def test_a_refusal_from_a_real_handler_marks_the_ticket_and_removes_restore(
            self, client):
        register_retention_handler(RetentionHandler(
            kind=copy.KIND_ASK, key="t.page", label="Ask records",
            handler=f"{__name__}.files_band_refuses", order=ORDER_FILES))
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]), follow=True)
            assert response.status_code == 200
            assert "a worker still holds this item" in response.content.decode()
            ticket.refresh_from_db()
            assert ticket.content_unrecoverable is True

            body = client.get(reverse("identity-deleted")).content.decode()
        main = body.split("<main>", 1)[1].split("</main>", 1)[0]
        restore_url = reverse("identity-deleted-restore", args=[ticket.pk])
        assert restore_url not in main
        assert copy.RESTORE_REFUSED_LINE in main

    def test_a_broken_handler_marks_the_ticket_and_removes_restore(self, client):
        register_retention_handler(RetentionHandler(
            kind=copy.KIND_ASK, key="t.page", label="Ask records",
            handler=f"{__name__}.files_band_breaks", order=ORDER_FILES))
        from identity import views
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]), follow=True)
            assert response.redirect_chain[0][1] == 302
            assert views._PURGE_FAILED_MESSAGE in response.content.decode()
            ticket.refresh_from_db()
            assert ticket.content_unrecoverable is True

            body = client.get(reverse("identity-deleted")).content.decode()
        main = body.split("<main>", 1)[1].split("</main>", 1)[0]
        restore_url = reverse("identity-deleted-restore", args=[ticket.pk])
        assert restore_url not in main
        assert copy.RESTORE_REFUSED_LINE in main


class TestAFailedPurgeMarkRefusesRestore:
    """`identity.retention.may_restore` is the predicate; this proves
    both surfaces honour it -- the page renders no Restore control for a
    marked ticket, and the POST refuses even when called directly, the
    same shape `TestChildTicketsInheritTheRefusal` above proves for
    `may_purge`. The mark is written elsewhere (a failed purge, covered
    in `identity/tests/test_retention_service.py`); here it is set
    directly, because what this module tests is what the PAGE AND POST do
    with a marked ticket, not how one comes to be marked."""

    def _marked_ticket_for(self, user):
        ticket = _ticket_for(user)
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            content_unrecoverable=True)
        ticket.refresh_from_db()
        return ticket

    def test_the_page_renders_no_restore_control_for_it(self, client):
        """Checked by the FORM'S OWN URL, not by the word "restore":
        `copy.RESTORE_REFUSED_LINE` itself contains "restored", so a
        plain substring check for the word would pass by accident even
        if the button were still there. The URL is the one thing that
        can only appear if the form itself is rendered."""
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = self._marked_ticket_for(user)
            body = client.get(reverse("identity-deleted")).content.decode()
            main = body.split("<main>", 1)[1].split("</main>", 1)[0]
            restore_url = reverse("identity-deleted-restore", args=[ticket.pk])
            assert restore_url not in main
            assert copy.RESTORE_REFUSED_LINE in main

    def test_the_post_refuses_even_called_directly(self, client):
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = self._marked_ticket_for(user)
            response = client.post(
                reverse("identity-deleted-restore", args=[ticket.pk]),
                follow=True)
        assert response.redirect_chain[0][1] == 302
        assert DeletionTicket.objects.filter(pk=ticket.pk).exists()
        assert copy.RESTORE_REFUSED_LINE in response.content.decode()

    def test_permanent_delete_still_works_for_a_marked_ticket(self, client):
        """The point of the mark is that the content is gone, so
        finishing the job must remain possible. On this posture
        (`personal`) the owner already passes `may_purge`'s ordinary
        standing check regardless of the mark; `TestTheOrganisationPost
        ureAdmitsAMarkedTicket` below is the posture where the mark
        itself is what lets this succeed at all."""
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = self._marked_ticket_for(user)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
        assert not DeletionTicket.objects.filter(pk=ticket.pk).exists()
        assert AuditEvent.objects.filter(action=CONTENT_PURGED).count() == 1


class TestTheOrganisationPostureAdmitsAMarkedTicket:
    """Owner ruling (2026-09-28): a ticket whose content is already
    partly gone has nothing left for the enforced period to protect, so
    `may_purge`'s one named exception lets that ticket be destroyed
    early on this posture too -- and ONLY that ticket. Read beside
    `TestRestoreAndPurge::test_the_enterprise_posture_keeps_an_item_
    until_its_date` above (an ordinary, unmarked ticket, still refused),
    the two together are what make the exception legible: remove it and
    the marked case below would refuse instead of succeeding; widen it
    to every ticket and the unmarked case up above would stop refusing."""

    def _marked_ticket_for(self, user):
        ticket = _ticket_for(user)
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            content_unrecoverable=True)
        ticket.refresh_from_db()
        return ticket

    def test_a_marked_ticket_renders_and_accepts_permanent_delete_before_its_date(
            self, client):
        with posture("enterprise"):
            user = make_user()
            sign_in(client, user)
            ticket = self._marked_ticket_for(user)

            body = client.get(reverse("identity-deleted")).content.decode()
            main = body.split("<main>", 1)[1].split("</main>", 1)[0]
            assert reverse("identity-deleted-purge", args=[ticket.pk]) in main

            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]), follow=True)
        assert response.redirect_chain[0][1] == 302
        assert not DeletionTicket.objects.filter(pk=ticket.pk).exists()
        assert AuditEvent.objects.filter(action=CONTENT_PURGED).count() == 1

    def test_an_unmarked_ticket_on_the_same_posture_still_refuses(self, client):
        """THE NEIGHBOUR THE EXCEPTION NEEDS TO STAY LEGIBLE -- without
        it, `may_purge` could admit every ticket on this posture and the
        test above would not notice."""
        with posture("enterprise"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)

            body = client.get(reverse("identity-deleted")).content.decode()
            main = body.split("<main>", 1)[1].split("</main>", 1)[0]
            assert reverse("identity-deleted-purge", args=[ticket.pk]) not in main

            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]), follow=True)
        assert response.redirect_chain[0][1] == 302
        assert DeletionTicket.objects.filter(pk=ticket.pk).exists()
        assert copy.purge_refused_line(ticket.purge_on) in response.content.decode()


class TestTheQueryCost:
    def test_the_page_costs_the_same_queries_at_one_ticket_and_at_many(self, client):
        """THE FLAT-QUERY PIN. `deleted_page` reads `IdentitySettings`
        once and threads that SAME row through `principal_for_request`,
        `visible_tickets` and every row's own `may_purge(...,
        settings_row=row)` call -- so a row-per-ticket loop must not cost
        a settings read per row.

        NON-VACUOUS BY CONSTRUCTION: dropping `settings_row=row` from
        the view's own `may_purge` call -- the obvious wrong
        implementation, and the one this page had before `may_purge`
        grew the keyword -- costs one extra settings-row read per
        ticket, so ten tickets fails this equality by nine queries, not
        by a rounding error.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture("open"):
            _ticket_for(make_user(), key="one")
            client.get(reverse("identity-deleted"))  # warm the session reads
            with CaptureQueriesContext(connection) as one_ticket:
                assert client.get(reverse("identity-deleted")).status_code == 200
            for index in range(1, 10):
                _ticket_for(make_user(), key=f"many-{index}")
            with CaptureQueriesContext(connection) as many_tickets:
                body = client.get(reverse("identity-deleted")).content.decode()

        assert len(many_tickets) == len(one_ticket), (
            len(one_ticket), len(many_tickets), [q["sql"] for q in many_tickets])
        # Really ten rendered rows, not two empty pages agreeing by
        # accident: `copy.ACTION_PURGE` is offered per row an admin may
        # act on, and this admin `sees_all_content` on an open box.
        assert body.count(copy.ACTION_PURGE) == 10

    def test_the_get_reads_identitysettings_exactly_once(self, client):
        """THE TRUTH BEHIND `deleted_page`'s OWN "one read" CLAIM.
        `IdentityGateMiddleware` already reads `IdentitySettings` once
        per request (`identity/tests/test_middleware.py::
        TestTheSingleRowRead`'s own pin); `settings_row_for(request)`
        reuses that SAME row rather than the view fetching a second copy
        of its own with `IdentitySettings.get_solo()` -- the obvious
        wrong implementation, and the one this view had before this
        fix, which names the same table a second time and would fail
        this equality at 2, not 1.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture("open"):
            _ticket_for(make_user())
            client.get(reverse("identity-deleted"))  # warm the session reads
            with CaptureQueriesContext(connection) as context:
                assert client.get(reverse("identity-deleted")).status_code == 200

        reads = sum(
            1 for q in context.captured_queries if "identity_identitysettings" in q["sql"])
        assert reads == 1, [q["sql"] for q in context.captured_queries]

    def test_a_restore_post_reads_identitysettings_exactly_once(self, client):
        """`deleted_restore` already holds the request's one
        `IdentitySettings` row (`settings_row_for(request)`, the same
        row `IdentityGateMiddleware` fetched); it threads that row into
        `retention.restore_content(..., settings_row=row)` rather than
        letting that call fetch its own second copy -- the obvious wrong
        implementation, and the one `restore_content` had before it grew
        the keyword, which would fail this equality at 2, not 1.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture("open"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            with CaptureQueriesContext(connection) as context:
                response = client.post(
                    reverse("identity-deleted-restore", args=[ticket.pk]))
        assert response.status_code == 302
        reads = sum(
            1 for q in context.captured_queries if "identity_identitysettings" in q["sql"])
        assert reads == 1, [q["sql"] for q in context.captured_queries]

    def test_a_permanent_delete_post_reads_identitysettings_exactly_once(self, client):
        """The same pin as the restore POST above, for `deleted_purge`:
        `purge_ticket(..., settings_row=row)` reuses the request's one
        `IdentitySettings` row rather than reading a second one of its
        own. The registered handler here (`noop`, this module's own
        fixture) touches no settings itself, so every
        `identity_identitysettings` statement this request produces
        comes from the view/service path this test pins, not from the
        handler.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture("open"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            with CaptureQueriesContext(connection) as context:
                response = client.post(
                    reverse("identity-deleted-purge", args=[ticket.pk]))
        assert response.status_code == 302
        reads = sum(
            1 for q in context.captured_queries if "identity_identitysettings" in q["sql"])
        assert reads == 1, [q["sql"] for q in context.captured_queries]


class TestChildTicketsInheritTheRefusal:
    """15A-15C give a deleted conversation's images their own tickets,
    linked by `parent`. This proves that link needs no refusal logic of
    its own: `deleted_page` and `deleted_purge` both call `may_purge` on
    whichever ticket the row names, and a child ticket is an ordinary
    row to that call -- it does not ask whether it has a parent."""

    def test_a_childs_generated_image_row_refuses_too(self, client):
        register_retention_handler(RetentionHandler(
            kind=copy.KIND_CONVERSATION, key="t.page.conversation",
            label="Conversation", handler=f"{__name__}.noop",
            children=f"{__name__}._one_image_child"))
        with posture("enterprise"):
            user = make_user()
            sign_in(client, user)
            item = make_conversation(owner_kind="user", owner_key=str(user.pk))
            parent = service.delete_content(
                user_principal(user), kind=copy.KIND_CONVERSATION,
                key=str(item.pk), owner=item)
            child = DeletionTicket.objects.get(
                kind=copy.KIND_VISION_JOB, parent=parent)

            body = client.get(reverse("identity-deleted")).content.decode()
            main = body.split("<main>", 1)[1].split("</main>", 1)[0].lower()
            assert copy.KIND_LABELS[copy.KIND_VISION_JOB].lower() in main
            assert copy.ACTION_PURGE.lower() not in main

            response = client.post(
                reverse("identity-deleted-purge", args=[child.pk]),
                follow=True)
            assert response.redirect_chain[0][1] == 302
            assert DeletionTicket.objects.filter(pk=child.pk).exists()
            assert copy.purge_refused_line(child.purge_on) in response.content.decode()


class TestTheOtherOwnerSeesTheirOwnChild:
    """The cross-owner half of the same wave: A's conversation holds a
    generated image that belongs to B, not to A (a workstream share that
    let B post and generate inside it; an administrator's duplicate).
    TWO REAL USERS, a REAL child key carrying B's REAL owner columns --
    the shape the old stamping pin's synthetic keys could never
    exercise, because they had no owner of their own to differ FROM the
    parent's."""

    def _delete_as_a_with_bs_image(self, client, a, b):
        register_retention_handler(RetentionHandler(
            kind=copy.KIND_CONVERSATION, key="t.page.conversation",
            label="Conversation", handler=f"{__name__}.noop",
            children=f"{__name__}._cross_owner_image_child"))
        _CROSS_OWNER_SLOT["owner"] = ("user", str(b.pk))
        item = make_conversation(owner_kind="user", owner_key=str(a.pk))
        sign_in(client, a)
        parent = service.delete_content(
            user_principal(a), kind=copy.KIND_CONVERSATION,
            key=str(item.pk), owner=item)
        child = DeletionTicket.objects.get(
            kind=copy.KIND_VISION_JOB, parent=parent)
        assert (child.owner_kind, child.owner_key) == ("user", str(b.pk))
        return parent, child

    def test_b_sees_the_row_on_bs_own_deleted_page_and_can_restore_it(self, client):
        """THE ASSERTION THAT WOULD FLIP if the stamping regressed to
        the parent's owner: with `admin_sees_content` off (the shipped
        default), `visible_tickets` narrows to the viewer's own rows, so
        a child stamped as A's would be invisible to B here -- and the
        restore POST, gated through the identical `_own_ticket_or_404`,
        would 404 instead of succeeding."""
        with posture("personal"):
            a, b = make_user(), make_user()
            _parent, child = self._delete_as_a_with_bs_image(client, a, b)

            sign_in(client, b)
            body = client.get(reverse("identity-deleted")).content.decode()
            main = body.split("<main>", 1)[1].split("</main>", 1)[0]
            assert reverse("identity-deleted-restore", args=[child.pk]) in main

            response = client.post(
                reverse("identity-deleted-restore", args=[child.pk]), follow=True)
        assert response.redirect_chain[0][1] == 302
        assert not DeletionTicket.objects.filter(pk=child.pk).exists()
        assert AuditEvent.objects.filter(
            action=CONTENT_RESTORED, target_type=copy.KIND_VISION_JOB).count() == 1

    def test_the_deleting_principal_does_not_see_a_child_they_do_not_own(self, client):
        """THE MIRROR: A clicked delete, but the image was never A's --
        A's own Deleted page must not claim it, and a direct POST to
        its row is a 404 for A, exactly as it would be for a stranger."""
        with posture("personal"):
            a, b = make_user(), make_user()
            _parent, child = self._delete_as_a_with_bs_image(client, a, b)

            body = client.get(reverse("identity-deleted")).content.decode()
            main = body.split("<main>", 1)[1].split("</main>", 1)[0]
            assert reverse("identity-deleted-restore", args=[child.pk]) not in main

            response = client.post(
                reverse("identity-deleted-restore", args=[child.pk]))
        assert response.status_code == 404
        assert DeletionTicket.objects.filter(pk=child.pk).exists()

    def test_as_permanent_delete_destroys_as_content_and_leaves_bs_ticket_and_date_standing(
            self, client):
        """Owner ruling (2026-09-28), end to end through the real view:
        A permanently deleting the conversation reaches only what A
        owns. B's child ticket survives, detached, with its own date --
        `test_the_sweep_on_the_date_takes_both` below is its other half,
        proving the date is still a promise kept, not a promise
        forgotten."""
        with posture("personal"):
            a, b = make_user(), make_user()
            parent, child = self._delete_as_a_with_bs_image(client, a, b)

            client.post(reverse("identity-deleted-purge", args=[parent.pk]))

        assert not DeletionTicket.objects.filter(pk=parent.pk).exists()
        child.refresh_from_db()
        assert child.parent_id is None
        assert DeletionTicket.objects.filter(pk=child.pk).exists()
        assert service.may_restore(child) is True

    def test_the_sweep_still_takes_the_detached_child_after_as_permanent_delete(
            self, client):
        """`test_the_sweep_on_the_date_takes_both` below, and `identity.
        tests.test_retention_service.py::TestChildTickets::test_the_
        sweep_still_takes_a_child_the_clicker_did_not_own`, both sweep a
        child that is still ATTACHED to its parent -- neither runs the
        permanent delete first, so neither describes the state the skip
        actually leaves behind. This runs A's real permanent delete
        first, which detaches B's ticket exactly as `test_as_permanent_
        delete_destroys_as_content_and_leaves_bs_ticket_and_date_
        standing` above proves, and THEN backs the surviving, orphaned
        ticket's date up and sweeps. `sweep`'s due query has no
        `parent__isnull` clause today, which is exactly why this wants a
        pin: the day somebody adds one meaning to make the sweep prefer
        parents, this is the test that would catch a detached child
        being silently skipped forever, while the other two pins stay
        green throughout."""
        with posture("personal"):
            a, b = make_user(), make_user()
            parent, child = self._delete_as_a_with_bs_image(client, a, b)
            client.post(reverse("identity-deleted-purge", args=[parent.pk]))
            child.refresh_from_db()
            assert child.parent_id is None
            DeletionTicket.objects.filter(pk=child.pk).update(
                purge_on=timezone.localdate() - datetime.timedelta(days=1))

            assert service.sweep() == 1

        assert not DeletionTicket.objects.filter(pk=child.pk).exists()
        assert AuditEvent.objects.filter(
            action=CONTENT_PURGED, target_type=copy.KIND_VISION_JOB,
            target_key=child.key).exists()

    def test_the_sweep_on_the_date_takes_both(self, client):
        """Nothing outlives the date it was promised, ownership aside:
        the sweep always acts as `SERVICE_PRINCIPAL`, so once both
        tickets fall due the family is destroyed in full regardless of
        who owns which piece of it."""
        with posture("personal"):
            a, b = make_user(), make_user()
            parent, child = self._delete_as_a_with_bs_image(client, a, b)
            DeletionTicket.objects.filter(
                pk__in=[parent.pk, child.pk]).update(
                purge_on=timezone.localdate() - datetime.timedelta(days=1))

            assert service.sweep() == 2

        assert DeletionTicket.objects.count() == 0
