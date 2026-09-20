"""Groups are Django's `auth.Group`, unchanged -- membership only.

This platform never reads `Group.permissions`: Django's `Permission`
model is a named non-goal (owner decision 16), because it is model-level
and would be a second grant mechanism beside entitlements. Group
MANAGERS -- a role on membership -- are deferred, which is why
membership is not customised with a through-model now.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.models import Group

from identity import services
from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent, EntitlementGrant
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


class TestGroupWrites:
    def test_create_and_delete_are_admin_only_and_audited(self):
        admin = make_admin()
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            group = services.create_group(user_principal(admin), name="analysts")
            assert AuditEvent.objects.filter(action=actions.GROUP_CREATED).count() == 1
            with pytest.raises(services.ServiceRefused):
                services.create_group(user_principal(member), name="others")
            services.delete_group(user_principal(admin), group)
        assert Group.objects.filter(name="analysts").count() == 0
        assert AuditEvent.objects.filter(action=actions.GROUP_DELETED).count() == 1

    def test_a_duplicate_group_name_is_refused_not_crashed(self):
        admin = make_admin()
        make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.create_group(user_principal(admin), name="analysts")

    def test_membership_writes_are_audited_and_idempotent(self):
        admin = make_admin()
        member = make_user()
        group = make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE):
            services.add_group_member(user_principal(admin), group, member)
            services.add_group_member(user_principal(admin), group, member)
            assert AuditEvent.objects.filter(action=actions.GROUP_MEMBER_ADDED).count() == 1
            services.remove_group_member(user_principal(admin), group, member)
            services.remove_group_member(user_principal(admin), group, member)
        assert AuditEvent.objects.filter(action=actions.GROUP_MEMBER_REMOVED).count() == 1
        assert member.groups.count() == 0

    def test_deleting_a_group_takes_its_grants_with_it(self):
        admin = make_admin()
        group = make_group(name="analysts")
        finance = make_entitlement(name="Finance")
        grant(finance, group=group)
        with posture(POSTURE_ENTERPRISE):
            services.delete_group(user_principal(admin), group)
        assert EntitlementGrant.objects.count() == 0
