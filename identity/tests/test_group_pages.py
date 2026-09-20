"""`/identity/groups/` -- create, delete, add and remove members."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils.html import escape

from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.tests._helpers import (
    make_admin, make_group, make_user, posture, reset_settings, seed_sweep_posture, sign_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestTheGroupsPage:
    def test_an_admin_sees_the_page_and_a_member_gets_403(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            assert client.get(reverse("identity-groups")).status_code == 200
            other = client.__class__()
            sign_in(other, member)
            assert other.get(reverse("identity-groups")).status_code == 403

    def test_creating_a_group_redirects_and_flashes(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-groups"), {"name": "analysts"})
        assert response.status_code == 302
        assert Group.objects.filter(name="analysts").exists()

    def test_a_duplicate_name_flashes_an_error_and_never_500s(self, client):
        admin = make_admin()
        make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-groups"), {"name": "analysts"},
                                   follow=True)
        assert response.status_code == 200
        assert b"already exists" in response.content
        assert b"Traceback" not in response.content

    def test_membership_and_delete_actions_dispatch(self, client):
        admin, member = make_admin(), make_user()
        group = make_group(name="analysts")
        url = reverse("identity-group-edit", args=[group.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(url, {"action": "add_member", "user": member.pk})
            assert member.groups.filter(pk=group.pk).exists()
            client.post(url, {"action": "remove_member", "user": member.pk})
            assert not member.groups.filter(pk=group.pk).exists()
            assert client.post(url, {"action": "delete"}).status_code == 302
        assert Group.objects.filter(pk=group.pk).count() == 0

    def test_an_unknown_action_flashes_and_is_not_a_silent_no_op(self, client):
        """F7's class (Coherence Wave C): flash-and-redirect, not a raw
        400. "rename" is a real action on OTHER pages and not one here,
        which is exactly the stale-form case this refusal has to be
        readable for."""
        admin = make_admin()
        group = make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-group-edit", args=[group.pk]),
                                   {"action": "rename"}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse("identity-groups")
        assert escape("'rename' is not a recognised action.") in response.content.decode()
        assert Group.objects.filter(name="analysts").exists()

    def test_an_unknown_group_id_answers_404(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-group-edit", args=[9999]),
                                   {"action": "delete"})
        assert response.status_code == 404
