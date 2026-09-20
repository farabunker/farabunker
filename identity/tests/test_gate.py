"""`identity.gate`'s explicit per-view decorators -- the belt to
`IdentityGateMiddleware`'s braces.

Nothing in IA-1 wires these to a route through the URL conf, so they are
exercised directly against a `RequestFactory` request rather than the
test `Client`: no URL resolution, no middleware, exactly the shape
`identity/tests/test_request.py` already uses for the function these
decorators are themselves built on.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse

from identity.contracts.postures import POSTURE_OPEN, POSTURE_PERSONAL
from identity.gate import require_admin, require_principal
from identity.tests._helpers import make_admin, make_user, posture, seed_sweep_posture

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


def _view(request):
    return HttpResponse("ok")


def _request(rf, user=None):
    request = rf.get("/gated/")
    request.user = user if user is not None else AnonymousUser()
    return request


class TestRequirePrincipal:
    def test_open_posture_passes_every_caller_through(self, rf):
        """No accounts, nobody to refuse."""
        with posture(POSTURE_OPEN):
            response = require_principal(_view)(_request(rf))
        assert response.status_code == 200

    def test_anonymous_in_personal_posture_is_redirected(self, rf):
        with posture(POSTURE_PERSONAL):
            response = require_principal(_view)(_request(rf))
        assert response.status_code == 302

    def test_a_signed_in_user_passes_through(self, rf):
        member = make_user()
        with posture(POSTURE_PERSONAL):
            response = require_principal(_view)(_request(rf, member))
        assert response.status_code == 200


class TestRequireAdmin:
    def test_open_posture_passes_every_caller_through(self, rf):
        """`OPEN_PRINCIPAL` is an admin: on a box with no accounts there
        is nobody for anything to be hidden from."""
        with posture(POSTURE_OPEN):
            response = require_admin(_view)(_request(rf))
        assert response.status_code == 200

    def test_anonymous_in_personal_posture_is_redirected_not_forbidden(self, rf):
        """Refused for lack of a session, same as `require_principal` --
        403 is reserved for a caller who IS signed in and still isn't an
        administrator."""
        with posture(POSTURE_PERSONAL):
            response = require_admin(_view)(_request(rf))
        assert response.status_code == 302

    def test_a_member_is_refused_with_403(self, rf):
        member = make_user()
        with posture(POSTURE_PERSONAL):
            response = require_admin(_view)(_request(rf, member))
        assert response.status_code == 403

    def test_an_admin_passes_through(self, rf):
        admin = make_admin()
        with posture(POSTURE_PERSONAL):
            response = require_admin(_view)(_request(rf, admin))
        assert response.status_code == 200

    def test_a_member_polling_gets_json_not_html(self, rf):
        """FIX-BEFORE-MERGE 5: `require_admin` shares
        `identity.middleware.refuse_not_admin` with
        `IdentityGateMiddleware`, so an XHR reaching either door gets the
        same JSON 403 -- not the HTML `HttpResponseForbidden` this
        decorator used to answer unconditionally."""
        member = make_user()
        request = _request(rf, member)
        request.headers = {"X-Requested-With": "XMLHttpRequest"}
        with posture(POSTURE_PERSONAL):
            response = require_admin(_view)(request)
        assert response.status_code == 403
        assert response["Content-Type"] == "application/json"
        import json
        assert json.loads(response.content) == {"error": "administrators only"}
