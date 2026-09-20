"""Signing in, signing out, and changing your own password.

Django's own views do the work. What is tested here is what this
platform adds: the audit rows, the `?next=` round trip, the refusal for
a deactivated account, and the one line of copy that tells a person
there is no password-reset email on this box.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity.contracts import actions
from identity.contracts.postures import POSTURE_PERSONAL
from identity.models import AuditEvent
from identity.tests._helpers import make_user, posture, seed_sweep_posture, sign_in

pytestmark = pytest.mark.django_db

PASSWORD = "not-a-real-password"


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


class TestLogin:
    def test_the_page_is_reachable_without_a_session(self, client):
        with posture(POSTURE_PERSONAL):
            assert client.get(reverse("identity-login")).status_code == 200

    def test_a_correct_password_signs_in_and_audits(self, client):
        user = make_user(username="ann", password=PASSWORD)
        with posture(POSTURE_PERSONAL):
            response = client.post(reverse("identity-login"),
                                   {"username": "ann", "password": PASSWORD})
        assert response.status_code == 302
        assert AuditEvent.objects.filter(action=actions.LOGIN,
                                         target_key=str(user.pk)).count() == 1

    def test_a_wrong_password_audits_the_attempt_with_the_username_only(self, client):
        """It exists so a brute-force attempt is visible. It records the
        SUBMITTED username and never the password, never a hash of it,
        and never the request body."""
        make_user(username="ann", password=PASSWORD)
        with posture(POSTURE_PERSONAL):
            response = client.post(reverse("identity-login"),
                                   {"username": "ann", "password": "wrong-value-here"})
        assert response.status_code == 200          # re-rendered with an error
        row = AuditEvent.objects.get(action=actions.LOGIN_FAILED)
        assert row.target_label == "ann"
        assert "wrong-value-here" not in f"{row.detail}{row.target_label}"

    def test_a_deactivated_account_cannot_sign_in(self, client):
        """Django's `ModelBackend` refuses an inactive user, so no code
        here does -- this asserts the mechanism is really in force."""
        make_user(username="ann", password=PASSWORD, is_active=False)
        with posture(POSTURE_PERSONAL):
            response = client.post(reverse("identity-login"),
                                   {"username": "ann", "password": PASSWORD})
        assert response.status_code == 200
        assert AuditEvent.objects.filter(action=actions.LOGIN).count() == 0

    def test_next_survives_the_round_trip(self, client):
        """The gate redirects with `?next=`; signing in must land the
        person where they were going, not on a default page."""
        make_user(username="ann", password=PASSWORD)
        target = reverse("rag-documents")
        with posture(POSTURE_PERSONAL):
            response = client.post(
                f"{reverse('identity-login')}?next={target}",
                {"username": "ann", "password": PASSWORD},
            )
        assert response["Location"] == target

    def test_an_off_host_next_is_not_followed(self, client):
        """Django's `LoginView` validates `next` with
        `url_has_allowed_host_and_scheme` by default; this pins that the
        behaviour is really in force here, not merely inherited on
        paper. An off-host target falls back to `LOGIN_REDIRECT_URL`."""
        make_user(username="ann", password=PASSWORD)
        with posture(POSTURE_PERSONAL):
            response = client.post(
                f"{reverse('identity-login')}?next=https://evil.example/steal",
                {"username": "ann", "password": PASSWORD},
            )
        assert response.status_code == 302
        assert response["Location"] != "https://evil.example/steal"
        assert "evil.example" not in response["Location"]

    def test_the_page_says_there_is_no_reset_email_on_this_box(self, client):
        """One line of honest copy instead of a reset flow that cannot
        work: there is no email server on this box, so a forgotten
        password is reset by an administrator."""
        with posture(POSTURE_PERSONAL):
            body = client.get(reverse("identity-login")).content.decode()
        assert "administrator" in body.lower()


class TestLogout:
    def test_it_is_post_only_and_audits(self, client):
        """Django 5's `LogoutView` is POST-only, which is correct: a GET
        logout is a URL anybody can put in an image tag."""
        user = make_user()
        with posture(POSTURE_PERSONAL):
            sign_in(client, user)
            assert client.get(reverse("identity-logout")).status_code == 405
            assert client.post(reverse("identity-logout")).status_code == 302
        assert AuditEvent.objects.filter(action=actions.LOGOUT).count() == 1


class TestPasswordChange:
    def test_a_person_changes_their_own_and_it_audits(self, client):
        user = make_user(username="ann", password=PASSWORD)
        with posture(POSTURE_PERSONAL):
            sign_in(client, user)
            response = client.post(reverse("identity-password-change"), {
                "old_password": PASSWORD,
                "new_password1": "a-different-value-9",
                "new_password2": "a-different-value-9",
            })
        assert response.status_code == 302
        user.refresh_from_db()
        assert user.check_password("a-different-value-9")
        assert AuditEvent.objects.filter(action=actions.PASSWORD_CHANGED).count() == 1

    def test_the_page_needs_a_session(self, client):
        with posture(POSTURE_PERSONAL):
            assert client.get(reverse("identity-password-change")).status_code == 302
