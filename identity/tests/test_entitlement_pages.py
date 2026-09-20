"""`/identity/entitlements/` and one entitlement's grant page.

THE OWNER COLUMN IS WHAT THIS MODULE IS ABOUT. An entitlement owner
reaches the grant form for the entitlement they own -- and gets 404, not
403, for one they do not, because a 403 on a row-addressed URL confirms
the row exists.
"""
from __future__ import annotations

import re

import pytest
from django.urls import reverse
from django.utils.html import escape

from identity.models import Entitlement, EntitlementGrant
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.tests._helpers import (
    grant, make_admin, make_document, make_entitlement, make_group, make_user, posture,
    reset_settings, seed_sweep_posture, sign_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestTheListPage:
    def test_an_admin_creates_and_a_member_is_refused(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            assert client.post(reverse("identity-entitlements"),
                               {"name": "Finance"}).status_code == 302
            other = client.__class__()
            sign_in(other, member)
            assert other.get(reverse("identity-entitlements")).status_code == 403
        assert Entitlement.objects.filter(name="Finance").exists()

    def test_the_list_page_marks_itself_plain_current_not_current_parent(self, client):
        """SCOPE DISCIPLINE, THE OTHER DIRECTION (`TestTheEntitlementPage
        Nav`'s own sibling pin): the section's own INDEX marks itself
        current from its OWN exact url, not a child's -- it keeps the
        plain, inert `current` `foundation/templates/_shell.html`'s own
        `.current-parent` comment says every OTHER settings-nav entry
        still uses, unaffected by the round-17 addendum."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("identity-entitlements")).content.decode()
        assert f'href="{reverse("identity-entitlements")}" class="current"' in body
        assert f'href="{reverse("identity-entitlements")}" class="current-parent"' not in body


class TestTheEntitlementPage:
    def test_an_owner_reaches_their_own_and_404s_on_another(self, client):
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            assert client.get(
                reverse("identity-entitlement-edit", args=[finance.pk])).status_code == 200
            assert client.get(
                reverse("identity-entitlement-edit", args=[legal.pk])).status_code == 404

    def test_a_plain_member_404s_rather_than_403s(self, client):
        """404, never 403, on a row-addressed URL: a 403 would confirm
        that this entitlement exists."""
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.get(reverse("identity-entitlement-edit", args=[finance.pk]))
        assert response.status_code == 404

    def test_an_owner_may_grant_and_revoke_but_not_rename_or_delete(self, client):
        owner, member = make_user(), make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        url = reverse("identity-entitlement-edit", args=[finance.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            client.post(url, {"action": "grant", "subject": f"user:{member.pk}",
                              "role": "member"})
            assert EntitlementGrant.objects.filter(entitlement=finance, user=member).exists()
            row = EntitlementGrant.objects.get(entitlement=finance, user=member)
            client.post(url, {"action": "revoke", "grant": row.pk})
            assert not EntitlementGrant.objects.filter(pk=row.pk).exists()
            rename = client.post(url, {"action": "rename", "name": "Accounts"}, follow=True)
            assert b"Only an administrator" in rename.content
            finance.refresh_from_db()
            assert finance.name == "Finance"
            client.post(url, {"action": "delete"}, follow=True)
            assert Entitlement.objects.filter(pk=finance.pk).exists()

    def test_the_delete_confirmation_names_the_counts_first(self, client):
        """Done-when 6. Deleting an entitlement silently WIDENS access to
        every document it was the last label on, so the page says how
        many before it offers the button."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        grant(finance, user=make_user())
        grant(finance, group=make_group())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.get(reverse("identity-entitlement-edit", args=[finance.pk]))
        assert b"Grants: 2" in response.content

    def test_an_owner_may_flip_a_grants_role(self, client):
        """`set_role` reaches the page through the same Make owner/Make
        member buttons `entitlement.html` renders on every grant row,
        but had no HTTP-level pin of its own -- this is that pin."""
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        member = make_user()
        row = grant(finance, user=member, role=EntitlementGrant.Role.MEMBER)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("identity-entitlement-edit", args=[finance.pk]),
                {"action": "set_role", "grant": row.pk, "role": "owner"})
        assert response.status_code == 302
        row.refresh_from_db()
        assert row.role == EntitlementGrant.Role.OWNER

    def test_an_unrecognised_role_flashes_a_refusal_and_never_500s(self, client):
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        member = make_user()
        row = grant(finance, user=member, role=EntitlementGrant.Role.MEMBER)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("identity-entitlement-edit", args=[finance.pk]),
                {"action": "set_role", "grant": row.pk, "role": "bogus"}, follow=True)
        assert response.status_code == 200
        assert b"is not a grant role" in response.content
        assert b"Traceback" not in response.content
        row.refresh_from_db()
        assert row.role == EntitlementGrant.Role.MEMBER

    def test_revoking_a_grant_from_another_entitlement_is_refused(self, client):
        """The grant id arrives in a POST body. Without the belongs-to
        check an owner of Finance could revoke a Legal grant by guessing
        a sequential id -- and the audit row would read as legitimate."""
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        elsewhere = grant(legal, user=make_user())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("identity-entitlement-edit", args=[finance.pk]),
                                   {"action": "revoke", "grant": elsewhere.pk})
        assert response.status_code == 404
        assert EntitlementGrant.objects.filter(pk=elsewhere.pk).exists()

    def test_a_non_numeric_grant_id_answers_404_not_500(self, client):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-entitlement-edit", args=[finance.pk]),
                                   {"action": "revoke", "grant": "not-a-number"})
        assert response.status_code == 404
        assert b"Traceback" not in response.content

    def test_an_unrecognised_action_flashes_and_redirects(self, client):
        """F7's class (Coherence Wave C): this dispatcher used to answer a
        raw 400 -- a bare plain-text page with no shell, no sidebar and no
        flash, on a settings surface reached from a plain form. It flashes
        and redirects back to this entitlement's own page now, which is
        still not a silent no-op: the message says exactly what the 400
        body said."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-entitlement-edit", args=[finance.pk]),
                                   {"action": "explode"}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse(
            "identity-entitlement-edit", args=[finance.pk])
        assert escape("'explode' is not a recognised action.") in response.content.decode()
        assert Entitlement.objects.filter(pk=finance.pk).exists()


class TestTheEntitlementPageNav:
    """ROUND 17 ADDENDUM (the round-16 nav-link reviewer's own follow-on
    finding): this page marks its PARENT sidebar entry (Entitlements)
    current FROM A CHILD url (this entitlement's own detail page, not
    `identity-entitlements` itself) -- `foundation/templates/_shell.
    html`'s own `.current-parent` comment has the full reasoning.
    Before this fix the `Entitlements` entry here was bolded and DEAD:
    `nav.settings-nav a.current`'s own `pointer-events: none` made it
    unclickable, with no other link back to the list anywhere on this
    page.
    """

    def test_the_entitlements_entry_is_marked_current_parent_not_current(self, client):
        # ADMIN, NOT A MERE OWNER: `_settings.html`'s own Access/Box
        # groups (the sidebar section "Entitlements" itself lives in)
        # are gated on `identity_is_admin`, which an owner-only grant
        # does not carry -- an owner reaches this PAGE fine (`identity.
        # views.entitlement_edit`'s own gate, `admin or owned`), but
        # would see no settings sidebar at all for this assertion to
        # find anything in.
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("identity-entitlement-edit", args=[finance.pk])).content.decode()
        assert f'href="{reverse("identity-entitlements")}" class="current-parent"' in body
        assert f'href="{reverse("identity-entitlements")}" class="current"' not in body

    def test_the_current_parent_rule_carries_no_pointer_events_none(self, client):
        """Non-vacuous companion to the markup pin above: a real `href`
        alone is not enough (`nav.top-nav a.current`'s own pre-round-16
        history is exactly that -- a real `href`, made dead by CSS) --
        this proves the RULE `class="current-parent"` resolves to on
        this rendered page is not the inert one."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("identity-entitlement-edit", args=[finance.pk])).content.decode()
        start = body.index("nav.settings-nav a.current-parent {")
        rule = body[start:body.index("}", start)]
        assert "pointer-events" not in rule
        assert "font-weight: 600;" in rule


class TestTheEntitlementPageShowsItsFullReach:
    def test_it_names_every_kind_in_one_place(self, client):
        """One page, one answer. An operator who has to open six pages to
        learn what an entitlement touches does not have control of it."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        grant(finance, user=make_user())
        grant(finance, group=make_group())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("identity-entitlement-edit", args=[finance.pk])).content
        for label in (b"Grants", b"Document labels", b"Tool labels",
                      b"Model set attachments", b"Agent and flow labels"):
            assert label in body

    def test_the_panel_and_the_delete_confirmation_cannot_disagree(self, client):
        """SOURCED FROM THE SAME FUNCTION. Two counters answering "what
        does this entitlement touch" is how a page comes to reassure
        somebody about a delete that then removes something else.

        NON-ZERO IN MULTIPLE KINDS, not just the grant (T16 follow-up
        review, finding 3): a fixture with only a grant makes `reach ==
        counts` a comparison of all-zeros, which would pass even if the
        two functions disagreed on every cascade kind and only happened
        to agree that each was empty. A labelled document and a labelled
        tool give the equality real terms to fail on.
        """
        from django.apps import apps
        from identity import services
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        grant(finance, user=make_user())
        document = make_document()
        apps.get_model("rag.DocumentEntitlement").objects.create(
            document=document, entitlement=finance)
        apps.get_model("agents.ToolEntitlement").objects.create(
            tool_key="rag.ingest", entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            reach = services.entitlement_reach(finance)
            counts = services.entitlement_delete_counts(finance)
        assert reach == counts
        assert reach["Grants"] == 1
        assert reach["Document labels"] == 1
        assert reach["Tool labels"] == 1


class TestTheAxisEditors:
    """THE MANAGE-ONE SCREEN. An entitlement page used to answer "what do
    you reach" and offer no way to change it: an operator who wanted a
    tool labelled had to leave, find Tool access, and edit that tool's
    own row. Two projections, one object -- this is the entitlement-major
    one, and it is rendered from the axis registry, so a sixth labelled
    kind later is a registration rather than a section of markup.
    """

    def _url(self, entitlement):
        return reverse("identity-entitlement-edit", args=[entitlement.pk])

    def _body(self, client, principal, entitlement):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, principal)
            return client.get(self._url(entitlement)).content.decode()

    def _section(self, body, key):
        """One panel's own markup, from its opening tag to the next
        panel's -- so a pin about THIS panel cannot be satisfied by
        markup belonging to another.

        A `<div>` since phase 2: the fragment used to open a `<section>`
        and took its card from this page's own bare `section` rule,
        which the CSS gate's element check went red on the moment the
        two access pages became its second and third consumers."""
        marker = body.index(f'id="axis-{key}"')
        start = body.rindex("<div", 0, marker)
        after = body.find('class="transfer-panel', marker)
        return body[start:after] if after != -1 else body[start:]

    def test_an_admin_gets_one_two_pane_panel_per_editable_axis(self, client):
        body = self._body(client, make_admin(), make_entitlement(name="Finance"))
        for key in ("agents.tools", "agents.agents", "agents.flows",
                    "inference.model_sets"):
            assert f'id="axis-{key}"' in body, key
        # COUNTED PANE HEADINGS, both of them, and the buttons that move
        # rows between them -- the component's own shape.
        assert "Available —" in body
        assert "Active —" in body
        assert 'name="op" value="add"' in body
        assert 'name="op" value="remove"' in body

    def test_documents_get_a_reach_count_but_no_panel(self, client):
        """The deliberate asymmetry: a document label is the one labelled
        kind an entitlement OWNER may write, and the library page owns
        that write."""
        body = self._body(client, make_admin(), make_entitlement(name="Finance"))
        assert "Document labels" in body          # the reach panel, unchanged
        assert 'id="axis-rag.documents"' not in body

    def test_an_owner_sees_no_panels_at_all(self, client):
        """ADMINISTRATOR ONLY (spec section 7.4: an owner's three
        capabilities are grants, revocations and document labels). A page
        must not offer a control whose POST answers "no", so an owner's
        page is exactly what it always was."""
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        body = self._body(client, owner, finance)
        # THE MARKUP, not the string: the panel's CSS lives in
        # `_settings.html` and so ships on every settings page whether or
        # not one renders -- it is the `class=` that proves a panel was
        # built.
        assert 'class="transfer-panel"' not in body
        assert "Available —" not in body
        # AND NO SCRIPT EITHER: the filter script is gated on there being
        # a panel for it to attach to.
        assert "data-filter-rows" not in body
        assert "Grants" in body                   # the page they had, intact

    def test_adding_a_tool_writes_through_the_columns_own_writer(self, client):
        """The audit row is the assertion. `/chat/access/` reads that
        trail, so an axis editor that wrote the join table directly would
        make an operator's change invisible there."""
        from django.apps import apps

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(self._url(finance), {
                "action": "axis", "axis": "agents.tools", "op": "add",
                "add": ["rag.search"]})
        assert apps.get_model("agents.ToolEntitlement").objects.filter(
            entitlement=finance, tool_key="rag.search").exists()
        assert apps.get_model("identity.AuditEvent").objects.filter(
            action="tool.labelled", target_key="rag.search").exists()
        assert response.status_code == 302

    def test_removing_a_tool_writes_and_audits_the_other_direction(self, client):
        from django.apps import apps

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        apps.get_model("agents.ToolEntitlement").objects.create(
            tool_key="rag.search", entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(self._url(finance), {
                "action": "axis", "axis": "agents.tools", "op": "remove",
                "remove": ["rag.search"]})
        assert not apps.get_model("agents.ToolEntitlement").objects.filter(
            entitlement=finance).exists()
        assert apps.get_model("identity.AuditEvent").objects.filter(
            action="tool.unlabelled", target_key="rag.search").exists()

    def test_the_button_pressed_decides_which_pane_is_honoured(self, client):
        """ONE FORM SPANS BOTH PANES, so a submit carries every ticked box
        in both. A stray tick in the pane the operator did not act on --
        left over from a filter, or a mis-click -- must not act."""
        from django.apps import apps

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        apps.get_model("agents.ToolEntitlement").objects.create(
            tool_key="rag.ingest", entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(self._url(finance), {
                "action": "axis", "axis": "agents.tools", "op": "add",
                "add": ["rag.search"], "remove": ["rag.ingest"]})
        labelled = set(apps.get_model("agents.ToolEntitlement").objects
                       .filter(entitlement=finance).values_list("tool_key", flat=True))
        assert labelled == {"rag.ingest", "rag.search"}

    def test_the_save_lands_back_on_the_panel_it_edited(self, client):
        """An entitlement with four axes is a long page; a save that
        returned to the top would make every edit cost a scroll."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(self._url(finance), {
                "action": "axis", "axis": "agents.tools", "op": "add",
                "add": ["rag.search"]})
        assert response["Location"].endswith("#axis-agents.tools")

    def test_the_flash_says_what_changed(self, client):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(self._url(finance), {
                "action": "axis", "axis": "agents.tools", "op": "add",
                "add": ["rag.search"]}, follow=True)
        assert "Tools: 1 added, 0 removed." in response.content.decode()

    def test_an_id_outside_the_catalogue_is_refused_f7_shape(self, client):
        """Flash-and-redirect, never a bare 400, and never a write."""
        from django.apps import apps

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(self._url(finance), {
                "action": "axis", "axis": "agents.tools", "op": "add",
                "add": ["not.a.tool"]}, follow=True)
        assert response.redirect_chain[-1][0].endswith("#axis-agents.tools")
        assert escape("'not.a.tool' is not a tools row on this install.") in \
            response.content.decode()
        assert not apps.get_model("agents.ToolEntitlement").objects.filter(
            entitlement=finance).exists()

    def test_an_unregistered_axis_is_refused_and_lands_on_the_page_top(self, client):
        """The anchor is assembled only for an axis that really exists --
        never from whatever was posted."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(self._url(finance), {
                "action": "axis", "axis": "made.up", "op": "add",
                "add": ["rag.search"]}, follow=True)
        assert response.redirect_chain[-1][0] == self._url(finance)
        assert escape("'made.up' is not an editable kind on this box.") in \
            response.content.decode()

    def test_an_unrecognised_operation_is_refused(self, client):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(self._url(finance), {
                "action": "axis", "axis": "agents.tools", "op": "explode",
                "add": ["rag.search"]}, follow=True)
        assert escape("'explode' is not a recognised operation.") in \
            response.content.decode()

    def test_an_owner_posting_the_axis_action_is_refused(self, client):
        """The gate is the SERVICE, not the template: hiding the panels
        from an owner is the courtesy, refusing their POST is the rule."""
        from django.apps import apps

        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(self._url(finance), {
                "action": "axis", "axis": "agents.tools", "op": "add",
                "add": ["rag.search"]}, follow=True)
        assert b"Only an administrator" in response.content
        assert not apps.get_model("agents.ToolEntitlement").objects.filter(
            entitlement=finance).exists()

    def test_the_page_stores_nothing_in_the_browser(self, client):
        """The no-browser-storage convention, pinned on this surface the
        same way `tools/vision/tests/test_views_create.py` pins it on
        its own."""
        body = self._body(client, make_admin(), make_entitlement(name="Finance"))
        for token in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
            assert token not in body, token

    def test_the_page_carries_the_filter_script_and_the_chromes_guard_and_nothing_else(
            self, client):
        """ONE HOME FOR THE FILTER LOGIC (`foundation/templates/
        _filter_rows_script.html`), reused by the chat tag dropdown, the
        pin chooser and these panels -- never a fourth copy. It stopped
        being the ONLY script here on 2026-09-16, so the name that said
        so has gone.

        THE SECOND IS THE SETTINGS CHROME'S unsaved-input navigation
        guard (`foundation/templates/_settings.html`), on every settings
        page whatever it renders, with its own claims pinned in
        `foundation/tests/test_shell.py::TestTheUnsavedInputGuard`. This
        page's own budget is the count below minus that one, and is
        unchanged.

        BOTH ARE IDENTIFIED, not just counted, so losing either goes red
        rather than being absorbed by a third arriving -- each token
        below appears in that script's BODY and nowhere in the markup
        (`data-filter-scope` alone would not do: the panel's own inputs
        carry that attribute whether the script ships or not).
        """
        body = self._body(client, make_admin(), make_entitlement(name="Finance"))
        assert body.count("<script>") == 2
        assert "[data-filter-rows][data-filter-scope]" in body   # the filter script
        assert 'window.addEventListener("beforeunload"' in body  # the chrome's guard
        # PROGRESSIVE ENHANCEMENT, in the rendered page: nothing the
        # panel does needs the filter script, and the search box says so.
        assert "requires JavaScript" in body
        # NO DOCUMENT-DELEGATED LISTENER OF THIS PAGE'S OWN. The guard is
        # document-delegated by design -- it is the recorder, and the
        # invariant is about a page script CANCELLING a submit in one,
        # not about delegation itself (`test_shell.py::
        # TestTheGuardsOrderingInvariant`). Counted, not forbidden.
        assert body.count("document.addEventListener") == 1
        assert "<form target=" not in body


    def test_the_documents_door_comes_from_the_registry_not_from_this_template(
            self, client):
        """FIX ROUND 2, P2-I1, and the ONLY pin that distinguishes a
        registered door from a hand-written one: remove the axis and the
        section must go with it. Before this round the Documents section
        was markup in this template plus a `reverse("rag-documents")` in
        `identity/views.py`, and unregistering `rag.documents` changed
        nothing at all.
        """
        from identity.contracts import axes as axes_module

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            with_door = client.get(self._url(finance)).content.decode()
            saved = axes_module._AXES.pop("rag.documents")
            try:
                without = client.get(self._url(finance)).content.decode()
            finally:
                axes_module._AXES["rag.documents"] = saved
        assert 'id="door-rag.documents"' in with_door
        assert "Browse the documents labelled Finance" in with_door
        assert "door-rag.documents" not in without
        assert "Browse the documents" not in without

    def test_an_owner_gets_the_door_as_well(self, client):
        """Labelling documents is one of an entitlement owner's three
        capabilities, so the way into the library is as much theirs as
        an administrator's -- unlike the panels, which are not."""
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        body = self._body(client, owner, finance)
        assert 'id="door-rag.documents"' in body
        assert 'class="transfer-panel"' not in body

    def test_the_door_states_no_count_of_its_own(self, client):
        """The reach panel is the single home for counts (`:244`); a
        second number on one page is how a page comes to disagree with
        itself."""
        from django.apps import apps

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        for _ in range(3):
            apps.get_model("rag.DocumentEntitlement").objects.create(
                document=make_document(), entitlement=finance)
        import html as _html

        body = self._body(client, admin, finance)
        start = body.index('id="door-rag.documents"')
        door = body[body.index(">", start) + 1:body.index("</section>", start)]
        # THE READER'S OWN TEXT: tags stripped (the pk lives in an href,
        # `h2` is a tag name) and entities unescaped (`&#x27;` is an
        # apostrophe, not a number). What this pins is that the door
        # prints no NUMBER at a reader -- the reach panel is the single
        # home for those.
        prose = _html.unescape(re.sub(r"<[^>]*>", "", door))
        assert not re.search(r"\d", prose), prose
        # And the reach panel, which IS the single home, still says it.
        assert "Document labels" in body and ">3<" in body

    def test_enter_in_a_filter_box_can_never_mutate(self, client):
        """FIX ROUND 1, I2. HTML's implicit-submission rule activates a
        form's FIRST submit button when Enter is pressed in a text-like
        field inside it -- which, when the filter boxes belonged to the
        panel's own form, was the left pane's `Add`. Ticking three rows
        in the Active pane, typing in that pane's filter and pressing
        Enter posted `op=add` with an empty left pane: three ticks
        discarded and "0 added, 0 removed" reported as success, on a
        page whose whole promise is that it works without JavaScript.

        PINNED STRUCTURALLY, because a test client cannot press a key:
        each filter input is associated by `form=` with a sibling form
        that is NOT a POST, has no fields of its own and no submit
        button at all -- so whatever a browser decides to do with Enter
        there, it cannot be a write.

        The form's id gained its `axis-` stem in fix round 2 (P2-N1):
        three call sites had drifted into three different answers for
        `tp_key`, all valid, and every consumer now passes its own
        `tp_anchor`. Markup only -- the mechanism this pins is unchanged.
        """
        from django.apps import apps

        finance = make_entitlement(name="Finance")
        # BOTH PANES NON-EMPTY, so the "writing form untouched" half
        # below has a checkbox of each name to find.
        apps.get_model("agents.ToolEntitlement").objects.create(
            tool_key="rag.search", entitlement=finance)
        body = self._body(client, make_admin(), finance)
        section = self._section(body, "agents.tools")

        searches = re.findall(r"<input type=\"search\"[^>]*>", section)
        assert len(searches) == 2, searches
        for tag in searches:
            assert 'form="transfer-filter-axis-agents.tools"' in tag, tag

        inert = re.search(
            r"<form id=\"transfer-filter-axis-agents\.tools\"([^>]*)>(.*?)</form>",
            section, re.S)
        assert inert is not None, "the inert filter form is gone"
        assert 'method="post"' not in inert.group(1)
        assert "submit" not in inert.group(2)
        assert inert.group(2).strip() == "", inert.group(2)

        # AND THE WRITING FORM IS UNTOUCHED: still one POST form with
        # both panes' checkboxes and both operation buttons in it.
        assert section.count('<form method="post"') == 1
        assert 'name="add"' in section and 'name="remove"' in section
        assert 'name="op" value="add"' in section
        assert 'name="op" value="remove"' in section

    def test_each_pane_labels_its_own_checkbox_group(self, client):
        """FIX ROUND 2, P2-M2. The chip picker these panels replaced on
        the access pages carried `role="group"` and an `aria-labelledby`
        pointing at its own "Entitlements" label; the panel's two lists
        carried neither, so a screen reader met two unlabelled runs of
        checkboxes. Fixed in the FRAGMENT, so both directions and all
        three consumers get it."""
        body = self._body(client, make_admin(), make_entitlement(name="Finance"))
        section = self._section(body, "agents.tools")
        for side in ("available", "active"):
            heading_id = f"axis-agents.tools-{side}"
            assert f'<h3 id="{heading_id}">' in section, side
            assert f'role="group" aria-labelledby="{heading_id}"' in section, side

    def test_a_counted_only_axis_refusal_lands_on_the_page_top(self, client):
        """FIX ROUND 1, M3. `axis_for` answers for a COUNTED-ONLY axis
        too, so a forged `axis=rag.documents` was refused correctly and
        then redirected to `#axis-rag.documents` -- a fragment
        identifier this page never emits. Only an axis that really
        renders a panel has an anchor to land on."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(self._url(finance), {
                "action": "axis", "axis": "rag.documents", "op": "add",
                "add": ["1"]}, follow=True)
        assert response.redirect_chain[-1][0] == self._url(finance)
        assert escape("'rag.documents' is not an editable kind on this box.") in \
            response.content.decode()
        assert 'id="axis-rag.documents"' not in response.content.decode()

    @pytest.mark.parametrize("payload", [
        {"action": "axis"},
        {"action": "axis", "axis": "agents.tools"},
        {"action": "axis", "axis": "", "op": "add"},
        {"action": "axis", "axis": "agents.tools", "op": ""},
        {"action": "axis", "axis": "agents.tools", "op": "add"},
        {"action": "axis", "axis": "agents.tools", "op": "add", "add": [""]},
        {"action": "axis", "axis": "agents.tools", "op": "remove",
         "remove": ["../../etc/passwd"]},
        {"action": "axis", "axis": "agents.agents", "op": "add", "add": ["not-a-pk"]},
        {"action": "axis", "axis": "agents.agents", "op": "add", "add": ["999999"]},
        {"action": "axis", "axis": "inference.model_sets", "op": "remove",
         "remove": ["0"]},
        {"action": "axis", "axis": "rag.documents", "op": "add", "add": ["1"]},
    ])
    def test_a_tampered_axis_post_never_500s(self, client, payload):
        """NEVER 500, house style (`agents/chat/tests/test_never_500.py`'s
        own shape, in identity's own test home). Every field of this
        action arrives from a form body -- the axis key, the operation,
        and a list of ids that may be blank, non-numeric, a path, a pk
        that no longer exists, or a row on a COUNTED-ONLY axis that has
        no writer at all. Each reads back as a flash on the page it came
        from, never a traceback.
        """
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(self._url(finance), payload, follow=True)
        assert response.status_code == 200
        assert b"Traceback" not in response.content
        assert Entitlement.objects.filter(pk=finance.pk).exists()

    def test_a_panel_row_carries_the_page_only_marker_for_a_tool_no_agent_gets(
            self, client):
        """LABELLABLE IS NOT GRANTABLE, carried through the catalogue
        rather than reasoned about again here."""
        from agents.axes import PAGE_ONLY_NOTE

        body = self._body(client, make_admin(), make_entitlement(name="Finance"))
        assert PAGE_ONLY_NOTE in body


class TestTheListPageIsTheManageAllScreen:
    """A box runs to fifty entitlements. A list of names and one grant
    count is a page you read; a searchable list with one reach column per
    kind is a page you WORK from -- and the difference at fifty rows is
    whether the operator has to open every one of them to find out which
    is which.
    """

    _URL_ARGS = ()

    def _url(self):
        return reverse("identity-entitlements")

    def test_the_search_filters_on_name_and_on_description(self, client):
        admin = make_admin()
        make_entitlement(name="Finance", description="Ledgers and payroll")
        make_entitlement(name="Legal", description="Contracts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            by_name = client.get(self._url(), {"q": "fina"}).content.decode()
            by_description = client.get(self._url(), {"q": "payroll"}).content.decode()
            unfiltered = client.get(self._url()).content.decode()
        assert "Finance" in by_name and "Legal" not in by_name
        assert "Finance" in by_description and "Legal" not in by_description
        assert "Finance" in unfiltered and "Legal" in unfiltered

    def test_a_search_that_matches_nothing_says_so(self, client):
        admin = make_admin()
        make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(self._url(), {"q": "nothing-like-this"}).content.decode()
        assert "No entitlement matches that." in body

    def test_the_create_form_keeps_the_operator_in_their_search(self, client):
        """A create that dumped them back to an unfiltered list would
        lose the search they were working in."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(self._url(), {"name": "Finance", "q": "fin"})
        assert response["Location"].endswith("?q=fin")
        assert Entitlement.objects.filter(name="Finance").exists()

    def test_every_axis_gets_a_column_and_the_counts_are_right(self, client):
        from django.apps import apps

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        grant(finance, user=make_user())
        apps.get_model("agents.ToolEntitlement").objects.create(
            tool_key="rag.search", entitlement=finance)
        apps.get_model("agents.ToolEntitlement").objects.create(
            tool_key="rag.ingest", entitlement=finance)
        apps.get_model("rag.DocumentEntitlement").objects.create(
            document=make_document(), entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(self._url()).content.decode()
        for heading in ("Tools", "Agents", "Flows", "Model sets", "Documents",
                        "Holders"):
            assert f'<th class="reach">{heading}</th>' in body, heading
        # The row itself: two tools, one document, no agents/flows/sets,
        # one holder -- in the registry's own display order.
        row = body[body.index(">Finance<"):]
        cells = re.findall(r'<td class="reach">(\d+)</td>', row)[:6]
        assert cells == ["2", "0", "0", "0", "1", "1"]

    def test_the_table_costs_the_same_queries_at_one_row_and_at_thirty(self, client):
        """THE FLAT-QUERY PIN. Every number in the table comes from one
        aggregate per axis over the whole join table; a per-row counter
        would cost five queries per row.

        NON-VACUOUS BY CONSTRUCTION: with a per-entitlement reach read --
        `services.entitlement_reach(row)` in the loop, which is the
        obvious wrong implementation and the one this page's own detail
        view uses for a SINGLE entitlement -- the thirty-row render costs
        about a hundred and fifty queries more than the one-row render,
        so this equality fails by two orders of magnitude rather than by
        a rounding error. The body assertions below are what stop a view
        that renders nothing at all from passing it.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        admin = make_admin()
        make_entitlement(name="E00")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.get(self._url())                    # warm the session reads
            with CaptureQueriesContext(connection) as one_row:
                assert client.get(self._url()).status_code == 200
            for index in range(1, 30):
                make_entitlement(name=f"E{index:02d}")
            with CaptureQueriesContext(connection) as thirty_rows:
                body = client.get(self._url()).content.decode()

        assert len(thirty_rows) == len(one_row), (
            len(one_row), len(thirty_rows), [q["sql"] for q in thirty_rows])
        # Really thirty rendered rows, and really the reach columns --
        # otherwise the equality above is a comparison of two empty pages.
        for index in range(30):
            assert f"E{index:02d}" in body
        assert '<th class="reach">Tools</th>' in body

    def test_the_search_form_carries_the_assistant_flag_and_only_when_open(self, client):
        """FIX ROUND 1, M2. The route-matrix sweep inspects only
        `method="post"` form ACTIONS, so nothing in the tree noticed
        this: delete the hidden field and the assistant panel shuts on
        every search, with a fully green suite.

        A GET form cannot carry the flag on its `action` at all -- a
        browser discards that query string and rebuilds it from the
        fields -- so it rides a hidden input, and the collapsed half is
        pinned byte-for-byte the way this repo pins it elsewhere: with
        the panel shut, the page says nothing about `assistant`.
        """
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            opened = client.get(self._url(), {"assistant": "1"}).content.decode()
            shut = client.get(self._url()).content.decode()
        assert '<input type="hidden" name="assistant" value="1">' in opened
        assert 'name="assistant"' not in shut

    def test_the_list_is_ordered_by_name(self, client):
        """A PRE-EXISTING FAULT THIS ROUND FIXES, not a new feature.
        `.annotate()` with an aggregate CLEARS `Meta.ordering` (Django
        drops it so the implicit GROUP BY cannot be wrong), so this page
        had been listing entitlements in whatever order PostgreSQL
        returned rows since the grant count was added -- unnoticed at
        three, unusable at fifty."""
        admin = make_admin()
        for name in ("Zulu", "Alpha", "Mike"):
            make_entitlement(name=name)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(self._url()).content.decode()
        assert [m for m in re.findall(r">(Zulu|Alpha|Mike)<", body)] == [
            "Alpha", "Mike", "Zulu"]

    def test_the_page_keeps_its_name(self, client):
        """`foundation/tests/test_page_names.py` pins h1 and title as
        "Entitlements"; this round changed what the page DOES, not what
        it is called."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(self._url()).content.decode()
        assert "<h1>Entitlements</h1>" in body
