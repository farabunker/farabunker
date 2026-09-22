"""The settings area's table and its `/settings/` landing route (UI-2).

TWO CLAIMS, AND THEY ARE DIFFERENT. `foundation/settings_area.py` says
which entries exist and who may see each; the route says where a viewer
who clicks the app bar's one `Settings` entry actually lands. The
sidebar that renders the same table is asserted in `test_shell.py`,
including the drift test that pins its first link to this route's
redirect target.

The redirect is exercised through `/settings/` itself rather than by
calling `first_entry` twice: the point of the route is that it reads the
posture row and the principal off a REAL request, and a unit call would
assert the table against itself.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from foundation.settings_area import (
    ACCOUNTS_ADMIN, ADMIN, EVERYONE, SETTINGS_GROUPS, first_entry,
    preserve_assistant_flag, settings_redirect, visible_entries, with_assistant_flag,
)
from foundation.tests._helpers import (
    make_admin, make_user, posture, seed_sweep_posture, sign_in,
)
from identity.contracts.postures import (
    POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _a_box(db):
    seed_sweep_posture()


def _labels(**kwargs) -> list[str]:
    return [entry.label for entry in visible_entries(**kwargs)]


class TestTheTable:
    def test_every_entry_names_a_route_this_box_owns(self):
        """Anti-rot: a table of url names nothing reverses is a sidebar
        of 500s waiting for the first admin to open it."""
        for _group, entries in SETTINGS_GROUPS:
            for entry in entries:
                assert reverse(entry.url_name)

    def test_every_entry_carries_a_gate_the_table_understands(self):
        gates = {entry.gate for _g, entries in SETTINGS_GROUPS for entry in entries}
        assert gates <= {EVERYONE, ADMIN, ACCOUNTS_ADMIN}

    def test_a_household_box_gets_setup_and_nothing_else(self):
        """`is_admin` is True in the open posture -- everybody operates a
        household box -- so every UNGATED-BY-FEATURE SETUP entry
        renders; there are no accounts to administer, so ACCESS and BOX
        render nothing at all, label included.

        `features` is omitted here (defaults to empty -- `visible_
        entries`'s own docstring), so "Engine files" -- gated on
        `feature="vision"` -- stays off this particular list on
        purpose: `TestTheVisionFeatureGate`, below, is the one class
        that turns the flag on and asserts it joins Setup."""
        assert _labels(admin=True, posture="open") == [
            "Models", "Library", "Chat", "Job execution", "Agent library",
            "Install guides"]

    def test_a_member_gets_only_the_ungated_entry(self):
        """R1, carried over from the Manage row unchanged: every other
        entry is class S, and a nav entry that can only ever refuse is
        not navigation."""
        assert _labels(admin=False, posture=POSTURE_ENTERPRISE) == ["Install guides"]

    def test_an_administrator_on_an_accounts_box_gets_every_group(self):
        assert _labels(admin=True, posture=POSTURE_ENTERPRISE) == [
            "Models", "Library", "Chat", "Job execution", "Agent library",
            "Install guides",
            "Accounts", "Groups", "Entitlements", "Tool access", "Agent access",
            "Identity & security",
        ]

    def test_the_personal_posture_is_an_accounts_box_too(self):
        """`personal` is not `open`: it has accounts, so it has account
        administration."""
        assert "Accounts" in _labels(admin=True, posture=POSTURE_PERSONAL)

    @pytest.mark.parametrize("admin", [True, False])
    @pytest.mark.parametrize(
        "box", [POSTURE_OPEN, POSTURE_PERSONAL, POSTURE_ENTERPRISE])
    def test_nobody_is_ever_sent_nowhere(self, admin, box):
        """`first_entry` indexes [0] -- this is what makes that safe for
        every principal this box has, rather than a comment claiming it
        is."""
        assert first_entry(admin=admin, posture=box).label


class TestTheVisionFeatureGate:
    """`Entry.feature="vision"` (Engine files, 2026-09-02): the ONE entry
    in this table gated on a `FARABUNKER_FEATURES` token rather than
    only on standing. `features=` defaults to empty on every OTHER test
    in this module -- deliberately (see `test_a_household_box_gets_
    setup_and_nothing_else`'s own docstring) -- so this class is where
    passing a real `features` set is actually exercised."""

    def test_an_administrator_with_the_flag_on_gets_engine_files_in_setup(self):
        assert _labels(admin=True, posture="open", features=frozenset({"vision"})) == [
            "Models", "Library", "Chat", "Job execution", "Agent library",
            "Engine files", "Install guides"]

    def test_a_member_does_not_get_it_even_with_the_flag_on(self):
        """ADMIN gate first, feature gate second -- either refusing is
        enough, and standing still refuses here regardless of the
        flag."""
        assert "Engine files" not in _labels(
            admin=False, posture=POSTURE_ENTERPRISE, features=frozenset({"vision"}))

    def test_an_administrator_with_the_flag_off_does_not_get_it(self):
        assert "Engine files" not in _labels(admin=True, posture="open", features=frozenset())


class TestTheLandingRoute:
    def test_a_household_box_lands_on_the_first_setup_entry(self, client):
        with posture(POSTURE_OPEN):
            response = client.get(reverse("settings-index"))
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("inference-console")

    def test_an_administrator_lands_on_the_first_setup_entry(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.get(reverse("settings-index"))
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("inference-console")

    def test_a_member_lands_on_the_one_section_a_member_may_open(self, client):
        """Not on a 403: the whole point of a landing route is that the
        bar's one entry never leads somewhere that refuses you."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("settings-index"))
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("setup-index")

    def test_an_anonymous_visitor_is_not_bounced_to_sign_in(self, client):
        """Class P (`identity/routes.py`), and this is what that buys:
        the redirect resolves to the public install guidance, which is
        the page a person needs before they can sign in at all."""
        with posture(POSTURE_ENTERPRISE):
            response = client.get(reverse("settings-index"))
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("setup-index")


class TestTheAssistantOpenFlagIsAStringOperation:
    """`with_assistant_flag` -- the ONE place the chrome's open
    parameter is appended to a URL, wherever it is written: the
    sidebar's links, a settings form's action, and every settings POST
    view's redirect back.

    PARSED, NOT SUBSTRING-MATCHED, and fragment-aware. Both matter for
    the same reason: this runs on URLs other code built (an endpoint
    override's own query string, a card's anchor), and a `"assistant=1"
    in url` test would true-positive on `notassistant=1` while a bare
    append would bury the parameter inside a fragment where no query
    parser would ever see it.
    """

    def test_it_appends_the_parameter_to_a_bare_url(self):
        assert with_assistant_flag("/chat/settings/") == "/chat/settings/?assistant=1"

    def test_it_is_idempotent(self):
        once = with_assistant_flag("/chat/settings/")
        assert with_assistant_flag(once) == once

    def test_it_joins_an_existing_query_rather_than_starting_a_second_one(self):
        assert with_assistant_flag("/inference/?endpoint=http%3A%2F%2Fx") == (
            "/inference/?endpoint=http%3A%2F%2Fx&assistant=1")

    def test_it_writes_the_parameter_before_a_fragment_not_into_it(self):
        assert with_assistant_flag("/rag/settings/#retrieval-top-k") == (
            "/rag/settings/?assistant=1#retrieval-top-k")

    def test_a_lookalike_parameter_does_not_count_as_the_real_one(self):
        assert with_assistant_flag("/x/?notassistant=1") == "/x/?notassistant=1&assistant=1"

    def test_a_stale_value_is_replaced_never_appended_to(self):
        """N2 (review round 1). Idempotence keys on the VALUE, not on the
        key being present: `?assistant=0` used to come back untouched --
        a URL this function had promised carries `?assistant=1` and which
        would render a SHUT panel. Appending a second copy would be no
        better, because `request.GET.get` reads the FIRST. So a value
        that is not `1` is REPLACED, and the result carries the parameter
        exactly once, as the docstring says."""
        assert with_assistant_flag("/x/?assistant=0") == "/x/?assistant=1"

    def test_a_doubled_parameter_is_normalised_to_one(self):
        assert with_assistant_flag("/x/?assistant=1&assistant=1") == "/x/?assistant=1"

    def test_replacing_a_stale_value_keeps_the_other_parameters_and_the_fragment(self):
        assert with_assistant_flag("/x/?endpoint=y&assistant=0#z") == (
            "/x/?endpoint=y&assistant=1#z")


class TestASettingsRedirectPreservesTheFlagItWasGiven:
    """`preserve_assistant_flag` -- the rule every settings POST view
    redirects through: the panel stays open across a save because the
    form's own action carried the flag into `request.GET`, and the
    redirect back carries it out again. A request without the flag
    redirects EXACTLY where it always did."""

    def test_it_appends_when_the_request_carries_the_flag(self, rf):
        request = rf.post("/rag/settings/update/?assistant=1")
        assert preserve_assistant_flag(request, "/rag/settings/") == (
            "/rag/settings/?assistant=1")

    def test_it_leaves_the_url_alone_when_the_request_does_not(self, rf):
        request = rf.post("/rag/settings/update/")
        assert preserve_assistant_flag(request, "/rag/settings/") == "/rag/settings/"

    @pytest.mark.parametrize("raw", ["0", "", "yes", "11"])
    def test_only_the_one_value_counts(self, rf, raw):
        """The same `== "1"` read the panel's own context processor
        makes -- a flag that opened the panel for any truthy-looking
        value would be two definitions of open."""
        request = rf.post(f"/rag/settings/update/?assistant={raw}")
        assert preserve_assistant_flag(request, "/rag/settings/") == "/rag/settings/"

    def test_it_is_idempotent_on_a_target_that_already_carries_it(self, rf):
        request = rf.post("/rag/settings/update/?assistant=1")
        assert preserve_assistant_flag(request, "/rag/settings/?assistant=1") == (
            "/rag/settings/?assistant=1")

    def test_settings_redirect_takes_a_route_name_like_redirect_does(self, rf):
        request = rf.post(f"{reverse('rag-settings-update')}?assistant=1")
        response = settings_redirect(request, "rag-settings")
        assert response.status_code == 302
        assert response.headers["Location"] == f"{reverse('rag-settings')}?assistant=1"

    def test_settings_redirect_takes_a_built_url_too(self, rf):
        request = rf.post(f"{reverse('rag-settings-update')}?assistant=1")
        response = settings_redirect(request, "/rag/settings/#retrieval-top-k")
        assert response.headers["Location"] == "/rag/settings/?assistant=1#retrieval-top-k"

    def test_settings_redirect_is_a_plain_redirect_without_the_flag(self, rf):
        request = rf.post(reverse("rag-settings-update"))
        response = settings_redirect(request, "rag-settings")
        assert response.headers["Location"] == reverse("rag-settings")


class TestTheLandingRouteCarriesTheFlagThrough:
    """THE ROUTE'S OWN PASSTHROUGH, and only that: `/settings/` is a
    redirect, so arriving there with the panel open must not close it on
    the way to the page it resolves to.

    WHAT SENDS A REQUEST HERE CARRYING THE FLAG is the app bar's one
    `Settings` entry, and that is a different claim in a different
    place: `foundation/tests/test_shell.py::
    TestTheBarsSettingsEntryCarriesTheAssistantPanel` reads that href out
    of a rendered bar, and `agents/chat/tests/test_assistant_panel.py`
    follows it end to end. Named here because this class used to assert
    the bar in its own docstring while testing only the route -- which
    is how the bar went a whole round without the flag (review round 1,
    I2)."""

    def test_the_flag_survives_the_landing_redirect(self, client):
        with posture(POSTURE_OPEN):
            response = client.get(f"{reverse('settings-index')}?assistant=1")
        assert response.headers["Location"] == f"{reverse('inference-console')}?assistant=1"

    def test_a_plain_landing_is_unchanged(self, client):
        with posture(POSTURE_OPEN):
            response = client.get(reverse("settings-index"))
        assert response.headers["Location"] == reverse("inference-console")
