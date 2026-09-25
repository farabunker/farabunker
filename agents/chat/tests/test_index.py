"""The index page: installing an agent, and starting a thread.

THE CONVERSATION LIST IS NO LONGER HERE. UI-3b moved it into the
sidebar every chat page renders, so what this page still owns is the
start composer, the shipped-default offers and the empty states --
`agents/chat/tests/test_sidebar.py` owns the list. `TestTheReshape`
below is the pin that says the pane really did give the list up rather
than growing a second copy of it.

NO FARABUNKER_FEATURES OVERRIDE (see test_mount.py's docstring).
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    bound_chat_role, fake_turn_queue, make_agent, make_conversation,
)
from agents.models import Agent, Conversation, Share, Turn, WorkstreamTaint
from agents.tests._helpers import _workstream
from identity.access import owner_fields
from identity.testing import grant, make_entitlement, make_user, posture, sign_in, user_principal

pytestmark = pytest.mark.django_db


def _offers_section(body: str) -> str:
    """Just the "not installed yet" block, so a test asserting a slug
    is ABSENT from the offers cannot pass because the slug is also
    absent from the whole page -- or fail because it is present in the
    picker, which is where an installed default is supposed to be."""
    start = body.index('id="offers"')
    return body[start:body.index("</section>", start)]


class TestTheReshape:
    """UI-3b: the pane gave the list up. Every claim about WHICH rows
    are listed, in what order, and with what menu now lives in
    `test_sidebar.py`; these two pins are about this page specifically
    not keeping a second copy."""

    def test_the_pane_no_longer_carries_its_own_thread_list(self, client):
        conversation = make_conversation(title="a thread")
        body = client.get(reverse("chat-index")).content.decode()
        assert "thread-list" not in body
        # The row is still reachable from this page -- once, from the
        # sidebar -- so this is a MOVE, not a removal.
        link = f'href="{reverse("chat-conversation", args=[conversation.id])}"'
        assert body.count(link) == 1
        assert body.index('class="chat-nav"') < body.index(link)

    def test_the_start_composer_is_what_is_left_in_the_pane(self, client):
        """ROUND 18: `class="start-box"` retired along with the page-
        specific start form it named -- `chat/_composer.html` is the
        ONE shared composer now, rendering `class="composer-card"`
        here exactly as it does on the other two surfaces."""
        make_agent(slug="general")
        body = client.get(reverse("chat-index")).content.decode()
        assert f'action="{reverse("chat-start")}"' in body
        assert 'class="composer-card"' in body


class TestEnterToSendOnTheStartBox:
    """Owner feedback round 8, verbatim: "the chat didn't send the
    message when it pressed enter ... it should be sending a message
    when i hit enter or doing a newline when i do shift eneter" -- on
    THIS page's start box, which had no script at all before this fix
    (`chat/conversation.html`'s own Enter-to-send, shipped in 7f99cae,
    never reached here). `chat/_enter_to_send.html` is the shared
    fragment now included below the form; these are template-presence
    pins, the same shape `test_thread.py::TestEnterSubmits` uses for
    the conversation page's own copy -- there is no browser here to
    actually press Enter, so this pins the handler's source rather than
    its runtime effect."""

    def test_the_page_carries_the_shared_keydown_script(self, client):
        """Non-vacuous: removing the include drops this string from the
        page and this fails. `document.addEventListener`, not a
        parse-time `textarea.addEventListener` (final review, m1): the
        handler is now delegated so it survives a composer swap the
        settings assistant panel performs on other pages -- see
        `test_assistant_panel.py::TestTheOneScript::
        test_enter_to_send_is_document_delegated_so_it_survives_the_
        panels_own_swap`."""
        make_agent(slug="general")
        body = client.get(reverse("chat-index")).content.decode()
        assert 'document.addEventListener("keydown"' in body
        assert "form.requestSubmit()" in body

    def test_the_handler_still_leaves_shift_enter_alone(self, client):
        make_agent(slug="general")
        body = client.get(reverse("chat-index")).content.decode()
        assert "event.shiftKey" in body

    def test_the_handler_still_skips_an_ime_composition_enter(self, client):
        make_agent(slug="general")
        body = client.get(reverse("chat-index")).content.decode()
        assert "event.isComposing" in body
        assert "event.keyCode === 229" in body


class TestTheSingleColumnSidebar:
    """Owner feedback, chat layout fix round 2: "workstream needs to be
    stacked over chats... this takes up too much space." Supersedes the
    F1/F3 walk-fix batch's two/three-column squeeze fix (a real stream
    name wrapping one word per line inside an unconstrained flex item) --
    there is no longer a second `--chat-nav-width` column for that bug to
    recur in at all. `_sidebar.html` renders ONE top-level `<nav
    class="chat-nav">` now, Workstreams stacked above Chats inside it as
    a native `<details open>` accordion, so `nav.workstreams` and its own
    paired `.chat-nav-label` flex-basis rule are GONE from `chat/
    base.html`'s CSS, not merely unused."""

    def test_the_two_column_split_rule_is_gone(self, client):
        """Non-vacuous only if this rule really did exist before: it is
        the walk-fix batch's own literal selector, asserted absent here
        rather than merely un-asserted, so a regression that reintroduces
        the second column fails loudly instead of silently."""
        body = client.get(reverse("chat-index")).content.decode()
        assert "nav.workstreams, .chat-nav-label {" not in body
        assert "nav.workstreams h2 .new { white-space: nowrap; }" not in body
        assert "class=\"workstreams\"" not in body

    def test_the_one_nav_column_still_carries_the_fixed_width(self, client):
        body = client.get(reverse("chat-index")).content.decode()
        start = body.index("nav.chat-nav {")
        rule = body[start:body.index("}", start)]
        assert "flex: 0 0 var(--chat-nav-width);" in rule

    def test_the_workstreams_section_is_a_default_open_accordion(self, client):
        """Zero JS: collapse is the native `<details>` toggle, default
        `open` per the owner's words ("show the 5 most recent ones and
        the ability to see more or collapse"). `chat-nav-section` rides
        alongside `chat-workstreams` on the SAME element (grouping round)
        -- the accordion IS one of the two bounded groups, not a third
        thing beside them."""
        body = client.get(reverse("chat-index")).content.decode()
        assert '<details class="chat-workstreams chat-nav-section" open>' in body
        assert "<summary>Workstreams</summary>" in body

    def test_new_workstream_sits_outside_the_summary_not_inside_it(self, client):
        """A `<summary>`'s activation behaviour toggles its `<details>`
        on any click that reaches it, nested `<a>` included, since there
        is no script here to stop that propagation -- so "+ New" must be
        `<summary>`'s SIBLING (positioned onto its row by CSS, never a
        child of it), or clicking it would also flip the accordion."""
        body = client.get(reverse("chat-index")).content.decode()
        summary_start = body.index("<summary>Workstreams</summary>")
        summary_end = body.index("</summary>", summary_start) + len("</summary>")
        assert "+ New" not in body[summary_start:summary_end]
        # Prefix-matched (no closing quote): the element carries a SECOND
        # class now (`chat-nav-section`, the grouping round's bounded
        # card), so the attribute value is no longer exactly
        # `"chat-workstreams"`.
        details_start = body.index('<details class="chat-workstreams')
        details_end = body.index("</details>", details_start)
        # RETARGETED (owner feedback round 7, setup screen): "+ New"
        # points at `chat-workstream-new` now, not `chat-workstreams` --
        # creation moved off the list page entirely onto its own screen.
        assert '<a class="chat-new chat-workstreams-new" href="{}"'.format(
            reverse("chat-workstream-new")) in body[details_start:details_end]
        # Immediately after `</summary>`, not merely present somewhere in
        # the details -- this is what makes it "summary's own row" rather
        # than a row further down the accordion.
        assert body[summary_end:].lstrip().startswith(
            '<a class="chat-new chat-workstreams-new"')

    def test_new_workstream_is_pinned_onto_summarys_row_by_css_not_flex(self, client):
        """Tightening round, point 2: `<summary>` must stay `<details>`'s
        literal first child (or the browser stops treating it as the
        disclosure widget) and cannot itself wrap the link, so "same row"
        is reached by taking the link OUT of flow instead -- `position:
        absolute` on the link, anchored against `position: relative` on
        the `<details>` that contains both of them."""
        body = client.get(reverse("chat-index")).content.decode()
        assert ".chat-workstreams { position: relative; }" in body
        start = body.index(".chat-workstreams-new {")
        rule = body[start:body.index("}", start)]
        assert "position: absolute;" in rule
        assert "top: 0;" in rule
        assert "right: 0;" in rule

    def test_workstreams_summary_and_chats_label_share_one_small_type_scale(
        self, client
    ):
        """Tightening round, point 1: same size/letter-spacing/colour as
        `_shell.html`'s `.nav-group-label` (the "CONVERSATIONS" label) for
        BOTH -- the interactive summary keeps a stronger weight (`700`)
        than the plain `.chat-nav-label` heading (`600`, matching `.nav-
        group-label` exactly), the owner's own allowed exception, never a
        third size or colour anywhere in the rail."""
        body = client.get(reverse("chat-index")).content.decode()

        start = body.index(".chat-workstreams > summary {")
        summary_rule = body[start:body.index("}", start)]
        assert "font-size: 0.7rem;" in summary_rule
        assert "text-transform: uppercase;" in summary_rule
        assert "letter-spacing: 0.09em;" in summary_rule
        assert "color: var(--muted);" in summary_rule
        assert "font-weight: 700;" in summary_rule

        # Anchored to start AFTER `nav.chat-nav {`: the fix round added a
        # SECOND, compound selector ending in the identical text
        # `.chat-nav-label {` (`.chat-nav-section-head .chat-nav-label`,
        # earlier in the file, for the truncation guard) that a bare
        # `body.index(".chat-nav-label {")` would find FIRST instead of
        # this rule -- the same trap `test_the_scrolling_sidebar_lifts_
        # its_own_clip_while_a_menu_is_open` (test_sidebar.py) already
        # avoids for `chat/base.html`'s two `@media (min-width:
        # 1000.01px)` blocks.
        nav_start = body.index("nav.chat-nav {")
        start = body.index(".chat-nav-label {", nav_start)
        label_rule = body[start:body.index("}", start)]
        assert "font-size: 0.7rem;" in label_rule
        assert "text-transform: uppercase;" in label_rule
        assert "letter-spacing: 0.09em;" in label_rule
        assert "color: var(--muted);" in label_rule
        assert "font-weight: 600;" in label_rule
        # `margin: 0` is what stops a bare `<h2>`'s own UA default margin
        # from padding the tightened `nav.chat-nav` gap out further.
        assert "margin: 0;" in label_rule

    def test_the_rail_groups_share_a_tight_gap(self, client):
        """Tightening round, point 4: 0.75-1rem between rail groups, down
        from the first pass's 1.25rem -- one number, `nav.chat-nav`'s own
        `gap`, governs every top-level group at once."""
        body = client.get(reverse("chat-index")).content.decode()
        start = body.index("nav.chat-nav {")
        rule = body[start:body.index("}", start)]
        assert "gap: 0.85rem;" in rule
        assert "gap: 1.25rem;" not in rule

    def test_the_workstream_list_has_no_bullets_or_indent(self, client):
        """Tightening round, point 3: the workstream list is rebuilt on
        `.chat-nav-row`/`.chat-nav-group` -- the CONVERSATION list's own
        row markup -- so there is no `<ul>`/`<li>` left in the accordion
        to carry a disc marker or an indent at all."""
        _workstream(name="Q3 planning")
        body = client.get(reverse("chat-index")).content.decode()
        details_start = body.index('<details class="chat-workstreams')
        details_end = body.index("</details>", details_start)
        region = body[details_start:details_end]
        assert "<ul>" not in region
        assert "<li" not in region
        assert '<div class="chat-nav-row">' in region
        assert "Q3 planning" in region


class TestTheComposerCardFont:
    """Review fix (workstream composition pass, final round): the
    composer's own textarea never set a font, so it fell back to the UA
    default -- `monospace` in every engine this box targets -- which is
    the actual source of the "terminal look" the owner saw on `chat/
    workstream.html`'s own "New chat" card. ROUND 18: the rule is
    `.composer-textarea` now (`chat/base.html`, `chat/_composer.html`'s
    own class, superseding the former `.start-box textarea`), shared by
    all three composer surfaces -- one rule, fixing all three
    symmetrically."""

    def test_the_shared_composer_textarea_inherits_the_page_font(self, client):
        body = client.get(reverse("chat-index")).content.decode()
        start = body.index(".composer-textarea {")
        rule = body[start:body.index("}", start)]
        assert "font: inherit;" in rule


class TestTheTwoBoundedGroups:
    """Grouping round (owner feedback: "can we do a better job at
    visually grouping distinct segments" -- the tightened rail read as
    one undifferentiated run: three near-identical labels with no visual
    containment, "+ New" floating detached at the far right, and "Chats"
    plus "Conversations" double-labelling one list). `_sidebar.html`
    renders EXACTLY TWO bounded `.chat-nav-section` cards, always
    (Workstreams, Chats).

    ROUND 14 (OWNER AMENDMENT) RETIRES THE SCOPED-STREAM VARIANT this
    class used to test a THIRD shape for -- the workstream page's own
    rail is now IDENTICAL to every other chat page's (`_sidebar.html`'s
    own module comment has the full reasoning), so the tests that used
    to pin "exactly one section when scoped", the stream-name header,
    and its own truncation rule are GONE, not merely updated: there is
    no scoped shape left for them to describe. `test_index.py` keeps the
    class name (its own `git log` reads as one continuous story) with
    the round-14-affected tests rewritten in place.
    """

    def test_the_bounded_card_recipe_matches_the_house_family(self, client):
        """Subtle on purpose: the same background/border/radius tokens
        `.tool-card`/`.start-box`/`.banner`/`.chat-menu-body` already use
        on this box, at `.chat-menu-body`'s own compact padding (chosen
        there for this SAME 240px column)."""
        body = client.get(reverse("chat-index")).content.decode()
        start = body.index(".chat-nav-section {")
        rule = body[start:body.index("}", start)]
        assert "background: var(--panel);" in rule
        assert "border: 1px solid var(--border);" in rule
        assert "border-radius: 8px;" in rule
        assert "padding: 0.5rem 0.6rem;" in rule

    def test_chats_header_is_one_row_label_left_action_right(self, client):
        """Point 2: "CHATS ... All chats" on one row. ROUND 14 REPLACES
        THE HEADER'S OWN ACTION: `.chat-new`'s "+ New" moved off this
        row entirely, onto the rail's own single primary "+ New chat"
        row (`.chat-rail-primary`, above Workstreams) -- Part 1's own
        "All chats" affordance takes this header slot instead, pointing
        at `/chat/all/` (round 14, Part 2)."""
        body = client.get(reverse("chat-index")).content.decode()
        head_start = body.index('<div class="chat-nav-section-head">')
        head_end = body.index("</div>", head_start)
        head = body[head_start:head_end]
        assert '<h2 class="chat-nav-label">Chats</h2>' in head
        assert '<a href="{}">All chats</a>'.format(reverse("chat-all")) in head
        assert "chat-new" not in head
        # The rail's OWN primary action, once, not duplicated per page:
        assert '<a href="{}" class="chat-rail-primary">+ New chat</a>'.format(
            reverse("chat-index")) in body
        assert 'aria-label="New workstream"' in body

    def test_the_conversation_sub_label_is_gone(self, client):
        """Point 3: the separate `.nav-group-label` ("Conversations"/
        "Archived") that used to sit INSIDE the rows group is gone --
        the group's own header now does that job, non-vacuously proven
        by an archived conversation's own thread page (below) actually
        reading "Archived" there instead."""
        body = client.get(reverse("chat-index")).content.decode()
        assert '<span class="nav-group-label">' not in body
        assert ">Conversations<" not in body

    def test_chat_index_archived_redirects_to_chat_all(self, client):
        """ROUND 14, PART 2'S OWN ABSORPTION: `/chat/?archived=1` used
        to switch the rail's OWN Chats section to the archive, in place
        -- the only way to see it at all. It is now a redirect to `/chat/
        all/?archived=1`, the new page's own (searchable, filterable)
        archived view; every OTHER query string on `/chat/` is
        untouched."""
        response = client.get(reverse("chat-index") + "?archived=1")
        assert response.status_code == 302
        assert response.url == reverse("chat-all") + "?archived=1"

    def test_a_plain_chat_index_get_is_unaffected_by_the_redirect(self, client):
        response = client.get(reverse("chat-index"))
        assert response.status_code == 200

    def test_the_archived_view_names_itself_in_the_one_header(self, client):
        """The single label carries "Archived", not "Chats", the SAME
        rail state an archived conversation's own thread page renders
        (`agents.chat.views.thread.thread_context`'s own `archived=
        conversation.archived_at is not None`) -- reached without going
        through `/chat/?archived=1` at all now that that query string
        redirects away (the test just above)."""
        from django.utils import timezone

        conversation = make_conversation()
        conversation.archived_at = timezone.now()
        conversation.save(update_fields=["archived_at"])
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        head_start = body.index('<div class="chat-nav-section-head">')
        head_end = body.index("</div>", head_start)
        assert '<h2 class="chat-nav-label">Archived</h2>' in body[head_start:head_end]
        assert ">Chats<" not in body[head_start:head_end]

    @staticmethod
    def _section_count(body):
        """The two EXACT markers a bounded `.chat-nav-section` card opens
        with -- `<div class="chat-nav-section chat-nav-chats">` for the
        Chats card (round 14 adds the second class; see `chat/base.html`'s
        own `.chat-nav-chats` rule), the combined-class `<details>` for
        the Workstreams accordion -- counted separately and summed, never
        a bare `'class="chat-nav-section'` prefix search: that string is
        ALSO a prefix of `.chat-nav-section-head`'s own div, which would
        silently double-count every card's own header row as a second
        card."""
        return (body.count('<div class="chat-nav-section chat-nav-chats">')
                + body.count('chat-workstreams chat-nav-section" open>'))

    def test_exactly_two_sections_render(self, client):
        """ROUND 14: ALWAYS two, on every chat page -- the scoped
        one-section variant this test used to also check for is gone,
        `_sidebar.html`'s own module comment has the full reasoning."""
        body = client.get(reverse("chat-index")).content.decode()
        assert self._section_count(body) == 2
        stream = _workstream(name="Q3 Planning And Budget Review")
        scoped_body = client.get(
            reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert self._section_count(scoped_body) == 2

    def test_the_workstream_page_gets_the_identical_rail(self, client):
        """Part 1's own claim, proven directly: the workstream page's
        own rail renders the SAME global Chats rows as the index -- not
        a stream-filtered list -- with the stream's own conversation
        list living on the stream page's MAIN content instead (`chat/
        workstream.html`'s own `conversations` context key, untouched
        by this round)."""
        owner = make_user()
        agent = make_agent()
        stream = _workstream(user_principal(owner), name="Q3 planning")
        Conversation.objects.create(agent=agent, workstream=stream,
                                    title="inside the stream",
                                    **owner_fields(user_principal(owner)))
        loose = Conversation.objects.create(agent=agent, title="a loose chat",
                                            **owner_fields(user_principal(owner)))
        with posture("enterprise"):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-workstream", args=[stream.pk])).content.decode()
        section_start = body.index('<div class="chat-nav-section chat-nav-chats">')
        section_end = body.index("</nav>", section_start)
        section = body[section_start:section_end]
        assert "inside the stream" in section
        assert "a loose chat" in section
        # The stream's own workstream chip renders on ITS OWN row here.
        assert 'class="chip chip-queued stream-chip"' in section


class TestStartingRecordsTheActingPrincipal:
    def test_a_started_conversation_records_the_acting_principal(self, client):
        """RULING 4b. In open mode that is `Principal("open", "box")` --
        the TRUE statement about a box with no accounts, not a
        placeholder. P3's first draft wrote blanks here; the ruling
        replaces that: a row with no owner is a row a later filter
        cannot reason about, and backfilling one is a migration nobody
        has the information to write."""
        make_agent(slug="general")
        client.post(reverse("chat-start"), {"agent": "general"})
        conversation = Conversation.objects.get()
        assert (conversation.owner_kind, conversation.owner_key) == ("open", "box")


class TestInstalledAgentsAndOffers:
    def test_only_enabled_installed_agents_can_be_talked_to(self, client):
        make_agent(slug="general", name="General assistant")
        make_agent(slug="retired", name="Retired agent", enabled=False)
        body = client.get(reverse("chat-index")).content.decode()
        assert "General assistant" in body
        assert "Retired agent" not in body

    def test_a_fresh_box_offers_the_shipped_defaults_and_writes_nothing(
        self, client
    ):
        """RULING 2, and the sentence that matters: a GET installs
        NOTHING. A fresh box shows an honest empty state naming what
        the platform offers, each with an Add button -- it does not
        quietly create three agents because somebody opened a page.
        """
        response = client.get(reverse("chat-index"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Add the default" in body
        assert "General assistant" in body          # offered, by name
        assert Agent.objects.count() == 0           # and NOT installed

    def test_a_disabled_installed_default_is_neither_offered_nor_picked(self, client):
        """Review fix wave item 2: `offers` used to be computed from
        `visible_agents` (`enabled=True` only), so a DISABLED installed
        default was correctly gone from the picker but WAS RE-OFFERED --
        `missing_defaults` saw its slug as still missing and rendered a
        second "Add the default" button wired to an `install_default`
        that is a permanent no-op (the row already exists). `installed_
        agent_slugs` counts every ROW regardless of `enabled`, so a
        disabled default now stays off the offers list too, and both
        lists correctly show nothing for it."""
        client.post(reverse("chat-default-install"), {"kind": "agent", "slug": "general"})
        agent = Agent.objects.get(slug="general")
        agent.enabled = False
        agent.save()
        body = client.get(reverse("chat-index")).content.decode()
        assert 'value="general"' not in body       # not in the start-form picker
        assert "general" not in _offers_section(body)

    def test_zero_rows_shows_the_nothing_installed_banner_and_the_offers(self, client):
        """Follow-up to review fix wave item 2: with NO `Agent` rows at
        all, `nothing_installed` must key on `installed_agent_slugs`
        being empty (not on `agents`, which would also be empty here for
        the right reason but the wrong test would pass by accident) --
        the offers render underneath it, each with a live Add button."""
        body = client.get(reverse("chat-index")).content.decode()
        assert "No agents are installed on this box yet" in body
        assert "Every installed agent is disabled" not in body
        assert "Add the default" in body
        assert "General assistant" in _offers_section(body)

    def test_every_installed_agent_disabled_shows_a_distinct_banner_no_offers_no_picker(
        self, client
    ):
        """Follow-up to review fix wave item 2. Installing every shipped
        default and then disabling all of them leaves rows on the box
        (so `_NOTHING_INSTALLED`'s "No agents are installed" would be a
        lie) but nothing enabled (so the picker is empty) and nothing
        left to offer (`missing_defaults` sees every slug as already
        installed, disabled or not) -- a THIRD, distinct state from
        either "zero rows" or "some agent talkable-to", naming the
        actionable fix (`--reset <slug>` re-enables, per `agents.
        defaults.install_default`'s own docstring)."""
        from agents.defaults import DEFAULT_AGENTS

        for spec in DEFAULT_AGENTS:
            client.post(reverse("chat-default-install"),
                        {"kind": "agent", "slug": spec.slug})
        for agent in Agent.objects.all():
            agent.enabled = False
            agent.save()

        body = client.get(reverse("chat-index")).content.decode()

        assert "Every installed agent is disabled" in body
        assert "No agents are installed on this box yet" not in body
        assert "Add the default" not in body
        assert 'action="{}"'.format(reverse("chat-start")) not in body
        for spec in DEFAULT_AGENTS:
            assert f'value="{spec.slug}"' not in body   # no picker option

    def test_an_installed_default_stops_being_offered(self, client):
        """The two lists are disjoint by construction
        (`missing_defaults(kind, installed_slugs)`), so a default can
        never appear as both a thing you can talk to and a thing you
        can add."""
        body = client.get(reverse("chat-index")).content.decode()
        assert body.count("Add the default") >= 1
        client.post(reverse("chat-default-install"),
                    {"kind": "agent", "slug": "general"})
        body = client.get(reverse("chat-index")).content.decode()
        assert "general" not in _offers_section(body)

    def test_the_empty_state_names_the_command_too(self, client):
        """The button is the primary path; the CLI equivalent is named
        for an operator who prefers a shell. `sync_agents` is GONE
        (ruling 2) and must not be named anywhere."""
        body = client.get(reverse("chat-index")).content.decode()
        assert "install_defaults" in body
        assert "sync_agents" not in body


class TestInstallingADefault:
    def test_the_button_installs_exactly_one_row_and_redirects(self, client):
        response = client.post(reverse("chat-default-install"),
                               {"kind": "agent", "slug": "general"})
        assert response.status_code == 302
        assert Agent.objects.filter(slug="general").count() == 1

    def test_it_is_owned_by_the_installing_principal(self, client):
        client.post(reverse("chat-default-install"),
                    {"kind": "agent", "slug": "general"})
        agent = Agent.objects.get(slug="general")
        assert (agent.owner_kind, agent.owner_key) == ("open", "box")
        assert agent.resident is True      # origin marker (ruling 3)

    def test_a_double_click_installs_one_row_not_two(self, client):
        """`install_default` is create-if-absent, so the button is safe
        to press twice -- which people do."""
        for _ in range(2):
            client.post(reverse("chat-default-install"),
                        {"kind": "agent", "slug": "general"})
        assert Agent.objects.filter(slug="general").count() == 1

    def test_it_never_overwrites_an_edited_row(self, client):
        """The property ruling 2 exists for. P2's `sync_agents` would
        have reverted this on the next deploy."""
        client.post(reverse("chat-default-install"),
                    {"kind": "agent", "slug": "general"})
        agent = Agent.objects.get(slug="general")
        agent.system_prompt = "my own words"
        agent.save()
        client.post(reverse("chat-default-install"),
                    {"kind": "agent", "slug": "general"})
        agent.refresh_from_db()
        assert agent.system_prompt == "my own words"

    def test_two_concurrent_posts_racing_install_default_never_500(self, client, monkeypatch):
        """Review fix wave item 6: `install_default` is create-if-absent,
        but two concurrent POSTs can both pass its own `.filter().first()
        is None` check before either commits, and the second `INSERT`
        then hits the unique constraint -- `django.db.IntegrityError`,
        not `ValueError`. The view only caught `ValueError`; an
        `IntegrityError` reached here would be an uncaught 500 for a
        request whose desired end state (the row exists) is already
        true."""
        from django.db import IntegrityError

        import agents.chat.views.defaults as module

        def _raise(kind, slug, principal, **kwargs):
            raise IntegrityError("duplicate key value violates unique constraint")

        monkeypatch.setattr(module, "install_default", _raise)
        response = client.post(reverse("chat-default-install"),
                               {"kind": "agent", "slug": "general"})
        assert response.status_code == 302
        assert response["Location"] == reverse("chat-index")

    def test_an_unknown_kind_or_slug_flashes_and_redirects_and_writes_nothing(self, client):
        """UPDATED, ITEM 6 (Coherence Wave D): both refusals used to be
        a raw 400 (a bare plain-text page); they are flash-and-redirect
        now -- the caller is a plain zero-JS `<form>`
        (`chat/_offers.html:21-24`), and `_back(request)` is already
        this view's own redirect target for its two success paths."""
        assert client.post(reverse("chat-default-install"),
                           {"kind": "agent", "slug": "nope"}).status_code == 302
        assert client.post(reverse("chat-default-install"),
                           {"kind": "nonsense", "slug": "general"}).status_code == 302
        assert Agent.objects.count() == 0

    def test_get_is_not_allowed(self, client):
        """A GET must never install. `@require_POST` is the guard, and
        this is the test that says so out loud."""
        assert client.get(reverse("chat-default-install")).status_code == 405

    def test_the_form_carries_a_csrf_token_so_it_works_with_js_off(self, client):
        body = client.get(reverse("chat-index")).content.decode()
        assert "csrfmiddlewaretoken" in body
        assert f'action="{reverse("chat-default-install")}"' in body


class TestStarting:
    def test_it_creates_the_thread_and_redirects_to_it(self, client):
        make_agent(slug="general")
        response = client.post(reverse("chat-start"), {"agent": "general"})
        conversation = Conversation.objects.get()
        assert response.status_code == 302
        assert response["Location"] == reverse(
            "chat-conversation", args=[conversation.id]
        )

    def test_an_unknown_agent_is_refused_without_creating_anything(self, client):
        """UPDATED, ITEM 6 (Coherence Wave D): the refusal was already a
        400 (`conversation_start`'s own docstring: "a bad agent is a 400
        and writes nothing"), but it used to be a bare plain-text page.
        It now re-renders the FULL `chat/index.html` shell with the
        message in its `start_error` banner slot -- the same shape this
        view's OWN `start_turn` refusal (a few lines below in the view)
        already used, so this raw 400 was the one branch that was the
        odd one out on its own page."""
        response = client.post(reverse("chat-start"), {"agent": "nope"})
        assert response.status_code == 400
        assert '<div class="banner warn">' in response.content.decode()
        assert "Pick an agent that exists" in response.content.decode()
        assert Conversation.objects.count() == 0

    def test_a_disabled_agent_is_refused(self, client):
        """A disabled agent is one the operator turned off. Its past
        conversations stay readable; new ones do not start.
        `visible_agents` applies `enabled=True` for exactly this
        reason, so the picker and this refusal cannot disagree."""
        make_agent(slug="retired", enabled=False)
        response = client.post(reverse("chat-start"), {"agent": "retired"})
        assert response.status_code == 400
        assert Conversation.objects.count() == 0

    def test_get_is_not_allowed(self, client):
        assert client.get(reverse("chat-start")).status_code == 405

    def test_the_picked_connection_is_carried_into_the_thread_url(self, client):
        """Deviation P3-D7: the pick lives in the query string, not in
        a session and not in a column -- `tools/vision/views.py::
        _create_url`'s own rule, so a link is a complete description of
        what the page will show."""
        make_agent(slug="general")
        response = client.post(reverse("chat-start"),
                               {"agent": "general", "connection": "3"})
        assert response["Location"].endswith("?connection=3")


class TestStartingIntoAWorkstream:
    """Spec §14: `chat-start`'s `workstream` field must be in
    `visible_workstreams` AND pass `stream_access`, else 400 -- gate two
    is not only the stream page's own 403, it is also the one thing that
    stops a dormant recipient from opening a fresh conversation in a
    stream whose tags have outrun their grants (spec §14's route table,
    review finding 1 on this task)."""

    def test_a_dormant_recipient_may_not_start_a_conversation_in_the_stream(self, client):
        owner, reader = make_user(), make_user()
        ent = make_entitlement(name="Finance")
        stream = _workstream(**owner_fields(user_principal(owner)))
        WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
        Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                             user=reader, level=Share.Level.USE)
        # `resident=True, box_wide=True` (task 5, chat cluster feature
        # B), so the agent is visible to a non-owning, non-admin
        # recipient (`visible_agents`'s own box-wide-or-owned-or-shared
        # clause) -- the point of this test is gate two's refusal, not
        # the unrelated "unknown agent" one.
        make_agent(slug="general", resident=True, box_wide=True)
        with posture("enterprise"):
            sign_in(client, reader)
            response = client.post(reverse("chat-start"),
                                   {"agent": "general", "workstream": str(stream.pk)})
        assert response.status_code == 400
        # UPDATED, ITEM 6 (Coherence Wave D): see `TestStarting::
        # test_an_unknown_agent_is_refused_without_creating_anything`
        # for the reasoning -- the same shelled re-render, not a raw
        # 400, for this view's other caller-error refusal.
        assert '<div class="banner warn">' in response.content.decode()
        assert "not available" in response.content.decode()
        assert Conversation.objects.count() == 0

    def test_a_live_grant_recipient_may_still_start_a_conversation_in_the_stream(self, client):
        """THE POSITIVE CASE, asserted beside the negative one -- a
        recipient whose grants still cover the stream's tags is
        unaffected by gate two, exactly as owner decision 7 promises."""
        owner, reader = make_user(), make_user()
        ent = make_entitlement(name="Finance")
        grant(ent, user=reader)
        stream = _workstream(**owner_fields(user_principal(owner)))
        WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
        Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                             user=reader, level=Share.Level.USE)
        make_agent(slug="general", resident=True, box_wide=True)
        with posture("enterprise"):
            sign_in(client, reader)
            response = client.post(reverse("chat-start"),
                                   {"agent": "general", "workstream": str(stream.pk)})
        assert response.status_code == 302
        conversation = Conversation.objects.get()
        assert conversation.workstream_id == stream.pk

    def test_the_owner_is_unaffected_by_tags_they_do_not_personally_hold(self, client):
        """THE OWNER BRANCH of `stream_access` -- no tag check at all
        (ruling B's consequence: a recipient's own turn can taint the
        owner's stream, so the owner may hold no grant for a tag on
        their own stream, and starting a conversation there must not be
        refused by it)."""
        owner = make_user()
        ent = make_entitlement(name="Finance")
        stream = _workstream(**owner_fields(user_principal(owner)))
        WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
        make_agent(slug="general", resident=True, box_wide=True)
        with posture("enterprise"):
            sign_in(client, owner)
            response = client.post(reverse("chat-start"),
                                   {"agent": "general", "workstream": str(stream.pk)})
        assert response.status_code == 302
        assert Conversation.objects.get().workstream_id == stream.pk


class TestStartingWithAMessage:
    """Task 9: `conversation_start`'s second half -- a non-blank `text`
    posts the conversation's first turn through the same `start_turn`
    the thread's own message form uses."""

    def test_a_message_posts_the_first_turn_and_redirects_with_pending(
            self, client, bound_chat_role, fake_turn_queue):
        make_agent(slug="general")
        response = client.post(
            reverse("chat-start"), {"agent": "general", "text": "hello there"},
        )
        conversation = Conversation.objects.get()
        placeholder = Turn.objects.get(role=Turn.Role.ASSISTANT)
        assert response.status_code == 302
        assert response["Location"] == (
            f"{reverse('chat-conversation', args=[conversation.id])}?pending={placeholder.pk}"
        )
        assert Turn.objects.filter(role=Turn.Role.USER, text="hello there").exists()

    def test_a_blank_message_still_starts_an_empty_thread(self, client):
        """Unchanged from before Task 9: the index's own form always
        posts a `text` field, but a bare "start a conversation" with
        nothing typed is not an error."""
        make_agent(slug="general")
        response = client.post(reverse("chat-start"), {"agent": "general", "text": "   "})
        assert response.status_code == 302
        assert Turn.objects.count() == 0

    def test_a_refused_start_deletes_the_empty_conversation_and_shows_the_banner(
            self, client):
        """A refused start must never leave an empty thread sitting in
        the conversation list -- the operator never sees a thread they
        never got an answer in."""
        make_agent(slug="general", llm_role="nothing.bound.here")
        response = client.post(
            reverse("chat-start"), {"agent": "general", "text": "hello there"},
        )
        assert response.status_code == 503
        assert "nothing.bound.here" in response.content.decode()
        assert Conversation.objects.count() == 0
        assert Turn.objects.count() == 0


class TestTheStartBoxBlurbNamesItsAgent:
    """C-02. `{{ agents.0.description }}` sat under a picker
    (`chat/_composer.html`) listing every visible agent, on a page with
    no JavaScript to follow the `<select>`'s selection -- correct only
    while exactly one agent was installed. `agents.0` is ordered by
    `Agent.Meta.ordering = ["slug"]`, the same ordering `visible_agents`
    hands the template and the picker both iterate over, so it is also
    the option the browser renders selected by default."""

    def test_the_start_box_blurb_names_the_agent_it_describes(self, client):
        """`Researcher`'s slug sorts before `Drafter`'s, so `Researcher`
        is `agents.0` -- the row both the blurb and the picker's default
        selection describe. `Drafter`'s description must not appear at
        all: this pins the blurb to naming the FIRST agent, not just
        naming AN agent."""
        first = make_agent(slug="a-researcher", name="Researcher", description="Finds things.")
        make_agent(slug="b-drafter", name="Drafter", description="Writes things.")
        body = client.get(reverse("chat-index")).content.decode()
        assert "Finds things." in body
        assert first.name in body.split("Finds things.")[0][-120:]
        assert "Writes things." not in body
