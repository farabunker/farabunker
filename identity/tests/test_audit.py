"""`identity/audit.py` -- the one writer, and the only module in the
codebase that may touch `AuditEvent.objects` at all.
"""
from __future__ import annotations

import pytest

from identity import audit
from identity.contracts import actions
from identity.contracts.principals import ANONYMOUS, OPEN_PRINCIPAL
from identity.models import AuditEvent
from identity.tests._helpers import make_admin, user_principal

pytestmark = pytest.mark.django_db


class TestRecord:
    def test_it_writes_one_row_with_the_actor_flattened(self):
        row = audit.record(OPEN_PRINCIPAL, actions.POSTURE_CHANGED,
                           target_type="posture", target_key="1", to="personal")
        assert (row.actor_kind, row.actor_key) == ("open", "box")
        assert row.action == actions.POSTURE_CHANGED
        assert row.detail == {"to": "personal"}

    def test_it_resolves_the_actor_label_at_write_time_and_never_later(self):
        """Denormalised on purpose: an audit line must still read
        correctly after the account is renamed or deactivated, and a
        join that resolves a deleted pk to "unknown" is an audit trail
        that forgets."""
        admin = make_admin(username="ann")
        row = audit.record(user_principal(admin), actions.LOGIN)
        admin.username = "ann-renamed"
        admin.save()
        assert AuditEvent.objects.get(pk=row.pk).actor_label == "ann"

    def test_the_open_principal_gets_a_readable_label_too(self):
        row = audit.record(OPEN_PRINCIPAL, actions.ADOPTED)
        assert row.actor_label

    def test_an_anonymous_actor_is_recordable(self):
        """`identity.login_failed` is written before anybody is signed
        in, so the writer must accept the sentinel."""
        row = audit.record(ANONYMOUS, actions.LOGIN_FAILED, target_label="ann")
        assert row.actor_kind == "anonymous"
        assert row.target_label == "ann"

    def test_a_login_failure_records_the_username_and_nothing_else(self):
        """It exists so a brute-force attempt is visible. It records the
        SUBMITTED username and never the password, never a hash of it,
        and never the request body."""
        row = audit.record(ANONYMOUS, actions.LOGIN_FAILED, target_label="ann")
        serialised = f"{row.detail}{row.target_label}{row.target_key}"
        assert "password" not in serialised.lower()

    def test_an_unknown_action_is_refused_by_the_model(self):
        with pytest.raises(ValueError):
            audit.record(OPEN_PRINCIPAL, "identity.invented_this")


class TestReaders:
    def test_recent_is_newest_first_and_honours_its_limit(self):
        rows = [audit.record(OPEN_PRINCIPAL, actions.LOGIN, target_key=str(i))
                for i in range(5)]
        found = audit.recent(limit=3)
        assert [r.pk for r in found] == [rows[4].pk, rows[3].pk, rows[2].pk]

    def test_for_target_filters_by_target_and_returns_nothing_for_another(self):
        audit.record(OPEN_PRINCIPAL, actions.USER_CREATED,
                     target_type="user", target_key="1")
        row2 = audit.record(OPEN_PRINCIPAL, actions.USER_CREATED,
                             target_type="user", target_key="2")
        found = audit.for_target("user", "2")
        assert [r.pk for r in found] == [row2.pk]
        assert audit.for_target("user", "3") == []
