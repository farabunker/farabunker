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
        # THE POSITIVE CO-ASSERTION (review M3): a `not in` alone would
        # stay green on a page that stopped rendering the section at all.
        assert escape("Everyone's helper") in body
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

    def test_the_admin_path_costs_the_same_at_one_box_wide_row_and_at_twenty_five(
        self, client
    ):
        """Task 8 re-check, nit n2. The pin above runs as a MEMBER, whose
        box-wide section is empty, so the per-row `may_manage_agent` the
        fix round added is only ever cost-pinned on rows where it is True
        by construction. An ADMINISTRATOR is the reader whose box-wide
        section really has rows in it, and `may_manage_agent` takes the
        threaded `settings_row` into `is_admin` and short-circuits there
        -- `identity.access.may_read_owned_row`, the one unthreaded read
        underneath it, is on the `owner_kind == "service"` branch and
        unreachable from this page. So the cost IS flat, and an EQUALITY
        pin is the honest shape: a `<=` would pass while the predicate
        quietly grew a query per row.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="wide-only-one", name="Wide one", box_wide=True,
                       **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            client.get(reverse("chat-agents"))             # warm-up, unmeasured
            with CaptureQueriesContext(connection) as one:
                one_body = client.get(reverse("chat-agents")).content.decode()
            for index in range(25):
                make_agent(slug=f"wide-many-{index}", name=f"Wide many {index}",
                           box_wide=True, **owner_fields(user_principal(admin)))
            with CaptureQueriesContext(connection) as many:
                many_body = client.get(reverse("chat-agents")).content.decode()
        # THE POSITIVE HALF: both renders really carried the box-wide
        # section, so the equality is not two identical counts for two
        # pages that listed nothing. The NAME is what the list prints --
        # `make_agent` gives every row the same default name, so these
        # two rows are named apart deliberately.
        assert "Wide one" in one_body
        assert "Wide many 24" in many_body
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
        """BOTH VERBS, matching the stranger test's own standard (review
        M5): a GET-only pin would leave the write path unasserted for the
        one refusal `may_manage_agent` makes that is not about ownership."""
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="everyones-edit", name="Original",
                               box_wide=True,
                               **owner_fields(user_principal(member)))
            sign_in(client, member)
            get = client.get(reverse("chat-agent-edit", args=[agent.pk]))
            post = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "Hijacked", "description": "",
                "system_prompt": "", "max_steps": "2", "enabled": "on"})
            agent.refresh_from_db()
        assert get.status_code == 404
        assert post.status_code == 404
        assert agent.name == "Original"

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
            responses = [
                client.get(reverse("chat-agents")),
                client.get(reverse("chat-agent-new")),
                client.get(reverse("chat-agent-edit", args=[agent.pk])),
            ]
            bodies = [response.content.decode() for response in responses]
            headings = ["<h1>Agents</h1>", "<h1>New agent</h1>",
                        f"<h1>{escape(agent.name)}</h1>"]
        # THE POSITIVE CO-ASSERTIONS (review M3): three `not in`s over
        # three bodies would all pass on three empty pages, or on three
        # 404s. Each response has to really be the page it names.
        assert [response.status_code for response in responses] == [200, 200, 200]
        for heading, body in zip(headings, bodies):
            assert heading in body, heading
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


class TestTheBoxWideSectionSpeaksToItsReader:
    """Fix round, review I2. `box_wide_agents_owned_by` short-circuits on
    `sees_all_content`, which answers True for EVERYBODY on an open box --
    the default posture -- so the read-only section's one declared sentence
    ("An administrator can change it") was being printed to the
    administrator it points at, with the edit link withheld from the one
    reader `may_manage_agent` admits. Which sentence a row gets is now that
    predicate's answer for that row.

    The member half of the pair lives in `TestTheUserFacingList` above
    (`test_a_box_wide_row_this_member_owns_is_shown_read_only_with_a_reason`
    and `test_the_read_only_section_offers_no_edit_link_for_that_row`),
    unchanged -- the read-only sentence is TRUE for them and the link would
    be a link to a 404.
    """

    def test_an_administrator_on_an_accounts_on_box_gets_the_link_and_the_admin_sentence(
        self, client
    ):
        """OWNED BY THE ADMINISTRATOR deliberately: with
        `admin_sees_content` off (the default), `sees_all_content` is False
        even for an admin, so `box_wide_agents_owned_by` falls through to
        `owned_rows_q` and lists their own rows only -- which is
        `editable_agents`' own documented behaviour on an accounts-on box,
        not a quirk of this test."""
        from agents.chat.views.agents import (
            BOX_WIDE_SECTION_ADMIN_NOTE, BOX_WIDE_SECTION_NOTE,
        )

        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="everyones-admin", name="Everyone's helper",
                               box_wide=True,
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            body = client.get(reverse("chat-agents")).content.decode()
        assert escape("Everyone's helper") in body
        assert reverse("chat-agent-edit", args=[agent.pk]) in body
        assert BOX_WIDE_SECTION_ADMIN_NOTE in body
        assert BOX_WIDE_SECTION_NOTE not in body

    def test_on_an_open_box_the_default_reader_gets_the_link_not_the_read_only_sentence(
        self, client
    ):
        """NO `posture(...)` HERE, and that is the point: the shipped
        default is the open box, where `accounts_on` is False, nobody signs
        in, and `is_admin` answers True for whoever is at the keyboard. That
        made the open box the one configuration where the false sentence was
        what EVERY reader saw -- and it was the configuration no test
        rendered this page in."""
        from agents.chat.views.agents import (
            BOX_WIDE_SECTION_ADMIN_NOTE, BOX_WIDE_SECTION_NOTE,
        )

        agent = make_agent(slug="everyones-open", name="Everyone's helper",
                           box_wide=True)
        body = client.get(reverse("chat-agents")).content.decode()
        assert escape("Everyone's helper") in body
        assert reverse("chat-agent-edit", args=[agent.pk]) in body
        assert BOX_WIDE_SECTION_ADMIN_NOTE in body
        assert BOX_WIDE_SECTION_NOTE not in body

    def test_the_admin_link_really_opens_the_editor(self, client):
        """The other half: a link this section renders must not be a link
        to a 404, which is the defect the whole section exists to prevent.
        `may_manage_agent` decided to render it, so the same predicate must
        admit the GET."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="everyones-reachable", box_wide=True,
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            response = client.get(reverse("chat-agent-edit", args=[agent.pk]))
        assert response.status_code == 200


class TestTheEditRoutesActionBranch:
    """Fix round, review M2. Any POST used to be handled as a field save,
    `action=labels` and `action=nonsense` included. Task 9's panel is the
    reason that matters: the day it starts posting a labels-shaped body,
    a branchless handler would run it through `update_agent` and blank the
    row's prompt."""

    def test_an_unknown_action_is_refused_and_writes_nothing(self, client):
        from agents.chat.views.agents import AGENT_UNKNOWN_ACTION

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="odd-action", name="Original",
                               system_prompt="keep me",
                               **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "nonsense", "name": "Renamed", "description": "",
                "system_prompt": "", "max_steps": "2", "enabled": "on"})
            agent.refresh_from_db()
        assert response.status_code == 400
        assert AGENT_UNKNOWN_ACTION in response.content.decode()
        assert agent.name == "Original"
        assert agent.system_prompt == "keep me"

    def test_a_missing_action_is_refused_too(self, client):
        """An ABSENT `action` is the same shape as a wrong one -- the
        fragment always ships the hidden field, so a body without it never
        came from this page."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="no-action", name="Original",
                               **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "name": "Renamed", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on"})
            agent.refresh_from_db()
        assert response.status_code == 400
        assert agent.name == "Original"

    def test_the_fields_action_still_saves(self, client):
        """The honest other half, so the branch above is a VOCABULARY
        check rather than a route that refuses every POST."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="fields-action",
                               **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "Renamed", "description": "",
                "system_prompt": "", "max_steps": "2", "enabled": "on"})
            agent.refresh_from_db()
        assert response.status_code == 302
        assert agent.name == "Renamed"


def _page_content(body: str) -> str:
    """JUST THIS PAGE'S OWN MARKUP, not the shell and rail around it.

    Sliced for the reason `agents/chat/tests/test_sidebar.py::_nav` slices
    the rail: an assertion about what a page does NOT contain must not be
    answerable by the rest of the document. `chat/base.html` puts
    `{% templatetag openblock %} block chat_content {% templatetag
    closeblock %}` inside `<div class="chat-wrap">`, AFTER the sidebar
    block -- and the sidebar brings `chat/_menu_exclusive.html`, the one
    sanctioned script on this surface -- so an unsliced "no `<script>`"
    assertion could never pass on any page that renders the rail, which
    is all three of these.
    """
    marker = '<div class="chat-wrap">'
    assert marker in body, "chat/base.html no longer wraps the content block"
    return body.split(marker, 1)[1]


class TestTheNewPagesAddNoScript:
    """Fix round, review M4. The three pages are one plain POST form and
    two lists of links -- the doctrine -- and nothing held that. Mirrors
    `test_sidebar.py::test_every_action_is_a_details_and_a_post_form_never_
    a_script`, sliced for the same reason (see `_page_content`)."""

    def _assert_scriptless(self, body):
        content = _page_content(body)
        assert "<script" not in content
        assert "onclick" not in content
        assert "onsubmit" not in content

    def test_the_list_page_carries_no_script_of_its_own(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="scriptless-list",
                       **owner_fields(user_principal(member)))
            make_agent(slug="scriptless-boxwide", box_wide=True,
                       **owner_fields(user_principal(member)))
            sign_in(client, member)
            response = client.get(reverse("chat-agents"))
        assert response.status_code == 200
        # THE POSITIVE HALF: the slice really contains this page, so a
        # "no script" claim is not a claim about an empty string.
        assert "<h1>Agents</h1>" in _page_content(response.content.decode())
        self._assert_scriptless(response.content.decode())

    def test_the_create_page_carries_no_script_of_its_own(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.get(reverse("chat-agent-new"))
        assert response.status_code == 200
        assert "<h1>New agent</h1>" in _page_content(response.content.decode())
        self._assert_scriptless(response.content.decode())

    def test_the_edit_page_carries_no_script_of_its_own(self, client):
        """Rendered as an ADMINISTRATOR, so the role select and the reach
        fieldset -- the two controls a member's page never builds -- are
        both in the slice being checked."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="scriptless-edit",
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            response = client.get(reverse("chat-agent-edit", args=[agent.pk]))
        content = _page_content(response.content.decode())
        assert response.status_code == 200
        assert 'name="llm_role"' in content
        assert 'name="box_wide"' in content
        self._assert_scriptless(response.content.decode())


class TestTheAudienceWriteInBothDirections:
    """Spec review M1's headline. A label the actor may not label with is
    never in `choices`, therefore never in either pane, therefore never
    in `submitted` -- so it survives BOTH directions untouched, by
    construction rather than by a check. A test for the ADD direction
    alone would never have caught the hole this closes."""

    def _member_owned_agent_an_admin_labelled(self):
        member, admin = make_user(), make_admin()
        legal = make_entitlement(name="Legal")
        agent = make_agent(slug="two-controls", **owner_fields(user_principal(member)))
        set_agent_labels(user_principal(admin), agent, {legal.pk})
        return member, agent, legal

    def test_the_panels_own_hidden_action_is_the_one_this_view_branches_on(self):
        """THE TWO SPELLINGS, PINNED TOGETHER. The panel's hidden field is
        built in `agents.chat.agentform` and the branch that reads it is
        `LABELS_ACTION` in `agents.chat.views.agents` -- two literals,
        because a template cannot read a view constant and the form module
        is what the view imports, so it cannot import back. If they ever
        drift, every label POST silently becomes an unknown action and
        gets a 400 instead of writing."""
        from agents.chat.agentform import agent_form_context
        from agents.chat.views.agents import LABELS_ACTION

        make_entitlement(name="Anything")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="action-spelling",
                               **owner_fields(user_principal(make_admin())))
            panel = agent_form_context(user_principal(make_admin()),
                                       agent=agent)["entitlement_panel"]
        assert panel is not None
        assert panel["fields"]["action"] == LABELS_ACTION

    def test_the_panel_offers_the_administrators_label_in_NEITHER_pane(self, client):
        """DELIBERATELY STRONGER THAN THE BRIEF'S DRAFT, which asked this
        of a member who owns NO entitlement -- for whom
        `labelling_entitlements` answers `()`, the panel is `None`, and
        "in neither pane" is true of a panel that does not exist. The
        member here OWNS one, so the panel really renders, and the
        assertion is a PAIR: their own entitlement is offered and the
        administrator's is in neither pane of the same panel."""
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            member, agent, legal = self._member_owned_agent_an_admin_labelled()
            mine = make_entitlement(name="Mine")
            grant(mine, user=member, role="owner")
            panel = agent_form_context(user_principal(member),
                                       agent=agent)["entitlement_panel"]
        assert panel is not None, "the panel must really render, or this pin is vacuous"
        offered = {row["id"] for row in panel["available"]}
        offered |= {row["id"] for row in panel["active"]}
        assert mine.pk in offered
        assert legal.pk not in offered

    def test_a_forged_remove_is_refused_and_the_row_survives(self, client):
        with posture(POSTURE_ENTERPRISE):
            member, agent, legal = self._member_owned_agent_an_admin_labelled()
            sign_in(client, member)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "remove", "remove": str(legal.pk)})
        assert response.status_code == 302
        assert agent_label_ids(agent) == frozenset({legal.pk})

    def test_a_forged_add_is_refused_identically(self, client):
        with posture(POSTURE_ENTERPRISE):
            member, agent, _legal = self._member_owned_agent_an_admin_labelled()
            unowned = make_entitlement(name="Finance")
            sign_in(client, member)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(unowned.pk)})
        assert unowned.pk not in agent_label_ids(agent)

    def test_the_member_adds_and_removes_their_OWN_and_the_admins_is_untouched(
        self, client
    ):
        with posture(POSTURE_ENTERPRISE):
            member, agent, legal = self._member_owned_agent_an_admin_labelled()
            # OWNING AN ENTITLEMENT IS AN `EntitlementGrant` WITH
            # `role="owner"`, not a column on the row.
            # `identity/models.py::Entitlement` carries `name`,
            # `description`, `created_by`, `created_at` and nothing
            # else, and `identity.access.labelling_entitlements` filters
            # on OWNED ids -- merely holding one puts it in neither
            # pane. Getting this wrong does not fail loudly: it would
            # quietly assert the member's own add/remove leg against an
            # entitlement they cannot label with, which is the other
            # half of this same test.
            mine = make_entitlement(name="Mine")
            grant(mine, user=member, role="owner")
            sign_in(client, member)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(mine.pk)})
            assert agent_label_ids(agent) == frozenset({legal.pk, mine.pk})
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "remove", "remove": str(mine.pk)})
            assert agent_label_ids(agent) == frozenset({legal.pk})

    def test_an_unrecognised_operation_is_refused_and_writes_nothing(self, client):
        """`parse_entitlement_diff` owns this refusal, and it returns an
        `HttpResponse` the view hands straight back rather than a pair --
        so this is also the pin that `_save_labels` really returns that
        response instead of falling through to the writer with an
        unpacked tuple."""
        with posture(POSTURE_ENTERPRISE):
            member, agent, legal = self._member_owned_agent_an_admin_labelled()
            sign_in(client, member)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "sideways", "add": str(legal.pk)})
        assert response.status_code == 302
        assert agent_label_ids(agent) == frozenset({legal.pk})

    def test_a_typed_entitlement_id_is_refused_rather_than_crashing(self, client):
        """The other refusal in the same parser: an id that is not a
        decimal at all. It must be a redirect, never a 500 -- the
        `test_never_500` sweep never posts this body."""
        with posture(POSTURE_ENTERPRISE):
            member, agent, legal = self._member_owned_agent_an_admin_labelled()
            sign_in(client, member)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": "Legal"})
        assert response.status_code == 302
        assert agent_label_ids(agent) == frozenset({legal.pk})

    def test_a_stale_form_is_harmless_in_both_directions(self, client):
        """`op="remove"` naming a label already gone is a no-op with no
        audit row, and `op="add"` naming one already present likewise --
        the DIFF is the point (`agents/labels.py::_set_labels`).

        BOTH LEGS ARE REALLY DRIVEN HERE (the brief's draft named both
        directions and posted only the add): `present` is already on the
        row and `absent` is not, and the administrator may label with
        either, so each POST is accepted by the gate and then diffs away
        to nothing."""
        from identity.contracts import actions
        from identity.models import AuditEvent

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            present = make_entitlement(name="Present")
            absent = make_entitlement(name="Absent")
            agent = make_agent(slug="stale", **owner_fields(user_principal(admin)))
            set_agent_labels(user_principal(admin), agent, {present.pk})
            before = AuditEvent.objects.filter(
                action__in=[actions.AGENT_LABELLED, actions.AGENT_UNLABELLED]).count()
            sign_in(client, admin)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(present.pk)})
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "remove", "remove": str(absent.pk)})
            after = AuditEvent.objects.filter(
                action__in=[actions.AGENT_LABELLED, actions.AGENT_UNLABELLED]).count()
        assert after == before
        assert agent_label_ids(agent) == frozenset({present.pk})

    def test_two_edits_to_different_labels_do_not_clobber_each_other(self, client):
        """The add/remove shape's own reason for existing: a whole
        submitted set clobbers."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            first, second = make_entitlement(name="A"), make_entitlement(name="B")
            agent = make_agent(slug="concurrent", **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(first.pk)})
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(second.pk)})
        assert agent_label_ids(agent) == frozenset({first.pk, second.pk})

    def test_a_label_save_lands_back_on_the_row_it_was_editing(self, client):
        """NOT the list, and NOT `entitlement_row_url` -- which builds
        `reverse(route_name)` with no arguments and cannot name a
        row-addressed route at all. A label edit is one of a run of them,
        so it returns to the page the panel is on."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            entitlement = make_entitlement(name="Back")
            agent = make_agent(slug="lands-back",
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(entitlement.pk)})
        assert response.status_code == 302
        assert response["Location"] == reverse("chat-agent-edit", args=[agent.pk])

    def test_the_writer_stamps_who_labelled_it(self, client):
        """`user_for_request`, not the `Principal` value object:
        `AgentEntitlement.labelled_by` is a real `User` foreign key, and
        a save that left it null would lose the only record of who
        narrowed the agent."""
        from agents.models import AgentEntitlement

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            entitlement = make_entitlement(name="Stamped")
            agent = make_agent(slug="stamped", **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(entitlement.pk)})
            row = AgentEntitlement.objects.get(agent=agent, entitlement=entitlement)
        assert row.labelled_by == admin

    def test_the_label_path_is_unreachable_on_a_box_wide_row_for_a_non_admin(
        self, client
    ):
        """Both POST paths are on `chat-agent-edit`, class O, 404 unless
        `may_manage_agent` -- which short-circuits False on `box_wide`
        for a non-admin. The panel is not merely absent; the route is."""
        member = make_user()
        entitlement = make_entitlement(name="Anything")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="box-wide-labels", box_wide=True,
                               **owner_fields(user_principal(member)))
            sign_in(client, member)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(entitlement.pk)})
        assert response.status_code == 404
        assert agent_label_ids(agent) == frozenset()

    def test_an_open_box_has_nothing_to_label_with_so_every_label_post_is_refused(
        self, client
    ):
        """NO `posture(...)`: on the shipped open box `accounts_on` is
        False, `labelling_entitlements` answers `()` whoever asks, and the
        panel never renders -- so the gate's `submitted <= offered` check
        refuses every id on a surface where `is_admin` answers True for
        whoever is at the keyboard."""
        entitlement = make_entitlement(name="Open")
        agent = make_agent(slug="open-labels")
        response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
            "action": "labels", "op": "add", "add": str(entitlement.pk)})
        assert response.status_code == 302
        assert agent_label_ids(agent) == frozenset()


class TestThePanelOnThePage:
    """The rendered half: Task 8 built the context key and left the
    template not reading it. These pin what the page really ships."""

    def _admin_page(self, client, *, held=None):
        admin = make_admin()
        entitlement = make_entitlement(name="Legal")
        other = make_entitlement(name="Finance")
        agent = make_agent(slug="panelled-page", name="Panelled",
                           **owner_fields(user_principal(admin)))
        if held:
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
        sign_in(client, admin)
        response = client.get(reverse("chat-agent-edit", args=[agent.pk]))
        return response, agent, entitlement, other

    def test_the_panel_is_a_SIBLING_of_the_field_form_never_nested_inside_it(
        self, client
    ):
        """`_transfer_panel.html` renders its own `<form>`, and nested
        `<form>` elements are illegal HTML -- the inner one simply does
        not submit, so a nested panel would render, look right, and never
        write. The FIRST `</form>` on the page closes the field form, so
        the panel appearing after it is the structural proof."""
        with posture(POSTURE_ENTERPRISE):
            response, _agent, _held, _other = self._admin_page(client)
        content = _page_content(response.content.decode())
        assert response.status_code == 200
        assert 'class="transfer-panel' in content
        assert content.index("</form>") < content.index('class="transfer-panel')
        # AND THE FIELD FORM IS STILL THERE, so this is not a page that
        # dropped one control while gaining the other.
        assert 'name="system_prompt"' in content

    def test_both_panes_render_with_the_rows_the_builder_split(self, client):
        """The include's parameter names are the fragment's own
        (`tp_available`/`tp_active`); passing `available`/`active`
        instead renders two EMPTY panes with no error at all, which is
        why the pane contents are pinned rather than the panel's
        presence."""
        with posture(POSTURE_ENTERPRISE):
            response, _agent, held, other = self._admin_page(client, held=True)
        content = _page_content(response.content.decode())
        assert f'name="remove" value="{held.pk}"' in content
        assert f'name="add" value="{other.pk}"' in content
        assert 'name="op" value="add"' in content
        assert 'name="op" value="remove"' in content
        assert 'name="action" value="labels"' in content

    def test_the_panes_carry_their_own_counts(self, client):
        """`tp_available_count`/`tp_active_count` come from the builder,
        so a heading cannot disagree with the list under it."""
        with posture(POSTURE_ENTERPRISE):
            response, _agent, _held, _other = self._admin_page(client, held=True)
        content = _page_content(response.content.decode())
        assert "Available — 1" in content
        assert "Active — 1" in content

    def test_the_only_script_on_the_page_is_the_shared_filter_enhancement(
        self, client
    ):
        """THE ZERO-JS DOCTRINE, stated for the page as it is now rather
        than assumed from when it had no panel.
        `TestTheNewPagesAddNoScript::test_the_edit_page_carries_no_script_
        of_its_own` still holds unchanged, because it renders a box with
        NO entitlements at all and therefore no panel -- this is the
        other configuration, and the exception it allows is exactly one
        include, gated on the panel, adding no row and no `name=` a
        JS-off submission relies on."""
        with posture(POSTURE_ENTERPRISE):
            response, _agent, _held, _other = self._admin_page(client, held=True)
        content = _page_content(response.content.decode())
        assert content.count("<script") == 1
        assert "[data-filter-rows][data-filter-scope]" in content
        assert "onclick" not in content
        assert "onsubmit" not in content

    def test_a_reader_with_nothing_to_label_with_gets_no_panel_and_no_script(
        self, client
    ):
        """The gating half: the include is inside the panel's own `{% if
        %}`, so the page a member sees is byte-for-byte as scriptless as
        it was before this task."""
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="no-panel",
                               **owner_fields(user_principal(member)))
            sign_in(client, member)
            response = client.get(reverse("chat-agent-edit", args=[agent.pk]))
        content = _page_content(response.content.decode())
        assert response.status_code == 200
        assert "transfer-panel" not in content
        assert "<script" not in content

    def test_the_foreign_label_sentence_reaches_the_reader_who_has_no_panel(
        self, client
    ):
        """DEVIATION FROM THE BRIEF'S DRAFT, pinned here because it is a
        behaviour and not a preference. The draft nested this sentence
        under the panel's `<details>`; `agent_form_context` computes it
        whenever there is an AGENT, deliberately and with its own comment
        saying why -- a member who owns NO entitlement has no panel and
        is exactly the reader who needs telling that their row carries a
        restriction they cannot change here. Nested, that reader would be
        told nothing, and the existing context-level test would not have
        noticed."""
        member, admin = make_user(), make_admin()
        entitlement = make_entitlement(name="Radioactive")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="sentence-no-panel",
                               **owner_fields(user_principal(member)))
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
            sign_in(client, member)
            content = _page_content(
                client.get(reverse("chat-agent-edit", args=[agent.pk]))
                .content.decode())
        assert "transfer-panel" not in content
        assert "1 restriction" in content
        # HOW MANY, NEVER WHICH -- the non-disclosure gate, on the page
        # rather than only on the context.
        assert "Radioactive" not in content

    def test_the_create_page_still_renders_no_panel_at_all(self, client):
        """There is no row to label yet, so the create route is
        untouched by this task."""
        with posture(POSTURE_ENTERPRISE):
            make_entitlement(name="Legal")
            sign_in(client, make_admin())
            content = _page_content(
                client.get(reverse("chat-agent-new")).content.decode())
        assert "<h1>New agent</h1>" in content
        assert "transfer-panel" not in content
        assert "<script" not in content


class TestTheTruthTable:
    """`visible_agents` is `(owned | box_wide | shared) AND
    label_permitted_q`, which is a truth table, not an exclusive choice.
    An administrator with `admin_sees_content` ON sees every enabled row
    whatever this table says, because the `sees_all_content`
    short-circuit returns before the label clause is reached."""

    def test_all_four_rows(self):
        from agents.visibility import visible_agents

        holder, stranger, admin = (make_user(), make_user(username="stranger"),
                                   make_admin())
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            grant(legal, user=holder)
            owner_bits = owner_fields(user_principal(holder))
            private = make_agent(slug="r1-private", **owner_bits)
            private_labelled = make_agent(slug="r2-private-labelled", **owner_bits)
            wide = make_agent(slug="r3-wide", box_wide=True,
                              **owner_fields(user_principal(admin)))
            wide_labelled = make_agent(slug="r4-wide-labelled", box_wide=True,
                                       **owner_fields(user_principal(admin)))
            set_agent_labels(user_principal(admin), private_labelled, {legal.pk})
            set_agent_labels(user_principal(admin), wide_labelled, {legal.pk})

            holder_sees = set(visible_agents(user_principal(holder))
                              .values_list("slug", flat=True))
            stranger_sees = set(visible_agents(user_principal(stranger))
                                .values_list("slug", flat=True))
        assert private.slug in holder_sees and private.slug not in stranger_sees
        assert private_labelled.slug in holder_sees
        assert wide.slug in holder_sees and wide.slug in stranger_sees
        assert wide_labelled.slug in holder_sees
        assert wide_labelled.slug not in stranger_sees

    def test_the_AND_still_restricts_an_owner_of_a_labelled_row(self):
        """The AND applies to an owner too -- which is what makes
        labelling one's own agent actually restrict it rather than being
        bypassable by the person it is aimed at."""
        from agents.visibility import visible_agents

        owner, admin = make_user(), make_admin()
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="own-but-labelled",
                               **owner_fields(user_principal(owner)))
            set_agent_labels(user_principal(admin), agent, {legal.pk})
            sees = set(visible_agents(user_principal(owner))
                       .values_list("slug", flat=True))
        assert "own-but-labelled" not in sees
        assert agent.slug == "own-but-labelled"

    def test_a_label_set_from_THIS_page_narrows_the_row_the_same_way(self, client):
        """THE LOOP CLOSED. The truth table above is asserted against
        `set_agent_labels` called directly; this drives the same
        narrowing through the page's own POST, so the editor really is
        the audience control and not a form that writes rows nothing
        reads."""
        from agents.visibility import visible_agents

        admin, stranger = make_admin(), make_user(username="outsider")
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="narrowed-from-the-page", box_wide=True,
                               **owner_fields(user_principal(admin)))
            assert agent.slug in set(visible_agents(user_principal(stranger))
                                     .values_list("slug", flat=True))
            sign_in(client, admin)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(legal.pk)})
            after = set(visible_agents(user_principal(stranger))
                        .values_list("slug", flat=True))
        assert agent.slug not in after


class TestTheInstallInteraction:
    """Spec review M5, pinned against today's behaviour rather than
    asserted about the new column alone."""

    def test_a_member_installing_a_catalogue_slug_owns_a_box_wide_row_they_cannot_edit(
        self, client
    ):
        from agents.chat.views.agents import BOX_WIDE_SECTION_NOTE
        from agents.defaults import DEFAULT_AGENTS
        from agents.models import Agent
        from agents.visibility import may_manage_agent

        slug = DEFAULT_AGENTS[0].slug
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            client.post(reverse("chat-default-install"), {"kind": "agent", "slug": slug})
            row = Agent.objects.get(slug=slug)
            assert row.box_wide is True
            assert row.owner_kind == "user"
            assert may_manage_agent(user_principal(member), row) is False
            body = client.get(reverse("chat-agents")).content.decode()
        # THE DECLARED SENTENCE, not a substring of it -- the house rule
        # is that a user-facing sentence is declared once, in Python.
        assert BOX_WIDE_SECTION_NOTE in body
        assert row.name in body

    def test_the_row_still_reaches_everybody_exactly_as_it_did_before(self):
        from agents.defaults import DEFAULT_AGENTS, install_default
        from agents.visibility import visible_agents
        from identity.contracts.principals import Principal

        slug = DEFAULT_AGENTS[0].slug
        install_default("agent", slug, Principal("user", "5"))
        assert slug in set(visible_agents(Principal("user", "9"))
                           .values_list("slug", flat=True))
