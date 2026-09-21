"""The agent form's context builder, and the two pages that mount it."""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401
    grant, make_admin, make_agent, make_entitlement, make_user, posture, sign_in,
    user_principal,
)
from agents.labels import agent_label_ids, set_agent_labels
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


class TestTheFormContext:
    def test_a_new_agent_starts_from_the_declared_defaults(self):
        from agents.chat.agentform import agent_form_context
        from agents.limits import MAX_STEPS_CEILING, MAX_STEPS_DEFAULT

        context = agent_form_context(user_principal(make_user()))
        assert context["agent"] is None
        assert context["values"]["max_steps"] == MAX_STEPS_DEFAULT
        assert context["values"]["enabled"] is True
        assert context["max_steps_ceiling"] == MAX_STEPS_CEILING

    def test_an_existing_agent_seeds_every_editable_field(self):
        from agents.chat.agentform import agent_form_context

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="seeded", name="Seeded", system_prompt="p",
                               description="d", max_steps=5,
                               **owner_fields(user_principal(owner)))
            values = agent_form_context(user_principal(owner), agent=agent)["values"]
        assert values == {"name": "Seeded", "description": "d", "system_prompt": "p",
                          "max_steps": 5, "enabled": True}

    def test_a_posted_body_survives_a_refusal_so_nothing_is_retyped(self):
        from agents.chat.agentform import agent_form_context

        context = agent_form_context(
            user_principal(make_user()),
            posted={"name": "", "system_prompt": "kept", "description": "",
                    "max_steps": "4", "enabled": "on"},
            errors={"name": "Give this agent a name."})
        assert context["values"]["system_prompt"] == "kept"
        assert context["errors"]["name"]

    def test_the_role_select_is_built_for_an_administrator_only(self):
        """RENDER-VS-GATE: a non-admin's context never BUILDS the
        options -- which model backs an agent is box policy, the same
        call the Chat and Job execution settings pages already record."""
        from agents.chat.agentform import agent_form_context

        member, admin = make_user(), make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="roles", **owner_fields(user_principal(member)))
            member_context = agent_form_context(user_principal(member), agent=agent)
            admin_context = agent_form_context(user_principal(admin), agent=agent)
        assert member_context["may_choose_role"] is False
        assert member_context["role_options"] == ()
        assert member_context["current_role_label"]
        assert admin_context["may_choose_role"] is True
        assert admin_context["role_options"]

    def test_the_role_options_are_chat_capable_roles_only(self):
        from agents.chat.agentform import agent_form_context
        from models.contracts.roles import all_roles

        with posture(POSTURE_ENTERPRISE):
            context = agent_form_context(user_principal(make_admin()))
        offered = {key for key, _label in context["role_options"]}
        assert offered == {r.key for r in all_roles() if r.capability == "chat"}

    def test_the_reach_control_is_built_for_an_administrator_only(self):
        from agents.chat.agentform import agent_form_context

        member, admin = make_user(), make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="reach", **owner_fields(user_principal(member)))
            assert agent_form_context(
                user_principal(member), agent=agent)["may_set_reach"] is False
            assert agent_form_context(
                user_principal(admin), agent=agent)["may_set_reach"] is True

    def test_on_an_open_box_the_entitlement_panel_does_not_render_at_all(self):
        """`labelling_entitlements` returns `()` with accounts off --
        there is nothing to label with and nothing to show."""
        from agents.chat.agentform import agent_form_context
        from identity.contracts.principals import OPEN_PRINCIPAL

        agent = make_agent(slug="open-panel")
        assert agent_form_context(OPEN_PRINCIPAL, agent=agent)["entitlement_panel"] is None

    def test_a_new_agent_has_no_entitlement_panel_either(self):
        """There is no row to label yet; the panel appears on the edit
        page, once the agent exists."""
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            context = agent_form_context(user_principal(make_admin()))
        assert context["entitlement_panel"] is None

    def test_the_panel_is_the_shared_two_pane_shape_the_access_pages_render(self):
        from agents.chat.agentform import agent_form_context

        admin = make_admin()
        held = make_entitlement(name="Legal")
        other = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="panelled", **owner_fields(user_principal(admin)))
            set_agent_labels(user_principal(admin), agent, {held.pk})
            panel = agent_form_context(user_principal(admin),
                                       agent=agent)["entitlement_panel"]
        assert [row["id"] for row in panel["active"]] == [held.pk]
        assert [row["id"] for row in panel["available"]] == [other.pk]


class TestTheNonDisclosureGate:
    """Spec review R3. `entitlement_panes` builds BOTH panes from
    `choices`, so a member editing their own agent that an administrator
    labelled sees a panel with no trace of that label. Naming it would
    turn `identity/tests/test_route_matrix.py::
    test_no_route_other_than_the_dormant_share_page_names_an_entitlement
    _to_a_non_holder` red, correctly. So the form says HOW MANY, never
    WHICH."""

    def _member_owned_agent_labelled_by_an_admin(self, *, entitlement_name="Legal"):
        member, admin = make_user(), make_admin()
        entitlement = make_entitlement(name=entitlement_name)
        agent = make_agent(slug="labelled-by-admin",
                           **owner_fields(user_principal(member)))
        set_agent_labels(user_principal(admin), agent, {entitlement.pk})
        return member, admin, agent, entitlement

    def test_a_member_is_told_how_many_restrictions_they_cannot_change(self):
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            member, _admin, agent, entitlement = (
                self._member_owned_agent_labelled_by_an_admin())
            sentence = agent_form_context(user_principal(member),
                                          agent=agent)["foreign_label_sentence"]
        assert "1 restriction" in sentence
        assert entitlement.name not in sentence

    def test_a_member_who_HOLDS_one_of_them_sees_it_named(self):
        """No new information: they hold it, they know its name, and the
        gate's own subject is an entitlement you NEITHER own NOR hold."""
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            member, _admin, agent, entitlement = (
                self._member_owned_agent_labelled_by_an_admin())
            grant(entitlement, user=member)
            sentence = agent_form_context(user_principal(member),
                                          agent=agent)["foreign_label_sentence"]
        assert entitlement.name in sentence

    def test_an_administrator_sees_no_such_sentence_at_all(self):
        """`labelling_entitlements` offers them everything, so
        `missing_ids` is empty for them."""
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            _member, admin, agent, _entitlement = (
                self._member_owned_agent_labelled_by_an_admin())
            assert agent_form_context(user_principal(admin),
                                      agent=agent)["foreign_label_sentence"] == ""

    def test_disclose_all_is_never_used_on_this_surface(self):
        """Its one caller is the dormant-share 403, where the reader
        holds a live `Share` row and owner decision 8 asks for the names
        in so many words. Neither condition holds on an agent form."""
        import inspect

        from agents.chat import agentform

        assert "disclose_all" not in inspect.getsource(agentform)
