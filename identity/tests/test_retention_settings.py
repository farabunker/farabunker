"""The Retention section: three fields, one writer, one audit action."""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity import services
from identity.contracts import retention as copy
from identity.contracts.actions import RETENTION_POLICY_CHANGED
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.models import AuditEvent, IdentitySettings
from identity.tests._helpers import make_admin, posture, sign_in

pytestmark = pytest.mark.django_db


class TestTheWriter:
    def test_it_writes_all_three_fields(self):
        services.set_posture(OPEN_PRINCIPAL, retention_days=7,
                             queue_retention_days=None, audit_detail=True)
        row = IdentitySettings.get_solo()
        assert (row.retention_days, row.queue_retention_days, row.audit_detail) \
            == (7, None, True)

    def test_one_event_per_field_changed_naming_the_field(self):
        services.set_posture(OPEN_PRINCIPAL, retention_days=7, audit_detail=True)
        events = AuditEvent.objects.filter(action=RETENTION_POLICY_CHANGED)
        assert {e.detail["field"] for e in events} == {"retention_days", "audit_detail"}
        assert events.count() == 2

    def test_an_unchanged_field_writes_no_event(self):
        services.set_posture(OPEN_PRINCIPAL, retention_days=7)
        services.set_posture(OPEN_PRINCIPAL, retention_days=7)
        assert AuditEvent.objects.filter(action=RETENTION_POLICY_CHANGED).count() == 1

    @pytest.mark.parametrize("days", [-1, copy.RETENTION_DAYS_MAX + 1, 2**31])
    def test_an_out_of_range_retention_is_refused_before_save(self, days):
        with pytest.raises(services.ServiceRefused):
            services.set_posture(OPEN_PRINCIPAL, retention_days=days)
        assert IdentitySettings.get_solo().retention_days \
            == copy.RETENTION_DAYS_DEFAULT

    def test_zero_is_legal_for_the_content_cliff(self):
        services.set_posture(OPEN_PRINCIPAL, retention_days=0)
        assert IdentitySettings.get_solo().retention_days == 0

    def test_zero_is_illegal_for_the_queue_cliff_because_blank_is_how_no_cliff_is_said(self):
        with pytest.raises(services.ServiceRefused, match="Leave it blank"):
            services.set_posture(OPEN_PRINCIPAL, queue_retention_days=0)

    def test_blank_is_how_no_queue_cliff_is_expressed_and_round_trips(self):
        services.set_posture(OPEN_PRINCIPAL, queue_retention_days=None)
        assert IdentitySettings.get_solo().queue_retention_days is None
        services.set_posture(OPEN_PRINCIPAL, queue_retention_days=5)
        assert IdentitySettings.get_solo().queue_retention_days == 5


class TestThePage:
    def test_the_section_renders_the_three_labels_in_plain_words(self, client):
        with posture("personal"):
            admin = make_admin()
            sign_in(client, admin)
            body = client.get(reverse("identity-settings")).content.decode()
        assert copy.LABEL_RETENTION_DAYS in body
        assert copy.LABEL_QUEUE_RETENTION_DAYS in body
        assert copy.LABEL_AUDIT_DETAIL in body

    def test_the_retention_section_never_says_ticket_cliff_or_sweep(self, client):
        """SCOPED TO THIS PAGE'S OWN CONTENT, not the whole response: the
        settings shell, the sidebar and the assistant panel are shared
        markup this page does not own, and a substring assertion over
        them would fail for a word some other surface introduced."""
        with posture("personal"):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-settings")).content.decode()
        main = body.split("<main>", 1)[1].split("</main>", 1)[0].lower()
        for jargon in ("ticket", "cliff", "sweep"):
            assert jargon not in main

    def test_a_save_round_trips_both_expressible_extremes(self, client):
        # THE ASSERTION MUST BE INSIDE THE `with` BLOCK: `posture()`
        # restores the settings row to a snapshot taken BEFORE the POST
        # on exit -- via a full `row.save()`, not `update_fields` -- so
        # any field this test's own POST changed would be wiped out by
        # the very context manager that set the posture up, the same
        # reason `TestTheThreeRefusals` in `test_settings_page.py` reads
        # `IdentitySettings.get_solo()` before its `with` block ends.
        with posture("personal"):
            admin = make_admin()
            sign_in(client, admin)
            client.post(reverse("identity-settings"), {
                "posture": "personal", "library_posture": "open",
                "session_idle_minutes": "0",
                "retention_days": "0", "queue_retention_days": "",
            })
            row = IdentitySettings.get_solo()
            assert row.retention_days == 0
            assert row.queue_retention_days is None

    def test_an_out_of_range_value_flashes_and_redirects_never_500s(self, client):
        with posture("personal"):
            sign_in(client, make_admin())
            response = client.post(reverse("identity-settings"), {
                "posture": "personal", "library_posture": "open",
                "session_idle_minutes": "0", "retention_days": "99999",
            })
        assert response.status_code == 302
        assert IdentitySettings.get_solo().retention_days \
            == copy.RETENTION_DAYS_DEFAULT
