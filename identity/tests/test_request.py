"""`principal_for_request` -- the ONE request-to-principal point.

Uses `rf` (RequestFactory) rather than the test `Client` wherever it can,
so it triggers no URL resolution and no middleware at all: this function
is being tested, not the gate that consumes it (Task 7).
"""
from __future__ import annotations

import pytest

from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_PERSONAL
from identity.contracts.principals import ANONYMOUS, OPEN_PRINCIPAL, Principal
from identity.request import principal_for_request, user_for_request
from identity.tests._helpers import make_user, posture, seed_sweep_posture

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


class TestOpenPosture:
    def test_it_returns_the_shared_open_principal(self, rf):
        """The SAME object, not an equal one built here -- so this
        module and every command hand out one answer to "who is this
        box".

        PINNED: this module's `_sweep` autouse fixture applies
        `FARABUNKER_TEST_POSTURE` when the sweep sets it, and the
        `OPEN_PRINCIPAL` answer is specifically the OPEN posture's own
        -- `posture(...)` always wins over the sweep."""
        from identity.contracts.postures import POSTURE_OPEN
        with posture(POSTURE_OPEN):
            assert principal_for_request(rf.get("/chat/")) is OPEN_PRINCIPAL

    def test_it_does_not_look_at_the_request_user_at_all(self, rf, django_assert_num_queries):
        """An open box never runs a permission query. The one read is
        the settings row."""
        from identity.contracts.postures import POSTURE_OPEN
        from identity.models import IdentitySettings
        with posture(POSTURE_OPEN):
            IdentitySettings.get_solo()
            request = rf.get("/chat/")
            with django_assert_num_queries(1):
                assert principal_for_request(request) is OPEN_PRINCIPAL


class TestAccountsOn:
    @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
    def test_a_signed_in_user_becomes_a_user_principal_keyed_by_pk(self, rf, name):
        user = make_user()
        request = rf.get("/chat/")
        request.user = user
        with posture(name):
            assert principal_for_request(request) == Principal("user", str(user.pk))

    @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
    def test_a_request_with_no_session_is_ANONYMOUS_never_the_open_principal(self, rf, name):
        """A posture leak wearing an unauthenticated request's clothes
        is exactly what this must not do."""
        from django.contrib.auth.models import AnonymousUser
        request = rf.get("/chat/")
        request.user = AnonymousUser()
        with posture(name):
            answer = principal_for_request(request)
        assert answer is ANONYMOUS
        assert answer is not OPEN_PRINCIPAL

    def test_a_request_with_no_user_attribute_at_all_is_ANONYMOUS(self, rf):
        """A request that never reached `AuthenticationMiddleware` --
        a management command's synthetic request, a test double -- must
        not be read as the box."""
        request = rf.get("/chat/")
        with posture(POSTURE_PERSONAL):
            assert principal_for_request(request) is ANONYMOUS


class TestTheSettingIsGone:
    def test_accounts_required_is_not_a_setting_any_more(self):
        """Its True branch never worked -- it raised. Deleting it
        removes a documented non-feature, not a behaviour, and leaves
        exactly one answer to "what posture is this box in"."""
        from django.conf import settings
        assert not hasattr(settings, "ACCOUNTS_REQUIRED")


class TestUserForRequest:
    """The real `User` instance a foreign key needs (`ToolEntitlement.
    labelled_by`), as distinct from the `Principal` value object
    `principal_for_request` answers with. `identity/` is not scanned by
    `test_no_view_outside_identity_reads_request_user` -- this is the one
    place `request.user` is read directly, so a column outside `identity/`
    never has to."""

    def test_it_returns_the_signed_in_user(self, rf):
        user = make_user()
        request = rf.get("/chat/tools/")
        request.user = user
        assert user_for_request(request) == user

    def test_an_anonymous_request_answers_None(self, rf):
        from django.contrib.auth.models import AnonymousUser
        request = rf.get("/chat/tools/")
        request.user = AnonymousUser()
        assert user_for_request(request) is None

    def test_a_request_with_no_user_attribute_at_all_answers_None(self, rf):
        request = rf.get("/chat/tools/")
        assert user_for_request(request) is None
