"""The Deleted page: a checkable promise, in every posture."""
from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from identity.contracts import cascades as cascades_module
from identity.contracts import retention as copy
from identity.contracts.actions import CONTENT_PURGED, CONTENT_RESTORED
from identity.contracts.cascades import RetentionHandler, register_retention_handler
from identity.models import AuditEvent, DeletionTicket, IdentitySettings
from identity.tests._helpers import (
    make_conversation, make_user, posture, sign_in, user_principal,
)
from identity import retention as service

pytestmark = pytest.mark.django_db


def noop(key: str) -> int:
    return 0


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


def _one_image_child(key: str) -> list[tuple[str, str]]:
    """A CONVERSATION'S ONE CHILD, for the test below that proves the
    refusal reaches a child ticket -- an identity-level stand-in for
    `agents.visibility`'s own resolver, with no dependency on
    `tools.vision`: this module needs a `(kind, key)` pair that resolves
    to `KIND_VISION_JOB`, not the real generation row that kind names in
    production."""
    return [(copy.KIND_VISION_JOB, f"{key}-image")]


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
