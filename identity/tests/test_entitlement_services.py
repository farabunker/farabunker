"""The guarded writes behind entitlements and grants.

AN OWNER OF E MAY GRANT E, REVOKE E, AND LABEL WITH E -- and nothing
else. Not create, not rename, not delete, not the library posture, not
anybody else's entitlement. That is the whole of owner decision 15, and
every "and nothing else" below is one of the negative tests spec section
18.2's done-when 5 asks for.
"""
from __future__ import annotations

import pytest

from identity import services
from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent, Entitlement, EntitlementGrant
from identity.tests._helpers import (
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestCreateRenameDelete:
    def test_create_writes_the_row_and_one_audit_event(self):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            row = services.create_entitlement(user_principal(admin), name="Finance")
        assert row.name == "Finance"
        assert AuditEvent.objects.filter(action=actions.ENTITLEMENT_CREATED).count() == 1

    def test_a_duplicate_name_is_refused_not_crashed(self):
        admin = make_admin()
        make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.create_entitlement(user_principal(admin), name="finance")

    def test_a_blank_name_is_refused(self):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.create_entitlement(user_principal(admin), name="   ")

    def test_rename_audits_the_old_and_the_new_name(self):
        admin = make_admin()
        row = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            services.rename_entitlement(user_principal(admin), row, "Accounts")
        event = AuditEvent.objects.get(action=actions.ENTITLEMENT_RENAMED)
        assert event.detail == {"from": "Finance", "to": "Accounts"}

    def test_rename_to_the_same_name_is_not_an_event(self):
        admin = make_admin()
        row = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            services.rename_entitlement(user_principal(admin), row, "Finance")
        assert not AuditEvent.objects.filter(action=actions.ENTITLEMENT_RENAMED).exists()

    def test_delete_takes_the_grants_and_returns_the_cascade_counts(self):
        admin = make_admin()
        row = make_entitlement(name="Finance")
        grant(row, user=make_user())
        grant(row, group=make_group())
        with posture(POSTURE_ENTERPRISE):
            counts = services.delete_entitlement(user_principal(admin), row)
        assert Entitlement.objects.count() == 0
        assert EntitlementGrant.objects.count() == 0
        assert counts["Grants"] == 2
        assert "Document labels" in counts
        assert "Tool labels" in counts
        assert AuditEvent.objects.filter(action=actions.ENTITLEMENT_DELETED).count() == 1


class TestGrantAndRevoke:
    def test_an_admin_may_grant_any_entitlement(self):
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            services.grant(user_principal(admin), finance, user=member)
        assert EntitlementGrant.objects.filter(entitlement=finance, user=member).exists()
        assert AuditEvent.objects.filter(action=actions.GRANT_ADDED).count() == 1

    def test_an_owner_may_grant_the_entitlement_they_own(self):
        owner = make_user()
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            services.grant(user_principal(owner), finance, user=member)
        assert EntitlementGrant.objects.filter(entitlement=finance, user=member).exists()

    def test_an_owner_may_not_grant_an_entitlement_they_do_not_own(self):
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.grant(user_principal(owner), legal, user=make_user())

    def test_a_plain_member_of_an_entitlement_may_not_grant_it(self):
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.grant(user_principal(member), finance, user=make_user())

    def test_an_owner_may_not_create_rename_or_delete(self):
        """One negative test per non-capability (done-when 5). An owner
        of E is a member of E plus exactly three capabilities; these are
        not among them."""
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        principal = user_principal(owner)
        with posture(POSTURE_ENTERPRISE):
            with pytest.raises(services.ServiceRefused):
                services.create_entitlement(principal, name="Legal")
            with pytest.raises(services.ServiceRefused):
                services.rename_entitlement(principal, finance, "Accounts")
            with pytest.raises(services.ServiceRefused):
                services.delete_entitlement(principal, finance)

    def test_granting_twice_is_refused_with_a_readable_message(self):
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            services.grant(user_principal(admin), finance, user=member)
            with pytest.raises(services.ServiceRefused) as excinfo:
                services.grant(user_principal(admin), finance, user=member)
        assert "already" in str(excinfo.value)

    def test_a_grant_naming_both_or_neither_is_refused_before_the_database_sees_it(self):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            with pytest.raises(services.ServiceRefused):
                services.grant(user_principal(admin), finance,
                               user=make_user(), group=make_group())
            with pytest.raises(services.ServiceRefused):
                services.grant(user_principal(admin), finance)

    def test_changing_a_role_is_an_update_and_its_own_audit_action(self):
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            row = services.grant(user_principal(admin), finance, user=member)
            services.set_grant_role(user_principal(admin), row,
                                    EntitlementGrant.Role.OWNER)
        row.refresh_from_db()
        assert row.role == EntitlementGrant.Role.OWNER
        assert EntitlementGrant.objects.filter(entitlement=finance, user=member).count() == 1
        assert AuditEvent.objects.filter(action=actions.GRANT_ROLE_CHANGED).count() == 1

    def test_revoke_removes_the_row_and_audits_it(self):
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            row = services.grant(user_principal(admin), finance, user=member)
            services.revoke(user_principal(admin), row)
        assert EntitlementGrant.objects.count() == 0
        assert AuditEvent.objects.filter(action=actions.GRANT_REVOKED).count() == 1

    def test_an_owner_may_revoke_within_their_entitlement_only(self):
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        elsewhere = grant(legal, user=make_user())
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.revoke(user_principal(owner), elsewhere)
