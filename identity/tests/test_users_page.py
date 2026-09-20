"""The users page: list, create, deactivate/reactivate, toggle
superuser, reset password.

`rename` is not offered: `identity.services` has no rename primitive
and `identity.contracts.actions.AUDIT_ACTIONS` has no entry for it
yet -- see `docs/superpowers/specs/2026-08-29-identity-and-auth-
design.md` §15's Users row.

EVERY MUTATION GOES THROUGH `identity.services`, so every guard and
every audit row applies here exactly as it does on the command line.
The assertions below check the page's own behaviour; the guards
themselves are tested in `test_services.py`.
"""
from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils.html import escape

from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_PERSONAL
from identity.models import AuditEvent
from identity.tests._helpers import (
    grant, make_admin, make_entitlement, make_user, posture, seed_sweep_posture, sign_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


class TestAccess:
    def test_a_member_gets_403_and_an_admin_gets_200(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            assert client.get(reverse("identity-users")).status_code == 403
        client.logout()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            assert client.get(reverse("identity-users")).status_code == 200


class TestPersonalPosture:
    def test_it_hides_the_superuser_toggle_and_says_why(self, client):
        """In `personal` every account IS an administrator -- the
        owner's decision stated exactly -- so the page does not offer a
        choice it does not have, and says so in one sentence."""
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-users")).content.decode()
        assert "administrator" in body.lower()
        assert 'name="is_superuser"' not in body

    def test_creating_an_account_makes_it_a_superuser(self, client):
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            client.post(reverse("identity-user-create"),
                        {"username": "ann", "password": "a-real-enough-value"})
        from django.contrib.auth import get_user_model
        assert get_user_model().objects.get(username="ann").is_superuser is True

    def test_flipping_to_enterprise_demotes_nobody(self, client):
        """It starts OFFERING the choice; the first thing an
        administrator then does is demote the accounts that should be
        members."""
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            client.post(reverse("identity-user-create"),
                        {"username": "ann", "password": "a-real-enough-value"})
        from django.contrib.auth import get_user_model
        with posture(POSTURE_ENTERPRISE):
            assert get_user_model().objects.get(username="ann").is_superuser is True


class TestEnterprisePosture:
    def test_creating_a_member_does_not_make_them_an_admin(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            client.post(reverse("identity-user-create"),
                        {"username": "ann", "password": "a-real-enough-value"})
        from django.contrib.auth import get_user_model
        assert get_user_model().objects.get(username="ann").is_superuser is False


class TestPasswordSimilarityValidation:
    """T-consolidation FIX-BEFORE-MERGE 4: `UserCreateForm.clean_password`
    now passes `user=` to `validate_password`, so
    `UserAttributeSimilarityValidator` actually runs on account creation
    -- it silently no-ops on a `None` user, which is what shipped before
    this fix and is exactly the docstring's overstated promise this test
    pins against regressing."""

    def test_a_password_too_similar_to_the_username_is_refused(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(
                reverse("identity-user-create"),
                {"username": "annabelle", "password": "annabelle1"},
                follow=True,
            )
        from django.contrib.auth import get_user_model
        assert not get_user_model().objects.filter(username="annabelle").exists()
        body = response.content.decode()
        assert "too similar" in body.lower()


class TestFlashedFormErrorsShareOneFormat:
    """Doc-truth follow-up (consolidation round 3): `_flash_form_errors`
    is the ONE body `user_create` and `user_edit`'s `set_password`
    branch both call (round 2 FIX-NOW 4) -- pinned here so the two call
    sites, which had already drifted once (one field-prefixed, one
    bare), cannot drift back apart unnoticed."""

    def test_user_create_and_set_password_flash_the_same_field_prefixed_format(
            self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            create_response = client.post(
                reverse("identity-user-create"),
                {"username": "ann", "password": "short"}, follow=True)
            edit_response = client.post(
                reverse("identity-user-edit", args=[member.pk]),
                {"action": "set_password", "password": ""}, follow=True)
        assert "password:" in create_response.content.decode()
        assert "password:" in edit_response.content.decode()


@pytest.fixture
def _isolated_ownership_registry():
    """Snapshot and restore `identity.contracts.ownership`'s registry --
    matching `test_services.py`'s own fixture of the same name. Nothing
    in production code calls `register_owned_rows` yet (that lands with
    each owning column's `AppConfig.ready()`, a later task), so the real
    registry is empty at suite time; this registers one REAL table for
    the one test below that needs a non-zero count, and restores
    whatever was there (nothing, today) afterwards."""
    from identity.contracts import ownership
    saved = dict(ownership._OWNED)
    ownership._OWNED.clear()
    yield
    ownership._OWNED.clear()
    ownership._OWNED.update(saved)


class TestMutations:
    def test_deactivation_reports_the_owned_row_counts_on_the_page(
            self, client, _isolated_ownership_registry):
        """The operator is told what they now need to reassign, at the
        moment the decision is made -- not in a document they would have
        to remember to read.

        A REAL registered table with a REAL owned row, not merely
        checking for the `reassign_owner` hint text: T9 review round 1
        MINOR made that hint conditional on there being something to
        reassign, so the count itself (`Conversations: 1`) has to
        appear too, or this would pass whether or not the page ever
        looked at a single row."""
        from agents.models import Conversation
        from agents.tests._helpers import make_agent
        from identity.contracts.ownership import OwnedRows, register_owned_rows

        register_owned_rows(
            OwnedRows("agents.conversation", "Conversations", "agents.Conversation"))
        admin, member = make_admin(), make_user()
        agent = make_agent()
        Conversation.objects.create(agent=agent, owner_kind="user", owner_key=str(member.pk))

        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-user-edit", args=[member.pk]),
                                   {"action": "deactivate"}, follow=True)
        assert response.status_code == 200
        body = response.content.decode()
        assert "reassign_owner" in body
        assert "Conversations: 1" in body

    def test_deactivating_a_member_with_nothing_owned_says_nothing_about_reassigning(
            self, client):
        """T9 review round 1 MINOR: a member with nothing owned is not
        told to run a command against an empty set."""
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-user-edit", args=[member.pk]),
                                   {"action": "deactivate"}, follow=True)
        assert response.status_code == 200
        assert "reassign_owner" not in response.content.decode()

    def test_demoting_the_last_admin_is_refused_with_the_pages_own_message(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-user-edit", args=[admin.pk]),
                                   {"action": "demote"}, follow=True)
        assert "last active administrator" in response.content.decode()
        admin.refresh_from_db()
        assert admin.is_superuser is True

    def test_resetting_a_password_audits_a_reset_not_a_change(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("identity-user-edit", args=[member.pk]),
                        {"action": "set_password", "password": "another-real-value"})
        member.refresh_from_db()
        assert member.check_password("another-real-value")
        assert AuditEvent.objects.filter(action=actions.PASSWORD_RESET).exists()
        assert not AuditEvent.objects.filter(action=actions.PASSWORD_CHANGED).exists()

    def test_an_unknown_user_id_is_404_not_500(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            assert client.post(reverse("identity-user-edit", args=[99999999]),
                               {"action": "demote"}).status_code == 404

    def test_an_unknown_action_flashes_and_is_not_a_silent_no_op(self, client):
        """A form that quietly did nothing would read as success -- and a
        raw 400 (what this used to answer, F7's class, Coherence Wave C)
        is a bare plain-text page with no shell, no sidebar and no flash,
        which tells the operator the same thing while dumping them out of
        the settings chrome to read it."""
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("identity-user-edit", args=[member.pk]),
                                   {"action": "explode"}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse("identity-users")
        # `escape`, because the flash renders through `{{ message }}`:
        # the quotes `!r` puts round the action arrive as `&#x27;`.
        assert escape("'explode' is not a recognised action.") in response.content.decode()

    def test_resetting_to_an_empty_password_is_refused(self, client):
        """`identity.services.set_password` runs no validation of its
        own (it is a plain reset, not a policy check), and
        `AbstractBaseUser.set_password("")` happily produces a USABLE,
        EMPTY password. T9 review round 1 CRITICAL: the page must
        refuse this itself."""
        admin, member = make_admin(), make_user()
        original_hash = member.password
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-user-edit", args=[member.pk]),
                                   {"action": "set_password", "password": ""}, follow=True)
        member.refresh_from_db()
        assert response.status_code == 200
        assert member.password == original_hash

    def test_resetting_to_a_password_djangos_own_validators_reject_is_refused(self, client):
        """The same validators `UserCreateForm` runs on account
        creation -- length, commonness, similarity, all-numeric."""
        admin, member = make_admin(), make_user()
        original_hash = member.password
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-user-edit", args=[member.pk]),
                                   {"action": "set_password", "password": "12345678"},
                                   follow=True)
        member.refresh_from_db()
        assert response.status_code == 200
        assert member.password == original_hash


class TestMemberIsRefusedOnEveryMutation:
    """T9 review round 1 IMPORTANT: pins 403, not merely "not a 200", for
    a signed-in ordinary member POSTing any of the five `user_edit`
    actions or `user_create` -- `@require_admin` plus
    `IdentityGateMiddleware`'s own ADMIN tier for these routes, made
    explicit per-action rather than trusted by inference from the GET
    check in `TestAccess`."""

    @pytest.mark.parametrize("action", [
        "deactivate", "reactivate", "promote", "demote", "set_password"])
    def test_a_member_posting_user_edit_gets_403(self, client, action):
        member = make_user()
        target = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(
                reverse("identity-user-edit", args=[target.pk]),
                {"action": action, "password": "another-real-value"},
            )
        assert response.status_code == 403

    def test_a_member_posting_user_create_gets_403(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("identity-user-create"),
                                   {"username": "ann", "password": "a-real-enough-value"})
        assert response.status_code == 403
        from django.contrib.auth import get_user_model
        assert not get_user_model().objects.filter(username="ann").exists()


class TestTheUsersPageShowsEffectiveAccess:
    def test_it_shows_what_an_account_holds_and_what_that_unlocks(self, client):
        admin = make_admin()
        member = make_user(username="ana")
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("identity-users")).content
        assert b"Finance" in body

    def test_the_panel_writes_nothing(self, client):
        """READ-ONLY, and asserted: the panel is a view over data this
        column already owns, and a page that quietly wrote while
        rendering would be the worst kind of surprise on an admin
        surface."""
        from identity.models import AuditEvent
        admin = make_admin()
        grant(make_entitlement(name="Finance"), user=make_user())
        before = AuditEvent.objects.count()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.get(reverse("identity-users"))
        assert AuditEvent.objects.count() == before
