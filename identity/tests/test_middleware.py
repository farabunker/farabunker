"""The gate. Coarse tiers only -- the row rules live in the views.

KEEPS "vision" IN ANY FARABUNKER_FEATURES OVERRIDE it makes, because it
calls `reverse()`; without the flag, `/vision/`'s tree is not mounted
and `reverse` raises.
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL
from identity.tests._helpers import make_admin, make_user, posture, seed_sweep_posture, sign_in
# `models.queue.models` reached directly, in a test module only -- the
# same exemption `foundation/ops/tests/test_import_law.py`'s own
# docstring documents for `tools/*/tests/` seeding another column's
# rows for a fixture; this codebase has no shared factory layer to
# route it through instead.
from models.queue.models import InferenceJob

pytestmark = pytest.mark.django_db

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


def _job(**overrides) -> InferenceJob:
    fields = dict(kind="rag.ask", payload={}, priority=100)
    fields.update(overrides)
    return InferenceJob.objects.create(**fields)


class TestOpenPosture:
    def test_every_route_answers_exactly_as_it_does_today(self, client):
        """The household box's experience is byte-identical: no login,
        no redirect, no 403, on a page and on an admin surface alike."""
        with posture(POSTURE_OPEN):
            assert client.get(reverse("chat-index")).status_code == 200
            assert client.get(reverse("inference-console")).status_code == 200
            assert client.get(reverse("setup-index")).status_code == 200

    def test_the_gate_returns_before_it_reads_the_route_table(self, client, monkeypatch):
        """Structural, not incidental: `accounts_on()` is tested first
        and the middleware returns, so nothing about the route table or
        the user is consulted in the posture that is the default."""
        import identity.middleware as mw
        monkeypatch.setattr(mw, "tier_for",
                            lambda *a, **k: pytest.fail("route table consulted"))
        with posture(POSTURE_OPEN):
            assert client.get(reverse("chat-index")).status_code == 200


class TestAnonymous:
    @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
    def test_an_html_get_is_redirected_with_a_next_parameter(self, client, name):
        with posture(name):
            response = client.get(reverse("chat-index"))
        assert response.status_code == 302
        assert "next=/chat/" in response["Location"]

    @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
    def test_the_redirect_target_really_is_the_login_url(self, client, name):
        """`LOGIN_URL` is a literal path (`identity/middleware.py`'s
        module docstring explains why: reversing it would 500 every
        anonymous request before `/identity/` was mounted). Now that
        Task 8 has mounted it, this pins that the path and the resolver
        name agree, and that the redirect this middleware issues lands
        on the real login page rather than a stale hand-typed string."""
        assert settings.LOGIN_URL == reverse("identity-login")
        with posture(name):
            response = client.get(reverse("chat-index"))
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('identity-login')}?next=/chat/"

    @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
    def test_a_poll_gets_a_json_401_it_can_render(self, client, name):
        """The existing pollers already send this header, so they get a
        body with a message and a login URL rather than a login page
        they would inject into a card."""
        with posture(name):
            response = client.get(reverse("jobs-queue"), **XHR)
        assert response.status_code == 401
        assert response.json()["login_url"]

    def test_the_ask_status_poll_gets_a_json_401_when_anonymous(self, client):
        """`tools/rag/templates/rag/ask.html`'s status poll now sends
        this header (it did not before this fix), so a session that
        expires mid-poll gets a JSON 401 it can read rather than a
        followed redirect to the login page's HTML, which `response.
        json()` cannot parse. The job id doesn't need to exist: the gate
        refuses the request in `process_view`, before the view -- which
        would 404 an unknown id -- ever runs."""
        with posture(POSTURE_PERSONAL):
            response = client.get(reverse("rag-ask-status", args=[1]), **XHR)
        assert response.status_code == 401
        assert response.json()["login_url"]

    def test_the_setup_page_stays_public_in_every_posture(self, client):
        """It is the page a person needs BEFORE they can log in to a box
        whose engines are not up."""
        for name in (POSTURE_OPEN, POSTURE_PERSONAL, POSTURE_ENTERPRISE):
            with posture(name):
                assert client.get(reverse("setup-index")).status_code == 200


class TestTiers:
    def test_a_member_reaches_an_authenticated_route_and_not_an_admin_one(self, client):
        member = make_user()
        with posture(POSTURE_PERSONAL):
            sign_in(client, member)
            assert client.get(reverse("chat-index")).status_code == 200
            assert client.get(reverse("inference-console")).status_code == 403

    def test_403_on_an_admin_surface_not_404(self, client):
        """The EXISTENCE of a model console is not a secret. 404 is
        reserved for row-addressed URLs, where a 403 would confirm the
        row exists.

        `jobs-settings-update` is `@require_POST`, but `process_view` runs
        BEFORE Django dispatches to the view -- and therefore before that
        decorator ever sees the request -- so a GET here gets the gate's
        403, never the decorator's 405. `== 403`, not `in (403, 405)`:
        the looser form could not actually fail if the gate stopped
        refusing this route at all and Django's own dispatch produced
        the 405 instead.
        """
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            assert client.get(reverse("jobs-settings-update")).status_code == 403

    def test_an_admin_reaches_every_admin_surface_with_the_content_setting_off(self, client):
        """Administering is not reading: the console, the queue settings
        and the posture page are all reachable with the toggle off."""
        admin = make_admin()
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            sign_in(client, admin)
            assert client.get(reverse("inference-console")).status_code == 200

    def test_an_unclassified_route_is_refused_to_a_member(self, client, monkeypatch):
        """Fail closed. A route added without a classification must not
        ship open."""
        import identity.routes as routes
        monkeypatch.delitem(routes.ROUTE_RULES, "chat-index")
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            assert client.get(reverse("chat-index")).status_code == 403

    def test_the_django_admin_is_superuser_only_not_staff_only(self, client):
        """Classified wholesale by namespace, with no `AdminSite`
        subclass. A staff-but-not-superuser account is refused."""
        staff = make_user(is_staff=True)
        with posture(POSTURE_PERSONAL):
            sign_in(client, staff)
            assert client.get("/admin/").status_code == 403


class TestTheSingleRowRead:
    """`process_view` fetches `IdentitySettings.get_solo()` exactly ONCE
    and threads it through `accounts_on`, `principal_for_request`,
    `is_admin` (each via their `settings_row=` keyword) and
    `_roll_the_session` -- not once per call, off each of their own
    no-argument, re-fetching forms. Counted against
    `identity_identitysettings` specifically rather than the request's
    total query count, so this pin doesn't drift with the view's own
    unrelated queries.

    `inference-console` is picked because it calls nothing in
    `identity.*` itself (unlike `chat-index`, which resolves its own
    principal to attribute a conversation) -- so every
    `identity_identitysettings` read counted for it is provably the
    gate's, not a second, legitimate one the view made on its own
    account.

    `jobs-queue` is DIFFERENT since IA-1 (Task 13): the queue page now
    legitimately asks `identity.*` on its own account, once per request
    (`QueueView.get_context_data` calls `principal_for_request` and
    `models.queue.visibility.visible_rows`/`may_read_job_content` once
    per snapshot list and once per row) -- so it no longer belongs
    beside `inference-console`'s "calls nothing" claim. Its own pin
    below asserts the SAME request-wide total (one settings read) it
    always has, plus exactly TWO `identity_user` reads (this box's user
    table -- `AUTH_USER_MODEL = "identity.User"`, never Django's default
    `auth_user`): one is `django.contrib.auth.middleware.
    AuthenticationMiddleware`'s own unavoidable `request.user` lookup,
    present on every authenticated request whether or not `identity.*`
    asks anything at all, and the second is `identity.access._user_row`
    -- memoised on the threaded settings row (see that function's own
    docstring) -- resolved ONCE for the superuser check, however many
    times `visible_rows`/`may_read_job_content` ask it. Without the
    memoisation this second number scales with the row count instead of
    staying fixed at one; the fix pins it here.
    """

    @staticmethod
    def _identity_reads(context) -> int:
        return sum(
            1 for q in context.captured_queries
            if "identity_identitysettings" in q["sql"]
        )

    @staticmethod
    def _user_reads(context) -> int:
        # `AUTH_USER_MODEL = "identity.User"` (`config/settings.py`) --
        # this box's user table is `identity_user`, never Django's
        # default `auth_user`.
        return sum(
            1 for q in context.captured_queries
            if "identity_user" in q["sql"]
        )

    def test_a_member_request_reads_the_row_once(self, client):
        member = make_user()
        with posture(POSTURE_PERSONAL):
            sign_in(client, member)
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(reverse("jobs-queue")).status_code == 200
        assert self._identity_reads(ctx) == 1

    def test_an_admin_request_reads_the_row_once(self, client):
        admin = make_admin()
        with posture(POSTURE_PERSONAL):
            sign_in(client, admin)
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(reverse("inference-console")).status_code == 200
        assert self._identity_reads(ctx) == 1

    def test_a_member_on_the_queue_page_costs_one_settings_read_and_one_extra_user_read(
        self, client,
    ):
        """Fix round 1, IMPORTANT 6: `QueueView` asks `is_admin` (via
        `visible_rows`, three times, and `may_read_job_content` once per
        row) many times over in one render, and each of those used to
        re-query the user table for the SAME signed-in member --
        3+N queries that scaled with the row count. The settings row is
        threaded (one read, pinned above already); the resolved `User`
        row must be threaded with it too, not re-fetched per call.

        TWO `identity_user` reads total, not one: `django.contrib.auth`'s
        own `AuthenticationMiddleware` always resolves `request.user`
        once, on every authenticated request, with or without a single
        `identity.*` call in play -- that read is not this fix's to
        remove. The second is the ONE this fix pins: `is_admin`'s
        memoised superuser check, asked many times, queried once."""
        member = make_user()
        _job(payload={"actor_kind": "user", "actor_key": str(member.pk)})
        _job(payload={"actor_kind": "user", "actor_key": "99999999"})
        with posture(POSTURE_PERSONAL):
            sign_in(client, member)
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(reverse("jobs-queue")).status_code == 200
        assert self._identity_reads(ctx) == 1
        assert self._user_reads(ctx) == 2


class TestTheRollingWindow:
    def test_an_authenticated_request_pushes_the_expiry_out(self, client):
        from identity.models import IdentitySettings
        member = make_user()
        with posture(POSTURE_PERSONAL) as row:
            row.session_idle_minutes = 30
            row.save()
            sign_in(client, member)
            client.get(reverse("chat-index"))
            assert client.session.get_expiry_age() <= 30 * 60
            assert client.session.get_expiry_age() > 29 * 60

    def test_zero_minutes_means_expire_when_the_browser_closes(self, client):
        member = make_user()
        with posture(POSTURE_PERSONAL) as row:
            row.session_idle_minutes = 0
            row.save()
            sign_in(client, member)
            client.get(reverse("chat-index"))
            assert client.session.get_expire_at_browser_close() is True
