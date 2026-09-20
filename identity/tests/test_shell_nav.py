"""RULING R-T8: the shared shell's sign-out control, and (T9 review
round 1 IMPORTANT) the admin-only Identity & security entry beside
Accounts.

There is no sign-out control anywhere else on the box -- `identity-
logout` is POST-only (a GET logout is a URL anybody can put in an
image tag) and nothing but a page's own nav can reach it. This pins
that `foundation/templates/_shell.html` renders exactly one: a
zero-JS POST form, beside the signed-in account's name, and ONLY when
accounts are on and somebody is actually signed in.

The posture page (`identity-settings`) was linked from nowhere until
this review round: `foundation/setup/tests/test_views.py::
TestAccountsLink` pins the OPEN-posture discovery path (Install guides'
"Turn accounts on"), and `TestTheAdminSettingsLink` below pins the
ongoing, admin-only entry once accounts are on.

SINCE UI-2 THE IDENTITY ENTRIES LIVE IN THE SETTINGS SIDEBAR
(`foundation/templates/_settings.html`), not in a Manage row on every
page in the box, so every assertion here is driven from a settings-area
page. `setup-index` is the one used throughout: it is PUBLIC and mounted
unconditionally, so the SAME url renders the sidebar for an anonymous
visitor, a member and an administrator with no `FARABUNKER_FEATURES`
dependency either way -- which is exactly what an "only for an admin"
claim needs in order not to be vacuous.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL
from identity.tests._helpers import make_admin, make_user, posture, seed_sweep_posture, sign_in

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


def _has_sign_out_form(body: str) -> bool:
    return "Sign out" in body and reverse("identity-logout") in body


class TestTheSignOutControl:
    def test_it_renders_for_a_signed_in_member_in_personal_posture(self, client):
        member = make_user()
        with posture(POSTURE_PERSONAL):
            sign_in(client, member)
            response = client.get(reverse("identity-password-change"))
        assert response.status_code == 200
        body = response.content.decode()
        assert _has_sign_out_form(body)
        assert member.username in body

    def test_it_does_not_render_in_the_open_posture(self, client):
        with posture(POSTURE_OPEN):
            response = client.get(reverse("setup-index"))
        assert response.status_code == 200
        assert not _has_sign_out_form(response.content.decode())

    def test_it_does_not_render_for_an_anonymous_visitor(self, client):
        with posture(POSTURE_PERSONAL):
            response = client.get(reverse("setup-index"))
        assert response.status_code == 200
        assert not _has_sign_out_form(response.content.decode())


class TestTheAdminSettingsLink:
    """The admin-only sidebar entry for `identity-settings`, in the same
    settings area as Accounts."""

    def test_it_renders_beside_accounts_for_an_admin_in_a_non_open_posture(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.get(reverse("identity-users"))
        assert response.status_code == 200
        body = response.content.decode()
        assert reverse("identity-users") in body
        assert reverse("identity-settings") in body

    def test_it_does_not_render_for_a_member(self, client):
        """Driven from `setup-index`, which a member CAN open and which
        renders the settings sidebar: asking a page that has no sidebar
        at all would answer this for the wrong reason."""
        member = make_user()
        with posture(POSTURE_PERSONAL):
            sign_in(client, member)
            response = client.get(reverse("setup-index"))
        assert response.status_code == 200
        body = response.content.decode()
        assert reverse("setup-index") in body
        assert reverse("identity-settings") not in body

    def test_it_does_not_render_in_the_open_posture(self, client):
        """`identity-settings` still appears in the open posture, via
        Install guides' own "Turn accounts on" sentence
        (`foundation/setup/tests/test_views.py::TestAccountsLink`) --
        what's pinned absent here is the sidebar entry specifically,
        which `_settings.html` renders in the same conditional block as
        the Accounts entry right beside it."""
        with posture(POSTURE_OPEN):
            response = client.get(reverse("setup-index"))
        assert response.status_code == 200
        assert f'href="{reverse("identity-users")}"' not in response.content.decode()


class TestIA2NavLinks:
    """Groups and Entitlements, added to the nav in IA-2 and moved into
    the settings sidebar in UI-2 -- so they are asserted on a
    settings-area page rather than on `/chat/`, which is a use-surface
    and no longer names any operator page at all."""

    def test_the_two_new_links_appear_only_for_an_admin_off_open(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("setup-index")).content
            assert b"/identity/groups/" in body and b"/identity/entitlements/" in body
            other = client.__class__()
            sign_in(other, member)
            assert b"/identity/groups/" not in other.get(reverse("setup-index")).content

    def test_an_open_box_shell_is_unchanged(self, client):
        body = client.get(reverse("setup-index")).content
        assert b"/identity/groups/" not in body

    def test_no_use_surface_names_an_operator_page_at_all(self, client):
        """UI-2's own claim, and the reason the two above moved: an
        administrator's `/chat/` carries ONE door to the operator
        surfaces (`/settings/`) instead of nine links to them."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("chat-index")).content.decode()
        assert reverse("settings-index") in body
        for name in ("identity-groups", "identity-entitlements", "identity-users",
                     "identity-settings", "inference-console", "rag-settings"):
            assert reverse(name) not in body
