"""The agent form's context builder, and the two pages that mount it."""
from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils.html import escape

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


class TestTheUserFacingList:
    def test_it_lists_only_this_principals_own_agents(self, client):
        mine, theirs = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="a-mine", name="Mine", **owner_fields(user_principal(mine)))
            make_agent(slug="b-theirs", name="Theirs",
                       **owner_fields(user_principal(theirs)))
            sign_in(client, mine)
            body = client.get(reverse("chat-agents")).content.decode()
        assert "Mine" in body
        assert "Theirs" not in body

    def test_a_box_wide_row_this_member_owns_is_shown_read_only_with_a_reason(
        self, client
    ):
        """Spec §4.3.2. `chat-default-install` is class A, so a member
        can already own a row everybody on the box can use and that the
        editor refuses them. Without this section they would own a row
        that is absent from their list and refused by the editor, with
        nothing explaining why."""
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="everyones", name="Everyone's helper", box_wide=True,
                       **owner_fields(user_principal(member)))
            sign_in(client, member)
            body = client.get(reverse("chat-agents")).content.decode()
        # `escape`, NEVER `|safe`: Django autoescapes the apostrophe in
        # this row's own name, and a template that did not would be the
        # defect. Pinned against the SAME escaping the page applies.
        assert escape("Everyone's helper") in body
        assert "available to everyone here" in body
        assert "New agent" in body

    def test_the_read_only_section_offers_no_edit_link_for_that_row(self, client):
        """`may_manage_agent` refuses it, so a link into the editor
        would be a link to a 404 -- which is the defect the section
        exists to prevent, shipped in a different shape."""
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="everyones-nolink", name="Everyone's helper",
                               box_wide=True, **owner_fields(user_principal(member)))
            sign_in(client, member)
            body = client.get(reverse("chat-agents")).content.decode()
        assert reverse("chat-agent-edit", args=[agent.pk]) not in body

    def test_the_page_renders_a_bare_restriction_count_and_never_a_name(self, client):
        """The LIST pages carry the bare count only, never
        `name_for_viewer`: a per-row held-entitlement read is the sidebar
        N+1 all over again, and a name here is the non-disclosure gate."""
        member, admin = make_user(), make_admin()
        entitlement = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="restricted", name="Restricted",
                               **owner_fields(user_principal(member)))
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
            sign_in(client, member)
            body = client.get(reverse("chat-agents")).content.decode()
        assert "Legal" not in body
        assert "1 restriction" in body

    def test_the_list_costs_the_same_at_one_agent_and_at_twenty_five(self, client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="only-one", **owner_fields(user_principal(member)))
            sign_in(client, member)
            client.get(reverse("chat-agents"))             # warm-up, unmeasured
            with CaptureQueriesContext(connection) as one:
                client.get(reverse("chat-agents"))
            for index in range(25):
                make_agent(slug=f"many-{index}", **owner_fields(user_principal(member)))
            with CaptureQueriesContext(connection) as many:
                client.get(reverse("chat-agents"))
        assert len(many) == len(one)

    def test_the_rail_links_to_it(self, client):
        conversation_free_body = client.get(reverse("chat-index")).content.decode()
        assert reverse("chat-agents") in conversation_free_body


class TestCreatingAnAgent:
    def test_a_member_creates_one_and_it_becomes_theirs(self, client):
        from agents.models import Agent

        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("chat-agent-new"), {
                "name": "My helper", "description": "", "system_prompt": "be helpful",
                "max_steps": "4", "enabled": "on"})
            row = Agent.objects.get(slug="my-helper")
        assert response.status_code == 302
        assert row.owner_kind == "user"
        assert row.box_wide is False

    def test_a_refused_create_re_renders_the_form_with_what_was_typed(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("chat-agent-new"), {
                "name": "", "description": "", "system_prompt": "do not lose me",
                "max_steps": "4", "enabled": "on"})
        body = response.content.decode()
        assert response.status_code == 200
        assert "do not lose me" in body
        assert "Give this agent a name." in body

    def test_the_create_form_never_offers_a_slug_field(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("chat-agent-new")).content.decode()
        assert 'name="slug"' not in body

    def test_a_forged_non_chat_role_on_the_create_route_is_refused(self, client):
        """The vocabulary lives on the FORM (spec §4.4), so the VIEW --
        never `agents.visibility`, which gates `llm_role` on admin alone
        and has no vocabulary of its own -- is what refuses a
        registered-but-not-chat-capable role. The create path carries
        the same check as the edit path for the same reason."""
        from agents.chat.agentform import ROLE_NOT_OFFERED
        from agents.models import Agent
        from models.contracts.roles import RAG_EMBED_ROLE

        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-agent-new"), {
                "name": "Forged create", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on", "llm_role": RAG_EMBED_ROLE})
            created = Agent.objects.filter(name="Forged create").exists()
        assert response.status_code == 200
        assert ROLE_NOT_OFFERED in response.content.decode()
        assert created is False


class TestEditingAnAgent:
    def test_the_owner_edits_their_own(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="editable", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "Renamed", "description": "",
                "system_prompt": "changed", "max_steps": "3", "enabled": "on"})
            agent.refresh_from_db()
        assert response.status_code == 302
        assert agent.name == "Renamed"

    def test_a_stranger_gets_a_404_on_both_verbs_and_writes_nothing(self, client):
        owner, stranger = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="not-yours", name="Original",
                               **owner_fields(user_principal(owner)))
            sign_in(client, stranger)
            get = client.get(reverse("chat-agent-edit", args=[agent.pk]))
            post = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "Hijacked", "description": "",
                "system_prompt": "", "max_steps": "2", "enabled": "on"})
            agent.refresh_from_db()
        assert get.status_code == 404
        assert post.status_code == 404
        assert agent.name == "Original"

    def test_a_box_wide_row_refuses_its_own_non_admin_owner_with_a_404(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="everyones-edit", box_wide=True,
                               **owner_fields(user_principal(member)))
            sign_in(client, member)
            assert client.get(
                reverse("chat-agent-edit", args=[agent.pk])).status_code == 404

    def test_the_reach_control_is_absent_from_a_members_page_source(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="no-reach", **owner_fields(user_principal(member)))
            sign_in(client, member)
            body = client.get(reverse("chat-agent-edit", args=[agent.pk])).content.decode()
        assert "Everyone on this box" not in body
        assert 'name="box_wide"' not in body

    def test_a_forged_reach_post_from_a_member_is_refused(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="forged-reach", **owner_fields(user_principal(member)))
            sign_in(client, member)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "n", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on", "box_wide": "on"})
            agent.refresh_from_db()
        assert agent.box_wide is False

    def test_a_non_admin_sees_the_role_as_text_and_no_select(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="no-role-select",
                               **owner_fields(user_principal(member)))
            sign_in(client, member)
            body = client.get(reverse("chat-agent-edit", args=[agent.pk])).content.decode()
        assert 'name="llm_role"' not in body

    def test_a_forged_non_chat_role_from_an_admin_is_refused(self, client):
        """WITHOUT the view's own vocabulary check an administrator
        could stamp an agent with an embeddings role no chat turn can
        resolve: `agents.visibility::_validated_agent_fields` writes
        whatever string an admin sends."""
        from agents.chat.agentform import ROLE_NOT_OFFERED
        from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_EMBED_ROLE

        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="forged-role", llm_role=CHAT_CONVERSE_ROLE,
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "n", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on", "llm_role": RAG_EMBED_ROLE})
            agent.refresh_from_db()
        assert response.status_code == 200
        assert ROLE_NOT_OFFERED in response.content.decode()
        assert agent.llm_role == CHAT_CONVERSE_ROLE

    def test_a_chat_capable_role_from_an_admin_is_written(self, client):
        """The other half of the pair, so the refusal above is a
        VOCABULARY check rather than a select that refuses everything."""
        from models.contracts.roles import CHAT_CONVERSE_ROLE

        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="honest-role", llm_role="",
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "n", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on", "llm_role": CHAT_CONVERSE_ROLE})
            agent.refresh_from_db()
        assert response.status_code == 302
        assert agent.llm_role == CHAT_CONVERSE_ROLE

    def test_a_resident_row_warns_about_reset(self, client):
        from agents.chat.agentform import RESIDENT_WARNING

        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="shipped-row", resident=True,
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            body = client.get(reverse("chat-agent-edit", args=[agent.pk])).content.decode()
        assert "installed from the shipped catalogue" in body
        # THE WHOLE SENTENCE, pinned against the builder's own constant:
        # `TestTheFormContext` above never RENDERS it, so nothing else on
        # this branch proves the warning reaches a page at all.
        assert RESIDENT_WARNING in body

    def test_a_GET_next_is_echoed_into_a_hidden_field_and_redirected_nowhere(
        self, client
    ):
        """`validated_next_url` reads `request.POST` and nothing else, so
        a `?next=` arriving on a GET is NOT validated by it and a naive
        `request.GET["next"]` redirect would be an open redirect off-box
        (spec review m3)."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="next-echo", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            url = reverse("chat-agent-edit", args=[agent.pk])
            response = client.get(f"{url}?next=https://elsewhere.example/steal")
        body = response.content.decode()
        assert response.status_code == 200
        assert 'name="next"' in body

    def test_an_off_origin_next_on_the_POST_falls_back_to_the_list(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="next-refused", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "n", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on",
                "next": "https://elsewhere.example/steal"})
        assert response.status_code == 302
        assert response["Location"] == reverse("chat-agents")

    def test_a_same_origin_next_on_the_POST_is_honoured(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="next-ok", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "n", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on", "next": reverse("chat-index")})
        assert response["Location"] == reverse("chat-index")

    def test_a_malformed_llm_role_still_renders_200(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="odd-role", llm_role="not.a.registered.role",
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            assert client.get(
                reverse("chat-agent-edit", args=[agent.pk])).status_code == 200

    def test_max_steps_is_refused_at_the_declared_bounds(self, client):
        from agents.limits import MAX_STEPS_CEILING

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="steps", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            for value in ("0", str(MAX_STEPS_CEILING + 1)):
                response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                    "action": "fields", "name": "n", "description": "",
                    "system_prompt": "", "max_steps": value, "enabled": "on"})
                assert response.status_code == 200
                assert str(MAX_STEPS_CEILING) in response.content.decode()


class TestNoEntitlementNameLeaksFromTheseRoutes:
    """Asserted DIRECTLY, in this column's own tests, rather than left to
    `identity/tests/test_route_matrix.py`'s cross-cutting sweep to catch
    later (spec review R3)."""

    def test_none_of_the_three_routes_names_an_entitlement_to_a_non_holder(self, client):
        member, admin = make_user(), make_admin()
        entitlement = make_entitlement(name="Radioactive")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="swept", **owner_fields(user_principal(member)))
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
            sign_in(client, member)
            bodies = [
                client.get(reverse("chat-agents")).content.decode(),
                client.get(reverse("chat-agent-new")).content.decode(),
                client.get(reverse("chat-agent-edit", args=[agent.pk])).content.decode(),
            ]
        assert not any("Radioactive" in body for body in bodies)

    def test_the_count_this_page_DOES_carry_is_the_owners_own_standing(self, client):
        """The sweep above is a GATE, not a page that says nothing to
        anybody: an OWNER of the entitlement (`grant(..., role="owner")`
        -- the standing `labelling_entitlements` admits a non-admin on)
        may label with it, so the row carries NO restriction they cannot
        change and the count disappears. The name itself still belongs
        to Task 9's panel, which this page does not render yet."""
        member, admin = make_user(), make_admin()
        entitlement = make_entitlement(name="Radioactive")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="swept-owned",
                               **owner_fields(user_principal(member)))
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
            grant(entitlement, user=member, role="owner")
            sign_in(client, member)
            body = client.get(reverse("chat-agents")).content.decode()
        assert "Radioactive" not in body
        assert "1 restriction" not in body
