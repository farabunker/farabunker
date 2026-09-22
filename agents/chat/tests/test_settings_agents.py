"""`/settings/agents/` -- the box-wide agent list.

ITS OWN NEVER-500 MODULE, because `config/urls.py` mounts this route
from `agents/chat/agent_admin_urls.py` and `agents/chat/tests/
test_never_500.py` derives its sweep from `agents.chat.urls.urlpatterns`
-- which cannot see it. Exactly the position the three
`/settings/assistant/` routes are in.

WHAT THIS MODULE DOES NOT RE-PIN. The settings-area registration is
swept off `SETTINGS_GROUPS` itself by tests that already exist --
`foundation/tests/test_shell.py` (the sidebar drift test, the unsaved-
input guard, the `:target` rule), `foundation/tests/test_settings_help.py`
(the card, its gate, its title, its anchor) and `foundation/tests/
test_page_names.py` (the `<h1>`/`<title>`). The two tests at the bottom
of this file assert only the things those sweeps cannot: that the entry
is where it is meant to be, under the noun it is meant to have.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401
    grant, make_admin, make_agent, make_entitlement, make_user, posture, sign_in,
    user_principal,
)
from agents.labels import set_agent_labels
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db

_NEVER_500_STATUSES = frozenset({200, 302, 403, 404})


class TestTheSettingsList:
    def test_it_lists_every_agent_for_an_administrator(self, client):
        """THE WHOLE POINT OF THE SECOND MOUNT. `labellable_agents` is
        unfiltered by principal, so a member's own row is here beside
        the box-wide one even with `admin_sees_content` off -- which is
        the default, and which `visible_agents(principal)` would have
        hidden."""
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="members-row", name="Member's row",
                       **owner_fields(user_principal(member)))
            make_agent(slug="wide-row", name="Wide row", box_wide=True)
            sign_in(client, make_admin())
            body = client.get(reverse("settings-agents")).content.decode()
        assert "Member&#x27;s row" in body
        assert "Wide row" in body

    def test_it_is_refused_to_a_member_at_the_gate(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            assert client.get(reverse("settings-agents")).status_code == 403

    def test_it_carries_its_own_is_admin_check_as_well_as_the_gate(self):
        """The gate is not the only way a view function can be reached
        -- the reasoning `agents/chat/views/assistant.py` records for its
        own three views."""
        import inspect

        from agents.chat.views import agents_admin

        assert "is_admin" in inspect.getsource(agents_admin)

    def test_it_shows_each_rows_audience_and_owner(self, client):
        """The declared sentence, not a substring of it: a user-facing
        sentence is declared once, in Python (`agents/chat/views/
        agents_admin.py`), never typed into a template."""
        from agents.chat.views.agents_admin import REACH_EVERYONE, REACH_GIVEN

        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="audience-shown", name="Audience shown", box_wide=True,
                       **owner_fields(user_principal(member)))
            make_agent(slug="audience-narrow", name="Audience narrow",
                       **owner_fields(user_principal(member)))
            sign_in(client, make_admin())
            body = client.get(reverse("settings-agents")).content.decode()
        assert REACH_EVERYONE in body
        assert REACH_GIVEN in body
        assert f"user:{member.pk}" in body

    def test_it_counts_the_entitlements_that_restrict_a_row(self, client):
        """A BARE COUNT, NEVER A NAME -- the same rule `/chat/agents/`'s
        own restriction column follows. Naming one here would be the
        entitlement non-disclosure gate, which the route matrix sweeps
        these routes for."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            labelled = make_agent(slug="restricted", name="Restricted",
                                  **owner_fields(user_principal(admin)))
            make_agent(slug="unrestricted", name="Unrestricted",
                       **owner_fields(user_principal(admin)))
            legal = make_entitlement(name="Radioactive")
            set_agent_labels(user_principal(admin), labelled, {legal.pk})
            sign_in(client, admin)
            body = client.get(reverse("settings-agents")).content.decode()
        assert "Radioactive" not in body
        assert "<td>1</td>" in body
        assert "<td>0</td>" in body

    def test_every_row_links_to_the_SAME_edit_route(self, client):
        """ONE EDIT ROUTE, TWO LIST PAGES. There is no second editor and
        no second form; this page adds no write endpoint at all."""
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="linked", name="Linked")
            sign_in(client, make_admin())
            body = client.get(reverse("settings-agents")).content.decode()
        assert reverse("chat-agent-edit", args=[agent.pk]) in body

    def test_each_link_carries_this_page_as_its_next(self, client):
        """So the editor comes back HERE rather than to the member-facing
        list -- which is what Task 8 review N3 was about.

        `|urlencode`'s DEFAULT SAFE CHARACTERS INCLUDE `/`, which is why
        the expected value is a readable path rather than a percent-
        encoded one: the filter is there for a query string this page
        was reached with (the assistant panel's `?assistant=1`), so the
        whole value survives as ONE parameter instead of splitting the
        link -- which the second half of this test is about."""
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="returning", name="Returning")
            sign_in(client, make_admin())
            url = reverse("settings-agents")
            body = client.get(url).content.decode()
            flagged = client.get(url + "?assistant=1").content.decode()
        edit = reverse("chat-agent-edit", args=[agent.pk])
        assert f'{edit}?next={url}"' in body
        assert f'{edit}?next={url}%3Fassistant%3D1"' in flagged

    def test_the_list_costs_the_same_at_one_agent_and_at_twenty_five(self, client):
        """TWO BATCH READS FOR THE WHOLE PAGE, never one per row --
        the discipline `/chat/access/` documents. Both bodies assert a
        distinct name, so the equality is not two identical counts for
        two pages that listed nothing."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="settings-one", name="Settings one")
            sign_in(client, make_admin())
            client.get(reverse("settings-agents"))         # warm-up, unmeasured
            with CaptureQueriesContext(connection) as one:
                first = client.get(reverse("settings-agents")).content.decode()
            for index in range(25):
                make_agent(slug=f"settings-many-{index}", name=f"Settings many {index}")
            with CaptureQueriesContext(connection) as many:
                second = client.get(reverse("settings-agents")).content.decode()
        assert "Settings one" in first
        assert "Settings many 24" in second
        assert len(many) == len(one)


class TestItNever500s:
    """The four conditions `agents/chat/tests/test_never_500.py` applies
    to every `/chat/` route, applied here because that sweep cannot see
    this one."""

    def _assert(self, response):
        assert response.status_code in _NEVER_500_STATUSES, response.status_code
        assert "Traceback" not in response.content.decode(errors="replace")
        return response

    def test_a_normal_read(self, client):
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="sweep-normal")
            sign_in(client, make_admin())
            self._assert(client.get(reverse("settings-agents")))

    def test_with_no_agents_at_all(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = self._assert(
                client.get(reverse("settings-agents"))).content.decode()
        # The empty state is a row of its own, not a blank page -- and the
        # anchor the help card cites renders in it too.
        assert 'id="agent-library"' in body
        assert "No agents on this box yet." in body

    def test_with_a_row_naming_an_unregistered_role(self, client):
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="sweep-role", llm_role="not.a.registered.role")
            sign_in(client, make_admin())
            self._assert(client.get(reverse("settings-agents")))

    def test_a_post_is_a_405_not_a_traceback(self, client):
        """`require_safe`: this page writes nothing, so a POST is a
        DECLARED 405 rather than a branch nobody wrote."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("settings-agents"), {})
        assert response.status_code == 405
        assert "Traceback" not in response.content.decode(errors="replace")


class TestTheSettingsEntry:
    def test_the_nav_carries_it_under_its_own_noun(self):
        """"Agent library", not "Agents": the Access group already
        carries "Agent access" for a different job, and two entries
        sharing one noun is a nav an operator has to learn rather than
        read."""
        from foundation.settings_area import SETTINGS_GROUPS

        labels = {entry.label for _group, entries in SETTINGS_GROUPS
                  for entry in entries}
        assert "Agent library" in labels
        assert "Agent access" in labels

    def test_it_sits_in_the_setup_group_and_is_admin_gated(self):
        from foundation.settings_area import ADMIN, SETTINGS_GROUPS

        setup = dict(SETTINGS_GROUPS)["Setup"]
        entry = next(e for e in setup if e.url_name == "settings-agents")
        assert entry.gate == ADMIN
        assert entry.feature is None
