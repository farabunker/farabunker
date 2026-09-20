"""`/chat/access/` -- which entitlement an agent or a flow needs (spec
sections 6.11, 9.6).

CLASS S. Labelling an agent or a flow is library-wide operator policy,
the same shape `chat/tests/test_tool_entitlements_page.py` covers for
tools -- this module carries the identical six assertions for the two
new tables, plus the two facts unique to this page: a `resident=True`
row is offered like any other, and the page warns that a label reaches
the agent's own creator (decision 35).
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (
    make_admin, make_agent, make_entitlement, make_flow, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in,
)
from agents.models import AgentEntitlement, FlowEntitlement
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestTheAgentAccessPage:
    def test_an_admin_sees_every_agent_and_a_member_gets_403(self, client):
        admin, member = make_admin(), make_user()
        agent = make_agent(slug="general")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.get(reverse("chat-agent-entitlements"))
            assert response.status_code == 200
            assert agent.slug.encode() in response.content
            other = client.__class__()
            sign_in(other, member)
            assert other.get(reverse("chat-agent-entitlements")).status_code == 403

    def test_posting_agent_labels_writes_them_and_redirects(self, client):
        admin = make_admin()
        agent = make_agent(slug="posted-agent")
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-agent-entitlements"),
                                   {"kind": "agent", "pk": str(agent.pk),
                                    "op": "add", "add": [str(finance.pk)]})
        assert response.status_code == 302
        assert AgentEntitlement.objects.filter(agent=agent, entitlement=finance).exists()

    def test_posting_flow_labels_writes_them_and_redirects(self, client):
        admin = make_admin()
        flow = make_flow(slug="posted-flow")
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-agent-entitlements"),
                                   {"kind": "flow", "pk": str(flow.pk),
                                    "op": "add", "add": [str(finance.pk)]})
        assert response.status_code == 302
        assert FlowEntitlement.objects.filter(flow=flow, entitlement=finance).exists()

    def test_removing_every_active_entitlement_leaves_the_agent_unlabelled(self, client):
        """RE-PINNED IN PHASE 2 and RENAMED, for the reason its tool-page
        twin gives: "posting an empty set" named a field shape, not a
        behaviour. With add/remove, clearing an agent is ticking
        everything in the Active pane and pressing Remove. The behaviour
        that matters -- an agent can be returned to unlabelled, which
        WIDENS it to everyone signed in -- is unchanged and pinned
        here."""
        admin = make_admin()
        agent = make_agent(slug="unlabel-me")
        finance = make_entitlement(name="Finance")
        AgentEntitlement.objects.create(agent=agent, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("chat-agent-entitlements"),
                        {"kind": "agent", "pk": str(agent.pk), "op": "remove",
                         "remove": [str(finance.pk)]})
        assert AgentEntitlement.objects.filter(agent=agent).count() == 0

    def test_an_unknown_kind_flashes_and_redirects_not_a_silent_no_op(self, client):
        """F7 (Coherence Wave B): a raw 400 dumped the operator out of the
        chrome -- no shell, no sidebar, no flash. Now the house
        flash-and-redirect shape every other mutation on this platform
        uses."""
        admin = make_admin()
        agent = make_agent(slug="unknown-kind-agent")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-agent-entitlements"),
                                   {"kind": "not-a-kind", "pk": str(agent.pk),
                                    "op": "add", "add": []}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain
        assert "is not a labellable kind" in response.content.decode()
        assert AgentEntitlement.objects.count() == 0

    def test_an_unknown_row_flashes_and_redirects_not_a_silent_no_op(self, client):
        """F7 (Coherence Wave B): the sibling raw-400 in the same view --
        a well-formed `kind` naming no real row."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-agent-entitlements"),
                                   {"kind": "agent", "pk": "999999",
                                    "op": "add", "add": []}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain
        assert "No agent 999999 on this install" in response.content.decode()
        assert AgentEntitlement.objects.count() == 0

    def test_a_non_numeric_entitlement_id_is_refused_not_crashed(self, client):
        admin = make_admin()
        agent = make_agent(slug="non-numeric-ent-agent")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-agent-entitlements"),
                                   {"kind": "agent", "pk": str(agent.pk),
                                    "op": "add", "add": ["abc"]}, follow=True)
        assert response.status_code == 200
        assert b"Traceback" not in response.content
        assert AgentEntitlement.objects.count() == 0

    def test_an_unoffered_entitlement_is_refused(self, client):
        admin = make_admin()
        agent = make_agent(slug="unoffered-ent-agent")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("chat-agent-entitlements"),
                        {"kind": "agent", "pk": str(agent.pk),
                         "op": "add", "add": ["99999"]}, follow=True)
        assert AgentEntitlement.objects.count() == 0

    def test_labels_round_trip(self, client):
        admin = make_admin()
        agent = make_agent(slug="round-trip-agent")
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("chat-agent-entitlements"),
                        {"kind": "agent", "pk": str(agent.pk),
                         "op": "add", "add": [str(finance.pk)]})
            body = client.get(reverse("chat-agent-entitlements")).content
        # RE-PINNED IN PHASE 2. The chip-checkbox group is a two-pane
        # transfer panel now, so an ACTIVE entitlement is a `remove`
        # checkbox in the right pane rather than a `checked` box in a
        # flat list -- there is no `checked` attribute anywhere in the
        # panel, because which pane a row sits in IS its state. Same
        # round trip, and the summary line above the panel states it in
        # words as well.
        assert f'<input type="checkbox" name="remove" value="{finance.pk}">'.encode() in body
        assert b"1 entitlement: Finance" in body

    def test_a_resident_row_is_offered_for_labelling_like_any_other(self, client):
        """`resident=True` is not exempt (decision 35) -- and it must
        first be OFFERED on this page for an operator to restrict it."""
        admin = make_admin()
        shipped = make_agent(slug="shipped-for-access", resident=True)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("chat-agent-entitlements")).content
        assert shipped.slug.encode() in body

    def test_the_page_warns_that_a_label_reaches_the_agents_own_creator(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("chat-agent-entitlements")).content
        assert b"created</em> the agent" in body or b"created the agent" in body
