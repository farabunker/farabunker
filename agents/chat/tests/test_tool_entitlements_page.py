"""`/chat/tools/` -- which entitlement a registered tool needs.

CLASS S. Labelling a tool is library-wide operator policy: unlike a
document, a tool has no entitlement owner (spec section 7.4 names three
owner capabilities, and all three are about documents and grants).

THE SHELL-PATH CONSEQUENCE IS STATED ON THE PAGE, not discovered later.
A service principal holds no entitlements -- grants attach to a user or
a group by the XOR constraint, and service-account tokens are IA-3 -- so
labelling a tool makes it PERMANENTLY unavailable to the watcher and to
`manage.py agent_turn`, and the honest failure appears as an agent that
cannot find anything rather than as a refusal.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (
    make_admin, make_agent, make_entitlement, make_flow, make_user, posture,
    reset_settings, seed_sweep_posture, sign_in,
)
from agents.models import ToolEntitlement
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestTheToolLabelPage:
    def test_an_admin_sees_every_grantable_tool_and_a_member_gets_403(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.get(reverse("chat-tool-entitlements"))
            assert response.status_code == 200
            assert b"rag.search" in response.content
            other = client.__class__()
            sign_in(other, member)
            assert other.get(reverse("chat-tool-entitlements")).status_code == 403

    def test_grantable_tools_still_excludes_every_mutating_spec(self):
        """`grantable_tools()` excludes every `mutates=True` spec, and
        ADR 0010's rule that such a tool is registered but not grantable
        stays in force through IA-2. This is NOT authority against ever
        labelling a mutating tool: spec section 9.3 and the author
        decision recorded at section 22.35 have a mutating tool
        (`rag.ingest`, `vision.generate`) enforced by an in-view
        `tool_access_for` gate instead of `grantable_tools()`, and a
        later task lists it on this same page too, marked page-only."""
        from agents.contracts.tools import grantable_tools
        assert all(not spec.mutates for spec in grantable_tools())

    def test_posting_labels_writes_them_and_redirects(self, client):
        """RE-PINNED IN PHASE 2, same behaviour, new field shape: the
        row's whole-set `entitlements` field became the transfer panel's
        `op` + `add`/`remove`, so that two administrators editing
        different entitlements on one tool cannot clobber each other.
        What is asserted -- the write lands, the response redirects --
        is exactly what it was."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-tool-entitlements"),
                                   {"tool_key": "rag.search", "op": "add",
                                    "add": [str(finance.pk)]})
        assert response.status_code == 302
        assert ToolEntitlement.objects.filter(tool_key="rag.search",
                                              entitlement=finance).exists()

    def test_removing_every_active_entitlement_leaves_the_tool_unlabelled(self, client):
        """RE-PINNED IN PHASE 2 and RENAMED, because the old name
        described a field shape rather than a behaviour: "posting an
        empty set" was how the whole-set form said "no labels". With
        add/remove there is no empty set to post -- clearing a tool is
        ticking everything in the Active pane and pressing Remove -- and
        the behaviour that matters (a tool can be returned to
        unlabelled, which WIDENS it to everyone signed in) is unchanged
        and pinned here."""
        admin = make_admin()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("chat-tool-entitlements"),
                        {"tool_key": "rag.search", "op": "remove",
                         "remove": [str(finance.pk), str(legal.pk)]})
        assert ToolEntitlement.objects.count() == 0

    def test_a_remove_touches_only_the_ticked_entitlement(self, client):
        """THE POINT OF ADD/REMOVE. The old whole-set POST carried every
        other entitlement's checkbox with it, so a stale form silently
        reverted a colleague's change; a difference cannot."""
        admin = make_admin()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("chat-tool-entitlements"),
                        {"tool_key": "rag.search", "op": "remove",
                         "remove": [str(finance.pk)]})
        assert set(ToolEntitlement.objects.values_list("entitlement_id", flat=True)) == {
            legal.pk}

    def test_the_button_pressed_decides_which_pane_is_honoured(self, client):
        """One form spans both panes, so a submit carries every ticked
        box in both -- honouring only the pressed side is what stops a
        stray tick in the other pane from acting."""
        admin = make_admin()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("chat-tool-entitlements"),
                        {"tool_key": "rag.search", "op": "add",
                         "add": [str(finance.pk)], "remove": [str(legal.pk)]})
        assert set(ToolEntitlement.objects.values_list("entitlement_id", flat=True)) == {
            finance.pk, legal.pk}

    def test_an_unknown_tool_key_flashes_and_redirects_not_a_silent_no_op(self, client):
        """F7 (Coherence Wave B): a raw 400 dumped the operator out of the
        chrome -- no shell, no sidebar, no flash -- one of the two sites
        the surface audit named. Now the house flash-and-redirect shape
        every other mutation on this platform uses. NOT A COMPLETENESS
        CLAIM (M4, Coherence Wave B review): other raw-400 escapes of the
        same shape survive elsewhere on settings-chrome pages outside
        F7's two named sites (e.g. `identity/views.py`'s
        `_entitlement_action`, `models/registry/views.py`'s
        `model_set_edit`) -- F7's brief scope was these two, not every
        instance of the pattern in the tree."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-tool-entitlements"),
                                   {"tool_key": "not.registered", "entitlements": []},
                                   follow=True)
        assert response.status_code == 200
        assert response.redirect_chain
        assert "not.registered" in response.content.decode()
        assert "is not a tool on this install" in response.content.decode()
        assert ToolEntitlement.objects.count() == 0

    def test_a_non_numeric_entitlement_id_is_refused_not_crashed(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-tool-entitlements"),
                                   {"tool_key": "rag.search", "entitlements": ["abc"]},
                                   follow=True)
        assert response.status_code == 200
        assert b"Traceback" not in response.content
        assert ToolEntitlement.objects.count() == 0

    def test_an_entitlement_id_that_does_not_exist_is_refused(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("chat-tool-entitlements"),
                        {"tool_key": "rag.search", "op": "add",
                         "add": ["99999"]}, follow=True)
        assert ToolEntitlement.objects.count() == 0

    def test_the_page_names_the_shell_path_consequence(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("chat-tool-entitlements")).content
        assert b"command line" in body

    def test_a_tool_a_shipped_agent_declares_carries_its_own_warning(self, client):
        """`manage.py agent_turn` runs an agent as the one service
        principal, so labelling a tool a RESIDENT agent declares makes
        that shell path silently weaker. The form says which."""
        from agents.chat.tests._helpers import make_agent
        admin = make_admin()
        make_agent(slug="librarian", resident=True, enabled=True, tool_keys=["rag.search"])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("chat-tool-entitlements")).content
        assert b"librarian" in body


class TestAMutatingToolIsLabellableButStillUngrantable:
    def test_the_page_lists_it_and_marks_it_page_only(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("chat-tool-entitlements")).content
        assert b"rag.ingest" in body
        assert b"No agent can be granted this tool" in body

    def test_labelling_it_leaves_it_uncallable_by_every_agent(self):
        """The two rules do not collide, and this is where that is
        proved rather than argued. `granted_tools` drops a `mutates=True`
        key for `mutates=True`, before any access check runs."""
        from agents.contracts.tools import ToolAccess, granted_tools
        from identity.contracts.principals import OPEN_PRINCIPAL
        access = ToolAccess(required={"rag.ingest": frozenset({1})},
                            held=frozenset({1}), unrestricted=False)
        assert granted_tools(OPEN_PRINCIPAL, ["rag.ingest"], access) == []

    def test_posting_its_labels_actually_writes_them(self, client):
        """T13 review finding 1: `_save`'s membership check widened from
        `grantable_tools()` to `all_tools()` alongside the GET listing --
        reverting that one line alone would leave the GET honestly
        showing `rag.ingest` as labellable while every POST for it 400s.
        Nothing else exercises the POST path for a page-only tool, so
        this is the one test that would actually fail on that revert."""
        from agents.labels import tool_entitlement_ids
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-tool-entitlements"),
                                   {"tool_key": "rag.ingest", "op": "add",
                                    "add": [str(finance.pk)]})
        assert response.status_code == 302
        assert tool_entitlement_ids()["rag.ingest"] == frozenset({finance.pk})


class TestTheCollapsedRowsAndTheirPanels:
    """PHASE 2. Each resource row is a collapsed `<details>` whose
    summary states what it carries, expanding into the SAME transfer
    panel the entitlement page renders in the other direction. The old
    shape put one checkbox per entitlement on every row -- resources x
    entitlements of them in one render -- which is unreadable long
    before fifty entitlements.
    """

    def _body(self, client, url_name):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            return client.get(reverse(url_name)).content.decode()

    def test_no_element_id_is_rendered_twice(self, client):
        """Both the row card and the panel inside it need an id, and two
        elements cannot share one: `#tool-rag.search` would be ambiguous
        and the save anchor would land on whichever the browser picked.
        Swept over the WHOLE page, so a third id added later is covered
        without an edit here."""
        import re as _re

        make_entitlement(name="Finance")
        body = self._body(client, "chat-tool-entitlements")
        ids = _re.findall(r'\sid="([^"]+)"', body)
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        assert not duplicates, duplicates

    def test_the_summary_counts_and_names_what_the_row_carries(self, client):
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        body = self._body(client, "chat-tool-entitlements")
        assert "2 entitlements: Finance, Legal" in body

    def test_a_long_list_says_how_many_are_not_shown(self, client):
        """COUNT FIRST, THEN THE LEADING NAMES, THEN THE REST -- a
        summary that listed all fifty would be the wall of text the
        collapse exists to remove."""
        for index in range(5):
            ToolEntitlement.objects.create(
                tool_key="rag.search",
                entitlement=make_entitlement(name=f"E{index}"))
        body = self._body(client, "chat-tool-entitlements")
        assert "5 entitlements: E0, E1, E2 +2 more" in body

    def test_an_unlabelled_row_says_so_in_the_pages_own_vocabulary(self, client):
        body = self._body(client, "chat-tool-entitlements")
        assert "No entitlements — everyone signed in" in body

    def test_every_row_is_collapsed_by_default_and_anchored(self, client):
        """COLLAPSED means no `open` attribute anywhere on a plain GET;
        ANCHORED means each row carries a stable id a save can return
        to."""
        make_agent(slug="collapsed-agent")
        body = self._body(client, "chat-agent-entitlements")
        assert "<details>" in body
        assert "<details open>" not in body
        assert 'id="agent-' in body

    def test_the_panel_splits_the_entitlements_into_available_and_active(self, client):
        finance = make_entitlement(name="Finance")
        make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        body = self._body(client, "chat-tool-entitlements")
        start = body.index('id="tool-rag.search"')
        after = body.find('<section class="row" id="tool-', start)
        row = body[start:after] if after != -1 else body[start:]
        assert f'<input type="checkbox" name="remove" value="{finance.pk}">' in row
        assert 'name="add"' in row
        assert "Available —" in row and "Active —" in row

    def test_a_save_returns_to_the_row_with_it_reopened(self, client):
        """A `<details>` closes on every reload, so an anchor alone
        would be half a return -- `?open=` is the row's own state, in
        the URL, with no script and no cookie."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-tool-entitlements"),
                                   {"tool_key": "rag.search", "op": "add",
                                    "add": [str(finance.pk)]})
            assert response["Location"].endswith("#tool-rag.search")
            assert "open=tool-rag.search" in response["Location"]
            reopened = client.get(response["Location"]).content.decode()
        row = reopened[reopened.index('id="tool-rag.search"'):]
        assert row[:400].count("<details open>") == 1

    def test_the_save_redirect_composes_open_with_the_assistant_flag(self, client):
        """FIX ROUND 2, P2-M1. The route-matrix sweep never sees this
        URL: its drivers for both access pages are GETs, so the only
        POST it makes is the card-route leg's EMPTY body, which takes
        the unknown-`tool_key` branch and redirects to the page top --
        never to a row URL with `?open=` on it.

        So the composition is unpinned without this, and round 1's M2
        was the same shape. What it catches, checked rather than
        asserted: replacing `preserve_assistant_flag` with a
        concatenation (`url + "&assistant=1"` on a URL that already
        carries a `#`) buries the flag INSIDE the fragment, where
        `request.GET` never sees it and the panel shuts on every save --
        and every other test in this repository stays green.

        What it does NOT catch, because there is nothing to catch:
        building the fragment after `preserve_assistant_flag` rather
        than before produces the identical bytes, since that function is
        fragment-aware. `entitlement_row_url`'s own docstring used to
        claim that ordering was load-bearing; it is not, and it no
        longer says so.
        """
        from foundation.settings_area import with_assistant_flag

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        url = reverse("chat-tool-entitlements")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            flagged = client.post(f"{url}?assistant=1",
                                  {"tool_key": "rag.search", "op": "add",
                                   "add": [str(finance.pk)]})
            plain = client.post(url, {"tool_key": "rag.search", "op": "remove",
                                      "remove": [str(finance.pk)]})
        assert flagged["Location"] == (
            f"{url}?open=tool-rag.search&assistant=1#tool-rag.search")
        assert plain["Location"] == f"{url}?open=tool-rag.search#tool-rag.search"
        # AND THE EXACT RELATION the sweep asserts for every other
        # settings save, restated here because the sweep cannot reach
        # this URL to assert it.
        assert flagged["Location"] == with_assistant_flag(plain["Location"])

    def test_a_refusal_composes_the_flag_the_same_way(self, client):
        """A refusal returns to the edited row too, so it carries the
        same two parameters -- and it is the path a stale form actually
        takes."""
        admin = make_admin()
        url = reverse("chat-tool-entitlements")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            refused = client.post(f"{url}?assistant=1",
                                  {"tool_key": "rag.search", "op": "add",
                                   "add": ["424242"]})
        assert refused["Location"] == (
            f"{url}?open=tool-rag.search&assistant=1#tool-rag.search")

    def test_the_flash_says_what_really_changed(self, client):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            # Already there: a count of SUBMISSIONS would say "1 added".
            response = client.post(reverse("chat-tool-entitlements"),
                                   {"tool_key": "rag.search", "op": "add",
                                    "add": [str(finance.pk)]}, follow=True)
        assert "rag.search: 0 added, 0 removed." in response.content.decode()

    def test_the_page_carries_the_filter_script_and_the_chromes_guard_and_nothing_else(
            self, client):
        """ONE HOME FOR THE FILTER LOGIC, now with three consumers -- and
        since 2026-09-16 it is no longer the only script here, so the
        name that said so has gone.

        THE SECOND IS NOT THIS PAGE'S. It is the settings chrome's own
        unsaved-input navigation guard, declared once in
        `foundation/templates/_settings.html` and therefore on EVERY
        settings page, whatever that page renders
        (`foundation/tests/test_shell.py::TestTheUnsavedInputGuard` is
        where its own claims are pinned). This page's own budget is
        unchanged and is still what this assertion is about: the count
        below minus the chrome's one.

        BOTH ARE IDENTIFIED, not just counted, so losing either goes red
        rather than being absorbed by a third arriving -- each token
        below appears in that script's BODY and nowhere in the markup.
        """
        body = self._body(client, "chat-tool-entitlements")
        assert body.count("<script>") == 2
        assert "[data-filter-rows][data-filter-scope]" in body   # the filter script
        assert 'window.addEventListener("beforeunload"' in body  # the chrome's guard
        for token in ("localStorage", "sessionStorage", "document.cookie"):
            assert token not in body, token
        # NO DOCUMENT-DELEGATED LISTENER OF THIS PAGE'S OWN. The guard
        # is document-delegated by design -- it is the recorder, and the
        # invariant is about a page script CANCELLING a submit in one,
        # not about delegation itself (`test_shell.py::
        # TestTheGuardsOrderingInvariant`). So this counts rather than
        # forbids: one, and it is the chrome's.
        assert body.count("document.addEventListener") == 1
        assert "<form target=" not in body

    def test_every_panel_form_carries_the_assistant_flag_and_only_when_open(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            opened = client.get(
                reverse("chat-tool-entitlements") + "?assistant=1").content.decode()
            shut = client.get(reverse("chat-tool-entitlements")).content.decode()
        assert f'action="{reverse("chat-tool-entitlements")}?assistant=1"' in opened
        # THE COLLAPSED HALF, byte for byte, over the FORM ACTIONS only:
        # `_shell.html` ships the assistant panel's own CSS on every
        # settings page whether or not the panel is open, so the bare
        # word appears in the document either way. What must not appear
        # is the parameter on a form this page posts.
        assert f'action="{reverse("chat-tool-entitlements")}"' in shut
        # SCOPED TO FORM ACTIONS. The page still carries `?assistant=1`
        # once when the panel is shut -- on the collapsed panel's own
        # link, which is the control that OPENS it.
        import re as _re
        actions = _re.findall(r'<form[^>]*action="([^"]*)"', shut)
        assert actions, "no form actions found -- this half is vacuous"
        assert all("assistant" not in action for action in actions), actions


class TestTheAccessPagesCostAFlatNumberOfQueries:
    """THE SIDEBAR-N+1 LESSON, applied before rather than after. Both
    pages fold one batch read per join table and one
    `labelling_entitlements` over every row, so the query count must not
    move with the number of resources OR the number of entitlements.
    Neither page had such a pin before phase 2.

    NON-VACUOUS BY CONSTRUCTION: the obvious wrong implementation --
    `labels_for(spec.key)` per row, or `labelling_entitlements` inside
    the loop -- costs one query per row, so at twelve rows this equality
    fails by an order of magnitude rather than by a rounding error. The
    body assertions are what stop a page that renders nothing from
    passing it.
    """

    def _count(self, client, url_name):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            body = client.get(reverse(url_name)).content.decode()
        return len(captured), body

    def test_the_tool_page_is_flat_in_the_number_of_entitlements(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.get(reverse("chat-tool-entitlements"))        # warm the session
            make_entitlement(name="Only")
            one, _ = self._count(client, "chat-tool-entitlements")
            for index in range(12):
                make_entitlement(name=f"Many{index}")
            many, body = self._count(client, "chat-tool-entitlements")
        assert one == many, (one, many)
        assert "Many11" in body and "rag.search" in body

    def test_the_agent_page_is_flat_in_rows_and_entitlements(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.get(reverse("chat-agent-entitlements"))       # warm the session
            make_agent(slug="flat-one")
            make_entitlement(name="Only")
            one, _ = self._count(client, "chat-agent-entitlements")
            for index in range(6):
                make_agent(slug=f"flat-{index}")
                make_flow(slug=f"flat-flow-{index}")
                make_entitlement(name=f"Many{index}")
            many, body = self._count(client, "chat-agent-entitlements")
        assert one == many, (one, many)
        assert "flat-flow-5" in body and "Many5" in body
