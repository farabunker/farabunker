"""A-2: `auth.Group` stops being a second, unaudited door to grants and shares.

Django's stock `Group` admin has no interaction with the guards
`identity.services` puts around a group's writes -- `create_group`,
`delete_group`, `add_group_member` and `remove_group_member` each write
an audit row, and the stock admin's own create/rename/delete forms
bypass every one of them. The reasoning that used to leave the admin
registered ("this platform never reads `Group.permissions`, so there is
nothing to hide") covers permissions only, not the two reverse foreign
keys this platform hung off the model (`EntitlementGrant.group`,
`Share.group`): deleting a group from the admin cascades away every
grant and every share whose subject was that group, with none of the
audit rows the identity page's own delete writes for the identical
change. This module pins the fix -- `auth.Group` unregistered from
`/admin/` -- and that the one remaining door, the identity page, still
does the whole job and still audits it.
"""
from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.urls import reverse

from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent, User
from identity.tests._helpers import (
    make_admin, make_group, posture, reset_settings, seed_sweep_posture, sign_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestA2TheGroupModelIsNotASecondDoor:

    def test_the_group_admin_is_not_registered(self):
        assert Group not in admin.site._registry

    def test_the_group_change_url_is_a_404_for_a_superuser(self, client):
        admin_user = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin_user)
            assert client.get("/admin/auth/group/").status_code == 404

    def test_the_identity_page_still_creates_and_deletes_groups(self, client):
        """The one remaining door must genuinely work -- otherwise this
        removes a capability instead of a duplicate. Groups have no
        rename primitive in `identity.services` (membership only, no
        renaming action in `AUDIT_ACTIONS`), so create and delete are
        the whole vocabulary this door needs to cover."""
        admin_user = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin_user)
            create = client.post(reverse("identity-groups"), {"name": "analysts"})
            assert create.status_code == 302
            group = Group.objects.get(name="analysts")

            delete = client.post(reverse("identity-group-edit", args=[group.pk]),
                                 {"action": "delete"})
            assert delete.status_code == 302
        assert not Group.objects.filter(pk=group.pk).exists()

    def test_deleting_a_group_through_the_identity_page_writes_an_audit_row(self, client):
        """The write the admin door skipped. Pinned here so the reason
        for the unregistration is visible from the test that guards
        it."""
        admin_user = make_admin()
        group = make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin_user)
            client.post(reverse("identity-group-edit", args=[group.pk]), {"action": "delete"})
        assert AuditEvent.objects.filter(
            action=actions.GROUP_DELETED, target_label="analysts").count() == 1

    def test_the_user_admin_is_still_registered(self):
        """Break-glass for users stays -- this task narrows one model,
        not the whole surface."""
        assert User in admin.site._registry

    def test_the_user_admin_exposes_no_groups_field(self):
        """H40 follow-up: the user admin never offered group membership.

        `fieldsets` is declared in full and names no `groups`, so the
        only door to membership is the identity page above. Pinned so a
        later edit cannot quietly re-open a second, unaudited one."""
        user_admin = admin.site._registry[User]
        named = {field
                 for _label, options in user_admin.fieldsets
                 for field in options["fields"]}
        assert "groups" not in named
        assert "groups" not in user_admin.filter_horizontal
