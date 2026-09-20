"""The posture page -- posture, library posture, session window, and the
administrator-content toggle.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity.contracts import actions
from identity.contracts.postures import (
    POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
)
from identity.models import AuditEvent, IdentitySettings
from identity.tests._helpers import make_admin, make_user, posture, seed_sweep_posture, sign_in

pytestmark = pytest.mark.django_db

REAL_KEY = "a-real-key-for-this-test-only"


@pytest.fixture(autouse=True)
def _clean(settings):
    seed_sweep_posture()
    settings.DEBUG = False
    settings.SECRET_KEY = REAL_KEY


class TestAccess:
    def test_it_is_reachable_in_the_open_posture_by_anybody(self, client):
        """Somebody has to be able to turn accounts ON, and in the open
        posture the box's only principal is an administrator."""
        with posture(POSTURE_OPEN):
            assert client.get(reverse("identity-settings")).status_code == 200

    def test_a_member_is_refused(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            assert client.get(reverse("identity-settings")).status_code == 403

    def test_a_member_posting_is_refused_too(self, client):
        """T9 review round 1 IMPORTANT: the POST path, pinned
        explicitly rather than trusted by inference from the GET check
        above."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_ENTERPRISE,
                                    "admin_sees_content": "on",
                                    "library_posture": "open",
                                    "session_idle_minutes": "720"})
        assert response.status_code == 403
        assert IdentitySettings.get_solo().admin_sees_content is False


class TestTheOpenPostureSignInLink:
    """W-1 (IA-2 walkthrough finding): the owner twice could not find how
    to sign in on an open box -- open posture renders zero auth chrome by
    design, and the login page was linked from nowhere. RULING (binding,
    protects Global Constraint 10): a STATIC link, no queries -- this
    page already reads only `identity_identitysettings`
    (`test_zero_queries.py::_MOUNTS` includes `identity-settings`), and
    the sentence below adds no read of its own."""

    def test_the_open_posture_renders_the_sign_in_link(self, client):
        with posture(POSTURE_OPEN):
            body = client.get(reverse("identity-settings")).content.decode()
        assert "This box has no sign-in chrome while it is open." in body
        assert reverse("identity-login") in body

    def test_an_enterprise_box_does_not_render_it(self, client):
        """The nav chrome exists there (`identity/tests/test_shell_nav.py`
        already pins the sign-out control and the admin Settings link),
        so the static fallback sentence has nothing to add."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-settings")).content.decode()
        assert "no sign-in chrome" not in body


class TestTheOpenDebugNotice:
    """H22 review round 1 (F-1/D-1): `identity.W003`'s page-level echo --
    only when the posture is open AND DEBUG is on, the exact state the
    check itself fires on."""

    def test_it_renders_when_open_and_debug_is_on(self, client, settings):
        settings.DEBUG = True
        with posture(POSTURE_OPEN):
            body = client.get(reverse("identity-settings")).content.decode()
        assert "This box is open with DEBUG on" in body
        assert "Leaving the open posture" in body

    def test_it_is_silent_when_debug_is_off(self, client):
        with posture(POSTURE_OPEN):
            body = client.get(reverse("identity-settings")).content.decode()
        assert "This box is open with DEBUG on" not in body

    def test_it_is_silent_off_the_open_posture_even_with_debug_on(self, client, settings):
        settings.DEBUG = True
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-settings")).content.decode()
        assert "This box is open with DEBUG on" not in body


class TestTheLibraryPostureControl:
    """T9 review round 1 MINOR: unlike `admin_sees_content`,
    `library_posture` DOES have a posture branch --
    `identity.services.set_posture` resets it to "open" and ignores an
    explicit value whenever the box is (or becomes) personal -- so the
    page must not offer a choice it will not honour."""

    def test_the_select_is_not_offered_in_the_personal_posture(self, client):
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-settings")).content.decode()
        # Django's default `auto_id` names this widget's id
        # `id_library_posture`; its absence is a clean signal that no
        # `<select>` for the field was rendered at all, whatever
        # attribute order Django chooses.
        assert 'id="id_library_posture"' not in body
        assert '<input type="hidden" name="library_posture" value="open">' in body
        assert "always" in body.lower()

    def test_the_select_is_offered_in_the_enterprise_posture(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-settings")).content.decode()
        assert 'id="id_library_posture"' in body

    def test_the_enterprise_select_is_live_and_says_what_it_does(self, client):
        """IA-1 rendered this DISABLED, with a helptext pointing at IA-2,
        because `tools/rag/access.py` did not read the column yet.
        `may_see_unlabelled` reads it now, so a disabled control -- and a
        helptext still promising a later phase -- would be the lie in
        the other direction."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-settings")).content.decode()
        assert 'id="id_library_posture"' in body
        # Scoped to the SELECT'S OWN OPENING TAG, not a page-wide
        # substring check: the shared shell's own base CSS
        # (`foundation/templates/_shell.html`) carries an unrelated
        # `button:disabled { ... }` rule on every page, which a blanket
        # `"disabled" not in body` would trip on regardless of this
        # select's own attribute. Slicing to the tag also catches EITHER
        # rendering Django could produce for a disabled widget --
        # `attrs["disabled"] = "disabled"` (`disabled="disabled"`) or
        # `attrs["disabled"] = True` (a bare `disabled`) -- which a check
        # for the literal string `disabled="disabled"` alone would miss.
        id_index = body.index('id="id_library_posture"')
        tag_start = body.rindex("<select", 0, id_index)
        tag_end = body.index(">", id_index)
        select_tag = body[tag_start:tag_end]
        assert "disabled" not in select_tag
        assert "ia-2" not in body.lower()
        assert "carrying no label" in body

    def test_saving_from_the_enterprise_posture_round_trips_the_selected_value(self, client):
        """The select is LIVE now, so the browser submits its value and
        the page shows it selected on the way back. The IA-1 version of
        this test asserted the hidden mirror field's behaviour, and that
        field is gone."""
        from identity.contracts.postures import LIBRARY_LOCKED
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            sign_in(client, admin)
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_ENTERPRISE,
                                    "library_posture": LIBRARY_LOCKED,
                                    "session_idle_minutes": "720"}, follow=True)
            assert response.status_code == 200
            assert IdentitySettings.get_solo().library_posture == LIBRARY_LOCKED

    def test_saving_from_the_personal_posture_still_succeeds(self, client):
        """The hidden `library_posture` field keeps the (required)
        form submittable even though its select is not rendered."""
        admin = make_admin()
        with posture(POSTURE_PERSONAL):
            sign_in(client, admin)
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_PERSONAL,
                                    "library_posture": "open",
                                    "session_idle_minutes": "720"}, follow=True)
        assert response.status_code == 200
        assert IdentitySettings.get_solo().library_posture == "open"


class TestTheContentToggle:
    def test_it_is_present_in_personal_as_well_as_enterprise(self, client):
        """The administer/read split has NO posture branch, so the
        control that governs it appears in both."""
        for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
            with posture(name):
                sign_in(client, make_admin())
                body = client.get(reverse("identity-settings")).content.decode()
            assert "admin_sees_content" in body
            client.logout()

    def test_it_defaults_off_and_the_page_says_what_off_means(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-settings")).content.decode()
        assert IdentitySettings.get_solo().admin_sees_content is False
        assert "conversation" in body.lower()

    def test_turning_it_on_takes_effect_on_the_next_request_with_one_audit_row(self, client):
        """Read per request through `get_solo()`, so no restart and no
        re-login."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("identity-settings"),
                        {"posture": POSTURE_ENTERPRISE, "admin_sees_content": "on",
                         "library_posture": "open", "session_idle_minutes": "720"})
            assert IdentitySettings.get_solo().admin_sees_content is True
        assert AuditEvent.objects.filter(
            action=actions.ADMIN_CONTENT_ACCESS_CHANGED).count() == 1


class TestTheThreeRefusals:
    def test_switching_with_no_admin_re_renders_with_the_reason(self, client):
        # THE POSTURE ASSERTION MUST BE INSIDE THE `with` BLOCK: `posture()`
        # restores the settings row to whatever it was BEFORE the block on
        # exit -- under the sweep (`FARABUNKER_TEST_POSTURE`), that "before"
        # value is `personal`/`enterprise`, not `open`, so an assertion
        # placed after the block would read the RESTORED value instead of
        # the refusal's own effect (leaving the box at the posture it
        # actually pinned).
        with posture(POSTURE_OPEN):
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_PERSONAL,
                                    "library_posture": "open",
                                    "session_idle_minutes": "720"},
                                   follow=True)
            assert "superuser" in response.content.decode().lower()
            assert IdentitySettings.get_solo().posture == POSTURE_OPEN

    def test_switching_with_debug_on_re_renders_with_the_reason(self, client, settings):
        make_admin()
        settings.DEBUG = True
        with posture(POSTURE_OPEN):
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_PERSONAL,
                                    "library_posture": "open",
                                    "session_idle_minutes": "720"},
                                   follow=True)
        assert "debug" in response.content.decode().lower()

    def test_switching_with_the_default_key_re_renders_with_the_reason(self, client, settings):
        from config.settings import DEV_SECRET_KEY
        make_admin()
        settings.SECRET_KEY = DEV_SECRET_KEY
        with posture(POSTURE_OPEN):
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_PERSONAL,
                                    "library_posture": "open",
                                    "session_idle_minutes": "720"},
                                   follow=True)
        assert "key" in response.content.decode().lower()

    def test_a_refusal_is_never_a_500(self, client):
        with posture(POSTURE_OPEN):
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_PERSONAL,
                                    "library_posture": "open",
                                    "session_idle_minutes": "720"})
        assert response.status_code in (200, 302)
        assert "Traceback" not in response.content.decode(errors="replace")


class TestAnInvalidPostIsNeverSilent:
    """FOLLOW-UP closed in consolidation round 3: `settings_page` used to
    fall straight through to the re-render on an invalid `PostureForm`
    with no `messages.error` at all -- unlike every other mutation on
    this page. `_flash_form_errors` (shared with `user_create`/
    `user_edit`) closes that gap.

    A SECOND gap survived that fix (F7, Coherence Wave B): the re-render
    itself -- `_flash_form_errors` added the flash message but left the
    underlying "fall through to `return render(...)`" shape unchanged,
    unlike `groups` (`identity/views.py:356-375`), which redirects on
    the identical form-invalid condition. `test_it_redirects_rather_
    than_re_rendering` below pins the fix."""

    def test_an_invalid_session_idle_minutes_flashes_the_field_error(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_ENTERPRISE,
                                    "library_posture": "open",
                                    "session_idle_minutes": "not-a-number"},
                                   follow=True)
        assert response.status_code == 200
        body = response.content.decode()
        assert "session_idle_minutes:" in body

    def test_it_redirects_rather_than_re_rendering(self, client):
        """F7 (Coherence Wave B): `settings_page` used to fall through to
        a RE-RENDER on an invalid form, unlike its sibling `groups`
        (`identity/views.py:356-375`), which redirects on the same
        condition -- and unlike its OWN docstring, which already claimed
        the redirect shape. Asserted directly, without `follow=True`, so
        a regression back to a bare 200 re-render (same status code a
        redirect-then-follow also produces) cannot hide behind the test
        above."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_ENTERPRISE,
                                    "library_posture": "open",
                                    "session_idle_minutes": "not-a-number"})
        assert response.status_code == 302
        assert response.url == reverse("identity-settings")
