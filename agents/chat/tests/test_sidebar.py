"""The conversation sidebar, on both chat pages (UI-3b).

WHAT THIS MODULE PINS is the sidebar as a RENDER: which rows are in it,
which one is marked current, when the cap says so out loud, when the
Archived link exists at all, and which rows get a menu. The four
actions the menu POSTS to are `test_conversation_actions.py`'s
subject; the two files meet at exactly one claim -- a row nobody may
manage renders no menu -- which is the render-vs-gate rule, and it is
asserted from both sides on purpose.

NO `FARABUNKER_FEATURES` OVERRIDE (see `test_mount.py`'s own docstring)
-- every test here does an HTTP request or a `reverse()`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from agents.chat.sidebar import SIDEBAR_LIMIT, WORKSTREAM_SIDEBAR_LIMIT, sidebar_context
from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    grant, make_agent, make_conversation, make_entitlement, make_thread, make_turn, make_user,
    posture, reset_settings, sign_in, user_principal,
)
from agents.models import Conversation, Share, WorkstreamTaint
from agents.tests._helpers import _workstream
from agents.visibility import create_conversation
from identity.access import owner_fields, settings_row
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.models import IdentitySettings

pytestmark = pytest.mark.django_db


# ROUND 14 (app rail): `aria-label="Conversations"` -> `"Chat"` -- the
# rail now carries the app's own navigation, the Workstreams section,
# and the account block too, not only the conversation list, so the
# broader label is the honest one.
_NAV = re.compile(r'<nav class="chat-nav" aria-label="Chat">.*?</nav>', re.S)


def _index(client, *, archived=False):
    url = reverse("chat-index") + ("?archived=1" if archived else "")
    return client.get(url).content.decode()


def _nav(body: str) -> str:
    """JUST THE SIDEBAR, not the page it sits on.

    Sliced for the reason `foundation/tests/test_shell.py::_bar` slices
    the app bar: an assertion about what the sidebar does NOT contain
    must not be answerable by the rest of the document. Specifically,
    the thread page carries a pre-existing inline `onclick` of its own
    (its delete-confirm Cancel button, which predates this surface), so
    an unsliced `"onclick" not in body` could only ever run on the
    index -- which would leave the "the rail's own markup carries no
    inline handler and no `<script>`" claim pinned on one of the two
    pages that render it.

    THAT IS THE CLAIM, and it is narrower than the one this helper was
    written for. Since round 21 the FRAGMENT does include a script
    (`chat/_menu_exclusive.html`), but it rides OUTSIDE the `</nav>`
    this slice ends at, which is what keeps the assertions below true
    and worth making: the rail's markup is handler-free, and the one
    enhancement is separable from it.
    """
    match = _NAV.search(body)
    assert match is not None, "the page rendered no conversation sidebar at all"
    return match.group(0)


class TestItIsOnBothPages:
    """The whole point of the reshape: the list travels with you, so
    clicking between conversations never returns to a landing screen."""

    def test_the_index_renders_it(self, client):
        make_conversation(title="a thread")
        body = _index(client)
        assert 'class="chat-nav"' in body
        assert "a thread" in body

    def test_a_thread_page_renders_it_too(self, client):
        agent = make_agent(slug="general")
        here = make_conversation(agent=agent, title="the one I am reading")
        elsewhere = make_conversation(agent=agent, title="another thread")
        body = client.get(
            reverse("chat-conversation", args=[here.id])).content.decode()
        assert 'class="chat-nav"' in body
        assert "another thread" in body
        assert reverse("chat-conversation", args=[elsewhere.id]) in body

    def test_the_new_chat_link_points_at_the_index(self, client):
        """ROUND 14: the rail's ONE "+ New chat" action is now the
        primary row above Workstreams (`.chat-rail-primary`), not a
        `.chat-new` link on the Chats header (that slot is "All chats"
        now, `test_index.py::TestTheTwoBoundedGroups` covers it)."""
        body = _index(client)
        assert f'href="{reverse("chat-index")}" class="chat-rail-primary"' in body


class TestTheList:
    def test_it_lists_every_conversation_this_principal_may_see(self, client):
        """PRE-AUTH BEHAVIOUR, unchanged by the reshape: there are no
        accounts on an open box, so there is no owner filter. The rows
        moved from the pane to the sidebar; which rows they are did
        not."""
        agent = make_agent(slug="general")
        make_conversation(agent=agent, title="mine")
        make_conversation(agent=agent, title="theirs",
                          owner_kind="user", owner_key="someone")
        body = _index(client)
        assert "mine" in body and "theirs" in body

    def test_newest_first(self, client):
        agent = make_agent(slug="general")
        make_conversation(agent=agent, title="older")
        make_conversation(agent=agent, title="newer")
        body = _index(client)
        assert body.index("newer") < body.index("older")

    def test_an_empty_box_says_so_rather_than_rendering_nothing(self, client):
        assert "No conversations yet" in _index(client)

    def test_the_current_conversation_is_marked(self, client):
        agent = make_agent(slug="general")
        here = make_conversation(agent=agent, title="reading this")
        make_conversation(agent=agent, title="not this one")
        body = client.get(
            reverse("chat-conversation", args=[here.id])).content.decode()
        # The marked ROW, not the marked page: everything from the
        # `current` class to the end of that row's own link.
        start = body.index('class="chat-nav-row current"')
        row = body[start:body.index("</a>", start)]
        assert "reading this" in row
        assert "not this one" not in row

    def test_the_index_marks_nothing_current(self, client):
        make_conversation(title="a thread")
        assert "chat-nav-row current" not in _index(client)

    def test_the_full_title_is_on_the_link_for_hover(self, client):
        """ROUND 2, item 1 (owner feedback): a sidebar entry is one
        ellipsized line, so hovering it must show the whole thing.

        Already true before this round -- the `title=` attribute shipped
        with the sidebar in UI-3b -- and UNPINNED, which is the actual
        gap this closes. Zero JS: a native tooltip, escaped by the
        template's own autoescaping like any other attribute value.

        The CURRENT entry needs no separate case: this sidebar keeps its
        current row as a real `<a>` (it deliberately does not take the
        settings nav's `pointer-events: none`), so there is no
        non-anchor span for a title to be missing from -- asserted here
        rather than assumed.
        """
        long_title = (
            "a genuinely long conversation title that no 240px column "
            "could ever show in full without cutting it off somewhere"
        )
        conversation = make_conversation(title=long_title)
        nav = _nav(client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode())

        # On the link, carrying the WHOLE stored title -- not the
        # visually truncated form, which is what CSS is doing to it.
        assert f'title="{long_title}"' in nav
        # And the current row really is still an anchor, so that one
        # attribute covers both states.
        assert 'class="chat-nav-row current"' in nav
        assert f'href="{reverse("chat-conversation", args=[conversation.id])}"' in nav

    def test_an_untitled_row_hovers_to_the_same_words_it_shows(self, client):
        """An empty `title=""` would be a tooltip that flashes nothing.
        The fallback the link TEXT uses is the fallback the attribute
        uses."""
        make_conversation()
        assert 'title="Untitled conversation"' in _nav(_index(client))

    def test_a_title_is_never_re_truncated_in_python(self, client):
        """`Conversation.title` is already word-boundary-truncated at
        write time; the sidebar's one-line entry is CSS
        (`text-overflow`), so the full title is in the markup and the
        `title=` tooltip can show it."""
        long_title = "a genuinely long conversation title that would not fit a column"
        make_conversation(title=long_title)
        assert long_title in _index(client)


class TestTheCap:
    def test_the_cap_is_said_out_loud_when_it_bites(self, client):
        agent = make_agent(slug="general")
        for n in range(SIDEBAR_LIMIT + 3):
            make_conversation(agent=agent, title=f"thread {n}")
        body = _index(client)
        assert "3 older conversations" in body

    def test_one_over_the_cap_reads_as_one_conversation_not_one_conversations(
        self, client,
    ):
        agent = make_agent(slug="general")
        for n in range(SIDEBAR_LIMIT + 1):
            make_conversation(agent=agent, title=f"thread {n}")
        assert "1 older conversation<" in _index(client)

    def test_a_list_inside_the_cap_says_nothing_about_older_ones(self, client):
        make_conversation(title="the only one")
        assert "older conversation" not in _index(client)


class TestThePinnedSection:
    """ROUND 20 (owner, verbatim: "can we add the ability t[o] pin
    chats so it's always shown in its own se[c]tion ibetween
    Workstreams and chats. It shoudl only appear if we have somethign
    that is pinned, but it should hide otherwise."). `TestTheRailOrder::
    test_the_pinned_section_lands_between_workstreams_and_chats_when_
    present` pins WHERE; this class pins WHETHER it renders at all, and
    what pinning does to the ordinary Chats list beside it.
    """

    def test_absent_with_no_pinned_conversations(self, client):
        """THE OTHER DIRECTION of the presence pin -- pinned or not,
        the section itself must never render as an empty shell."""
        make_conversation(title="just an ordinary chat")
        body = _index(client)
        assert 'class="chat-nav-section chat-nav-pinned"' not in body
        assert ">Pinned<" not in body

    def test_present_with_at_least_one_pinned_conversation(self, client):
        make_conversation(title="bookmarked", pinned_at=timezone.now())
        body = _index(client)
        assert 'class="chat-nav-section chat-nav-pinned"' in body
        assert ">Pinned<" in body
        assert "bookmarked" in body

    def test_the_pinned_heading_carries_an_icon_only_explainer_on_the_header_row(self, client):
        """ROUND-20-CONFIRM R8 gave this heading an explainer, because
        the rail's "Pinned" (conversations) and the Documents panel's
        "Pinned" (documents, `rag/panels/documents.html`) name two
        unrelated things with the same word on the same stream page.

        ROUND 22 CHANGES ITS SHAPE, not its existence (owner, verbatim:
        "can we make it on the same line as pinned ... The symbol should
        be sufficient rather than the text 'what is this'"). It is now a
        `.chat-info` control INSIDE `.chat-nav-section-head` -- the
        header row -- carrying the glyph alone. The words did not
        vanish: they moved into the accessible name and the hover
        title, which is the whole trade (a screen reader and a hovering
        mouse still get a sentence; a 240px rail stops paying a line
        for it)."""
        make_conversation(title="bookmarked", pinned_at=timezone.now())
        body = _index(client)
        section_start = body.index('class="chat-nav-section chat-nav-pinned"')
        section_end = body.index('class="chat-nav-section chat-nav-chats"', section_start)
        section = body[section_start:section_end]
        head_start = section.index('class="chat-nav-section-head"')
        control = section.index('class="chat-menu chat-info"', head_start)
        # ON THE HEADER ROW, and this is what actually pins it (round-22
        # confirm, MINOR 3): a first cut sliced to `class="chat-nav-
        # group"`, which spans the header row AND the gap beneath it --
        # exactly where the old disclosure lived -- so a regression that
        # put the explainer back below the header would have satisfied
        # every assertion under a comment saying it could not. The
        # header row is ONE `<div>`, so the control is inside it if and
        # only if that div has not closed between the heading and the
        # control.
        assert "</div>" not in section[section.index("</h2>", head_start):control]
        panel = section.index('class="chat-menu-body"', control)
        assert "&#9432;" in section[control:panel]     # the glyph IS the trigger
        # THE TEXT IS GONE FROM THE SCREEN...
        assert "What's this?" not in section
        # ...AND STILL THERE FOR A SCREEN READER AND A HOVER.
        head = section[head_start:section.index('class="chat-nav-group"', head_start)]
        assert 'aria-label="What does Pinned mean, here?"' in head
        assert 'title="What does Pinned mean, here?"' in head
        assert "pinned" in section.lower()

    def test_the_explainer_is_an_overlay_not_an_inline_disclosure(self, client):
        """The owner's other half: "make it as a pop up info line when
        clicked rather than an inline modification". `.chat-menu-body`
        IS the overlay (`position: absolute; z-index: 5`), so opening it
        shifts no row -- the same thing the `⋯` menus do, reused rather
        than reinvented.

        WHAT THIS ASSERTS IS THE OVERLAY, and only that. It used to also
        carry a byte-exact match on `.chat-info > .chat-menu-body`'s own
        `min-width: calc(...)` source text -- a claim about PANEL WIDTH
        under a test named "is an overlay", already owned in full by the
        sibling `test_the_panel_is_the_cards_content_width_not_the_
        rails` below, and breakable by one reflow of a rule in a file
        whose CSS is rewrapped most rounds. Two tests, one claim each."""
        make_conversation(title="bookmarked", pinned_at=timezone.now())
        body = _index(client)
        section_start = body.index('class="chat-nav-section chat-nav-pinned"')
        section_end = body.index('class="chat-nav-section chat-nav-chats"', section_start)
        section = body[section_start:section_end]
        assert 'class="chat-menu-body"' in section
        # The overlay itself: the panel is out of flow and paints above
        # the rows, which is what "not an inline disclosure" means. The
        # leading newline anchors this to the BASE rule rather than to
        # `.chat-info > .chat-menu-body`'s own width override.
        rule_start = body.index("\n  .chat-menu-body {")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "position: absolute" in rule
        assert "z-index: 5" in rule

    def test_the_panel_is_the_cards_content_width_not_the_rails(self, client):
        """ROUND-22 CONFIRM, MINOR 2. `right: 0` anchors the panel to the
        `<details>`, whose right edge is `.chat-nav-section`'s CONTENT
        edge -- inset by that card's own padding and border. A panel at
        the full `--chat-nav-width` therefore overhung the PINNED card's
        left border by ~10.6px, which is cosmetic but is not what the
        comment claimed. Subtracting both paddings (0.6rem each) and
        both borders (1px each) is what makes "lands inside the card"
        true, so the derivation is pinned rather than left as a number
        somebody could round off.

        BOUND TO ITS SELECTOR, not merely present on the page (audit-2
        confirm, finding 6). This test used to assert the `calc(...)`
        substring anywhere in the body, which F13 then made the ONLY
        pin on that value once it dropped the sibling's byte-exact rule
        match -- and a value that floats free of its selector can be
        moved onto a different rule with the suite still green. Sliced
        from `.chat-info > .chat-menu-body` to the end of its
        declaration block, so the claim is "THIS rule carries that
        width", which is what the derivation above is about."""
        body = _index(client)
        rule_start = body.index(".chat-info > .chat-menu-body")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "min-width: calc(var(--chat-nav-width) - 1.2rem - 2px)" in rule

    def test_the_icon_carries_a_real_hit_target(self, client):
        """ROUND-22 CONFIRM, MINOR 1 -- an a11y regression, not a polish
        item. The first cut's `padding: 0 0.15rem` OVERRODE `.chat-menu
        > summary`'s own `0 0.25rem` with less, shipping a ~20x15px
        target: smaller than the row menus it borrows from, under WCAG
        2.2 AA 2.5.8's 24x24, and smaller than the full text line
        ("What's this?") this round replaced. `min-width`/`min-height`
        state the requirement directly instead of leaving it to be
        re-derived from a font size and two paddings."""
        body = _index(client)
        start = body.index(".chat-info > summary {")
        rule = body[start:body.index("}", start)]
        assert "min-width: 24px;" in rule
        assert "min-height: 24px;" in rule
        # inline-flex + centring is what keeps the glyph in the middle
        # once the box is bigger than the glyph.
        assert "display: inline-flex;" in rule

    def test_the_explainer_joins_the_row_menus_dismissal_behaviour(self, client):
        """NO CARVE-OUT, and this is what pins that claim: the round-21
        script selects `details.chat-menu` inside `nav.chat-nav`, and
        this control carries that class -- so outside-click, Escape and
        one-open-at-a-time all reach it without the script naming it.
        If a future edit gives the explainer its own class, this fails
        rather than the behaviour silently regressing."""
        make_conversation(title="bookmarked", pinned_at=timezone.now())
        body = _index(client)
        nav = _nav(body)
        assert 'class="chat-menu chat-info"' in nav
        assert 'querySelectorAll("details.chat-menu[open]")' in body

    def test_the_documents_panels_own_explainers_are_left_alone(self):
        """THE SCOPE FENCE (round 22), asserted rather than promised: the
        owner's report was about the RAIL card, where a wasted line
        costs a fifth of a 240px column. `rag/panels/documents.html`'s
        Contained/Pinned pair keeps the round-17 inline `.ws-info`
        disclosure, text and all, and `.ws-info`'s own rule stays in
        `chat/base.html` because that pair is still its consumer."""
        panel = (Path(__file__).resolve().parents[3]
                 / "tools" / "rag" / "templates" / "rag" / "panels" / "documents.html"
                 ).read_text()
        assert panel.count('class="ws-info"') == 2
        assert panel.count("What's this?") == 2
        base = (Path(__file__).resolve().parents[1]
                / "templates" / "chat" / "base.html").read_text()
        assert ".ws-info summary {" in base

    def test_the_chats_empty_state_is_honest_when_everything_is_pinned(self, client):
        """ROUND-20-CONFIRM R6: "No conversations yet." was false the
        moment the ONLY conversation this principal has is pinned -- it
        is shown, one card up, in Pinned. `chat/_sidebar.html`'s own
        `{% templatetag openblock %} empty {% templatetag closeblock %}`
        branch now reads the pinned set before claiming there is
        nothing at all."""
        make_conversation(title="only chat", pinned_at=timezone.now())
        body = _index(client)
        assert "No conversations yet." not in body
        assert "Nothing unpinned." in body

    def test_the_pinned_section_is_bounded_so_it_cannot_collapse_chats(self, client):
        """ROUND-20-CONFIRM R7 (real defect): an uncapped Pinned section
        with no `flex-shrink: 0` and no height bound of its own could
        squeeze `.chat-nav-chats` to zero and spill past `nav.chat-nav`
        with no scrollbar anywhere. Paint cannot be unit-tested, but the
        declarations can -- the same shape `TestTheMenu::test_the_
        scrolling_sidebar_lifts_its_own_clip_while_a_menu_is_open`
        already uses for this file's own CSS pins."""
        make_conversation(title="bookmarked", pinned_at=timezone.now())
        body = _index(client)

        start = body.index(".chat-nav-pinned {")
        rule = body[start:body.index("}", start)]
        assert "flex-shrink: 0;" in rule

        start = body.index(".chat-nav-pinned .chat-nav-group {")
        rule = body[start:body.index("}", start)]
        assert "max-height:" in rule
        assert "overflow-y: auto;" in rule

        # Sub-1000px reset -- the SAME layout, without the flex column
        # this bound exists to guard, must not gain an unwanted second
        # scrollbar.
        media_start = body.rindex("@media (max-width: 1000px)", 0,
                                  body.index(".chat-nav-pinned .chat-nav-group { max-height: none"))
        reset_start = body.index(".chat-nav-pinned .chat-nav-group { max-height: none",
                                 media_start)
        reset_rule = body[reset_start:body.index("}", reset_start)]
        assert "max-height: none;" in reset_rule
        assert "overflow-y: visible;" in reset_rule

    def test_a_pinned_conversation_is_excluded_from_the_chats_recents(self, client):
        """NO DUPLICATES: a pinned row appears exactly once on the
        page -- in Pinned, never also in Chats."""
        agent = make_agent()
        make_conversation(agent=agent, title="bookmarked", pinned_at=timezone.now())
        make_conversation(agent=agent, title="ordinary")
        body = _index(client)
        pinned_section = body[body.index('class="chat-nav-section chat-nav-pinned"'):
                              body.index('class="chat-nav-section chat-nav-chats"')]
        chats_section = body[body.index('class="chat-nav-section chat-nav-chats"'):]
        assert "bookmarked" in pinned_section
        assert "bookmarked" not in chats_section
        assert "ordinary" in chats_section

    def test_unpinning_returns_a_conversation_to_the_chats_recents(self, client):
        """THE RETURN TRIP: the same conversation, read from `Chats`
        alone once its own pin is cleared -- proven through the real
        toggle route, not by constructing the row pre-unpinned."""
        conversation = make_conversation(title="bookmarked", pinned_at=timezone.now())
        client.post(reverse("chat-conversation-unpin", args=[conversation.pk]))
        body = _index(client)
        assert 'class="chat-nav-section chat-nav-pinned"' not in body
        chats_section = body[body.index('class="chat-nav-section chat-nav-chats"'):]
        assert "bookmarked" in chats_section

    def test_archiving_a_pinned_conversation_removes_it_from_both_lists(self, client):
        """ARCHIVE WINS (the brief's own rule, stated for the render
        side -- `TestPinning::test_archiving_a_pinned_conversation_
        leaves_the_pin_column_standing`, `test_conversation_actions.py`,
        pins the COLUMN side of the identical claim): once archived, a
        pinned conversation is gone from Pinned AND from Chats, the
        same as any other archived row."""
        conversation = make_conversation(title="bookmarked", pinned_at=timezone.now())
        client.post(reverse("chat-conversation-archive", args=[conversation.pk]))
        body = _index(client)
        assert 'class="chat-nav-section chat-nav-pinned"' not in body
        assert "bookmarked" not in body

    def test_unarchiving_restores_the_pin_exactly_where_archiving_found_it(self, client):
        """The other half of "archive wins": unarchiving needs no
        second click to re-pin, because archiving never touched
        `pinned_at` in the first place."""
        conversation = make_conversation(title="bookmarked", pinned_at=timezone.now())
        client.post(reverse("chat-conversation-archive", args=[conversation.pk]))
        client.post(reverse("chat-conversation-unarchive", args=[conversation.pk]))
        body = _index(client)
        pinned_section = body[body.index('class="chat-nav-section chat-nav-pinned"'):
                              body.index('class="chat-nav-section chat-nav-chats"')]
        assert "bookmarked" in pinned_section

    def test_pinned_rows_order_most_recently_pinned_first(self, client):
        """Ordering: `pinned_at` DESCENDING -- the brief's own words,
        "simple, documented" -- proven with two rows pinned through the
        real toggle route in a known order, not constructed with
        hand-set timestamps that could hide a wrong `order_by()`."""
        agent = make_agent()
        older = make_conversation(agent=agent, title="pinned first")
        newer = make_conversation(agent=agent, title="pinned second")
        client.post(reverse("chat-conversation-pin", args=[older.pk]))
        client.post(reverse("chat-conversation-pin", args=[newer.pk]))
        body = _index(client)
        assert body.index("pinned second") < body.index("pinned first")


class TestTheArchive:
    """ROUND 14, PART 2 RETIRES `/chat/?archived=1` AS A WAY TO BROWSE
    THE ARCHIVE (it now redirects to `/chat/all/?archived=1`,
    `test_index.py::TestTheTwoBoundedGroups::
    test_chat_index_archived_redirects_to_chat_all` pins the redirect
    itself) -- so every test here that used to reach the rail's own
    "Archived" state THROUGH THAT QUERY STRING now reaches it the other
    way it was always ALSO reachable: viewing an archived conversation's
    own thread page directly (`agents.chat.views.thread.thread_context`'s
    `archived=conversation.archived_at is not None`, untouched by this
    round). Two tests that were fundamentally about the LIST PAGE's own
    behaviour with NO conversation selected (an empty archive's own
    message, which archived rows appear on that LIST) have no thread
    page to reach at all any more -- those move to `agents/chat/tests/
    test_all_conversations.py`, the new page's own file, rather than
    being retired outright.
    """

    def test_an_archived_conversation_leaves_the_default_sidebar(self, client):
        agent = make_agent(slug="general")
        make_conversation(agent=agent, title="still active")
        make_conversation(agent=agent, title="put away",
                          archived_at=timezone.now())
        body = _index(client)
        assert "still active" in body
        assert "put away" not in body

    def test_the_archived_link_appears_only_when_there_is_an_archive(self, client):
        agent = make_agent(slug="general")
        make_conversation(agent=agent, title="active")
        assert "Archived (" not in _index(client)

        make_conversation(agent=agent, title="put away",
                          archived_at=timezone.now())
        assert "Archived (1)" in _index(client)

    def test_the_archived_link_points_at_chat_all(self, client):
        """ROUND 14: the foot link used to point at `/chat/?archived=1`
        (which would then redirect); it now names the destination
        directly."""
        make_conversation(archived_at=timezone.now())
        assert f'href="{reverse("chat-all")}?archived=1"' in _index(client)

    def test_the_archive_offers_unarchive_and_delete_only(self, client):
        conversation = make_conversation(title="put away",
                                         archived_at=timezone.now())
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert reverse("chat-conversation-unarchive",
                       args=[conversation.id]) in body
        assert reverse("chat-conversation-delete", args=[conversation.id]) in body
        assert reverse("chat-conversation-archive", args=[conversation.id]) not in body

    def test_the_active_list_offers_archive_not_unarchive(self, client):
        conversation = make_conversation(title="active")
        body = _index(client)
        assert reverse("chat-conversation-archive", args=[conversation.id]) in body
        assert reverse("chat-conversation-unarchive",
                       args=[conversation.id]) not in body

    def test_chat_index_keeps_one_name_regardless_of_the_query_string(self, client):
        """ROUND 14 TRUTH-SWEEP: `chat/index.html` used to carry a
        SECOND `<h1>`/`<title>` for `?archived=1` (fix round 1, review
        Minor 1's own "two names for one page" fix) -- dead now that the
        query string redirects before this template ever renders
        (`chat/index.html`'s own comment has the full reasoning). One
        name, always, proven against a query string that predates this
        round (`?archived=0`, still the active list) so this is not
        merely "no conversations exist to be ambiguous about"."""
        make_conversation(title="active")
        body = client.get(reverse("chat-index") + "?archived=0").content.decode()
        assert "<title>Chat — farabunker</title>" in body
        assert "<h1>Chat</h1>" in body
        assert "Archived conversations" not in body

    def test_reading_an_archived_thread_shows_the_archive_sidebar(self, client):
        """The page you are on must be a row the sidebar can mark. An
        archived thread beside the ACTIVE list would be the one entry
        that could never be current. Also the ONE place this file still
        proves "Active conversations" (the rail's own back-link) shows
        up in the Archived state, now that reaching it no longer goes
        through `/chat/?archived=1`."""
        conversation = make_conversation(title="put away",
                                         archived_at=timezone.now())
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "chat-nav-row current" in body
        assert "Active conversations" in body

    def test_anything_but_archived_1_is_the_active_list(self, client):
        """A hand-typed query string can only ever land on one of the
        two lists that exist -- `?archived=0` (or anything else) is
        the active one, never the redirect (`ChatIndexView.get()`'s own
        `== "1"` equality, exact-match only)."""
        make_conversation(title="active")
        response = client.get(reverse("chat-index") + "?archived=0")
        assert response.status_code == 200
        assert "active" in response.content.decode()


class TestTheMenu:
    def test_every_action_is_a_details_and_a_post_form_never_a_script(self, client):
        conversation = make_conversation(title="a thread")
        nav = _nav(_index(client))
        for name in ("chat-conversation-rename", "chat-conversation-duplicate",
                     "chat-conversation-archive", "chat-conversation-delete",
                     "chat-conversation-pin"):
            assert f'action="{reverse(name, args=[conversation.id])}"' in nav
        assert "csrfmiddlewaretoken" in nav
        assert "<details" in nav
        assert "confirm(" not in nav
        assert "onclick" not in nav

    def test_the_active_list_offers_pin_not_unpin(self, client):
        conversation = make_conversation(title="unpinned")
        nav = _nav(_index(client))
        assert reverse("chat-conversation-pin", args=[conversation.id]) in nav
        assert reverse("chat-conversation-unpin", args=[conversation.id]) not in nav

    def test_a_pinned_row_offers_unpin_not_pin(self, client):
        """`chat/_sidebar_row.html`'s own row-level check
        (`row.conversation.pinned_at`), not the page-level `sidebar_
        archived` flag -- see that fragment's own module comment for
        why the archive toggle switched to the same reading this
        round, alongside pin/unpin."""
        conversation = make_conversation(title="bookmarked", pinned_at=timezone.now())
        body = _index(client)
        pinned_section = body[body.index('class="chat-nav-section chat-nav-pinned"'):
                              body.index('class="chat-nav-section chat-nav-chats"')]
        assert reverse("chat-conversation-unpin", args=[conversation.id]) in pinned_section
        assert reverse("chat-conversation-pin", args=[conversation.id]) not in pinned_section

    def test_a_shared_conversations_pinned_row_carries_no_menu_either(self, client):
        """RENDER-VS-GATE, the pinned section's own copy of the claim
        `test_a_shared_conversation_is_listed_with_no_menu_at_all`
        (below) already makes for Chats. PINNING IS THE OWNER's
        bookmark to SET (`Conversation.pinned_at`'s own docstring: no
        per-viewer pin table in v1) -- but, the same way an owner's
        `archived_at` already reads as shared, not per-viewer, for
        every principal `visible_conversations` admits the row to, a
        recipient's OWN sidebar shows this conversation in ITS Pinned
        section too (the row is visible to them, and it is pinned) --
        with no Unpin button, since `row.may_manage` is still False for
        a `use`-level share, the identical gate the Chats row already
        enforces."""
        owner, guest = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        conversation.title = "a bookmark that is not mine"
        conversation.pinned_at = timezone.now()
        conversation.save()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest,
                             level=Share.Level.USE)
        try:
            with posture(POSTURE_ENTERPRISE):
                sign_in(client, guest)
                body = _index(client)
        finally:
            reset_settings()
        pinned_section = body[body.index('class="chat-nav-section chat-nav-pinned"'):
                              body.index('class="chat-nav-section chat-nav-chats"')]
        assert "a bookmark that is not mine" in pinned_section
        assert reverse("chat-conversation-unpin", args=[conversation.pk]) not in pinned_section
        # SCOPED TO THE ROW GROUP (round 22): this used to read
        # `"chat-menu" not in pinned_section`, which was exact while the
        # only `.chat-menu` in this card was a ROW's -- the card's own
        # header now carries one too (the ⓘ explainer, which every
        # viewer gets and which is not a row action). Re-derived rather
        # than renumbered: the claim was always "this ROW has no menu",
        # and the rows live in `.chat-nav-group`.
        rows = pinned_section[pinned_section.index('class="chat-nav-group"'):]
        assert "chat-menu" not in rows
        assert 'aria-label="Actions for' not in pinned_section

    def test_the_rails_own_markup_carries_no_script_on_the_thread_page_either(
            self, client):
        """THE OTHER HALF OF THE RAIL-MARKUP CLAIM. The rail renders on
        two pages, and this assertion used to run on only one of them --
        the thread page carries its own pre-existing inline `onclick`
        (the delete-confirm Cancel that predates this surface), which an
        unsliced assertion would have tripped over. `_nav` slices the
        `<nav class="chat-nav">` element out, so the same claim is
        checkable on both.

        THE ONE SCRIPT THE FRAGMENT DOES INCLUDE -- `chat/
        _menu_exclusive.html`, round 21's one-menu-at-a-time
        enhancement -- sits AFTER the `</nav>` this slice ends at, by
        design, so it is outside the slice rather than exempted from
        it. That placement is what these three assertions still pin:
        the rail's markup itself carries no handler and no script, and
        the enhancement is separable from the thing it enhances."""
        conversation = make_conversation(title="a thread")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        nav = _nav(body)
        assert "confirm(" not in nav
        assert "onclick" not in nav
        assert "<script" not in nav
        # Non-vacuous: the page really does carry an inline handler
        # OUTSIDE the sidebar, which is exactly why the slice is needed.
        assert "onclick" in body

    def test_the_menu_panel_is_an_overlay_not_an_in_flow_block(self, client):
        """ROUND 2, item 2 (owner's ruling: "a menu that is above, not
        something that pushes all the other html down"). The panel was
        in flow, which shoved every row below it down the list; it is
        absolutely positioned now, and closing the menu costs no layout
        shift at all.

        PAINT CANNOT BE UNIT-TESTED, BUT THE DECLARATIONS CAN -- the
        same thing `models/registry/tests/test_views_tables_and_picker.py::
        test_endpoint_cell_carries_the_overflow_wrap_guard_class` does
        for its own grid-blowout guard, and the reason this asserts the
        rendered stylesheet rather than pretending to parse CSS.
        """
        make_conversation(title="a thread")
        body = _index(client)

        # The `<details>` is the panel's anchor -- `position: relative`
        # was removed as dead in fix round 1 (correctly, while the panel
        # was in flow) and is back with a job.
        assert ".chat-menu { position: relative;" in body

        # SLICED TO THE ONE RULE BLOCK, never asserted against the whole
        # document: `border-left: 2px solid var(--border)` below is also
        # (legitimately) the blockquote rule on the conversation page, so
        # a document-wide negative assertion would pass here only because
        # the INDEX happens not to load that rule -- and would break the
        # day somebody moved it onto this base, for a reason having
        # nothing to do with this menu.
        start = body.index(".chat-menu-body {")
        rule = body[start:body.index("}", start)]

        assert "position: absolute;" in rule
        assert "top: 100%;" in rule
        assert "right: 0;" in rule
        assert "z-index: 5;" in rule
        # A floating CARD: panel background, a hairline, a radius -- the
        # token recipe the one other floating panel on this box uses --
        # plus the elevation shade, token-derived so it follows the theme.
        assert "background: var(--panel);" in rule
        assert "border: 1px solid var(--border);" in rule
        assert ("box-shadow: 0 4px 14px "
                "color-mix(in srgb, var(--text) 18%, transparent);") in rule
        # The in-flow markers are GONE from that block, so this cannot
        # pass while the old rule is still what ships.
        assert "border-left" not in rule
        assert "margin: 0.25rem 0 0.5rem 0.75rem;" not in rule

    def test_the_overlay_is_still_nested_in_the_row_and_still_needs_no_script(
        self, client,
    ):
        """Positioning it absolutely changed CSS ONLY: the panel is the
        same markup in the same `<details>`, so the native toggle still
        opens and closes it and the nested delete confirm still works.
        An overlay built by moving the panel out of the row would have
        needed a script to place it."""
        conversation = make_conversation(title="a thread")
        nav = _nav(_index(client))

        assert '<details class="chat-menu">' in nav
        assert '<div class="chat-menu-body">' in nav
        # The panel opens INSIDE the menu element.
        assert nav.index('<details class="chat-menu">') < nav.index(
            '<div class="chat-menu-body">')
        # The delete confirm is still nested in there, and still a POST.
        assert reverse("chat-conversation-delete", args=[conversation.id]) in nav
        assert "onclick" not in nav
        assert "<script" not in nav

    def test_the_scrolling_sidebar_lifts_its_own_clip_while_a_menu_is_open(
        self, client
    ):
        """Owner-requested chat layout fix, review round: `nav.chat-nav`
        gains `overflow-y: auto` once the app shell lands (UX point 3),
        which would clip this test's own sibling above -- the overlay
        panel -- near the list's bottom edge. The FIRST fix dropped the
        panel to `position: static`, which reversed the standing ruling
        `test_the_menu_panel_is_an_overlay_not_an_in_flow_block` pins;
        review caught it. The real fix stays off `.chat-menu-body`
        entirely and lifts the clip on `nav.chat-nav` itself, only while
        a menu is open, via `:has()`.

        SLICED TO ITS OWN MEDIA BLOCK, not to the first `@media
        (min-width: 1000.01px)` in the file -- `chat/base.html` has TWO
        of them (the other gates `.chat-wrap`'s own app-shell rule), so
        `body.index("@media (min-width: 1000.01px)")` would find the
        WRONG one. Anchoring on the unique selector text itself and
        walking backward to the nearest preceding `@media` is what
        actually reaches this rule's own block, the same trap
        `test_the_menu_panel_is_an_overlay_not_an_in_flow_block` above
        avoids by slicing to the first `.chat-menu-body {` rather than
        asserting against the whole document."""
        body = _index(client)

        # ROUND 14: the clipping container this rule lifts is now
        # `.chat-nav-chats` (the rail's OWN scrolling region), not the
        # whole `nav.chat-nav` -- `chat/base.html`'s own rule comment
        # has the full reasoning for the move.
        selector_start = body.index(".chat-nav-chats:has(.chat-menu[open])")
        rule = body[selector_start:body.index("}", selector_start)]
        assert "overflow-y: visible;" in rule

        # The rule's own enclosing `@media` -- found by walking BACKWARD
        # from the selector, so this reaches the block that actually
        # wraps it rather than the earlier, unrelated one.
        media_start = body.rindex("@media (min-width: 1000.01px)", 0, selector_start)
        media_block = body[media_start:body.index("}", selector_start) + 1]
        assert ".chat-nav-chats:has(.chat-menu[open])" in media_block
        # Non-vacuous: the OTHER `@media (min-width: 1000.01px)` block
        # in the file is a real, different rule, not this one.
        assert media_start != body.index("@media (min-width: 1000.01px)")

        # `.chat-menu-body` itself is untouched by this fix -- the
        # standing overlay ruling stays intact at every width.
        panel_start = body.index(".chat-menu-body {")
        panel_rule = body[panel_start:body.index("}", panel_start)]
        assert "position: absolute;" in panel_rule
        assert "position: static" not in panel_rule

    def test_the_rename_form_is_prefilled_with_the_current_title(self, client):
        make_conversation(title="the name it has")
        assert 'value="the name it has"' in _index(client)

    def test_a_shared_conversation_is_listed_with_no_menu_at_all(self, client):
        """RENDER-VS-GATE. A `use` share is the widest this platform
        grants and it still does not make the thread the recipient's to
        change (`test_conversation_actions.py` asserts the four 404s
        from the other side) -- so the row is a link and nothing more.
        Five controls that all 404 would be the exact defect this
        surface removed from its compose form."""
        owner, guest = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        conversation.title = "a thread belonging to somebody else"
        conversation.save()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest,
                             level=Share.Level.USE)
        try:
            with posture(POSTURE_ENTERPRISE):
                sign_in(client, guest)
                body = _index(client)
        finally:
            reset_settings()
        assert "a thread belonging to somebody else" in body
        assert reverse("chat-conversation-rename", args=[conversation.pk]) not in body
        assert reverse("chat-conversation-delete", args=[conversation.pk]) not in body


class TestItCostsTheSameAtOneRowAndAtThirty:
    """THE N+1 PIN (fix round 1, Major 1). The sidebar asks
    `may_manage_conversation` once per listed row, which is exactly the
    caller `identity.access.sees_all_content`'s own `settings_row=`
    docstring names as the one that must not re-read the identity
    singleton per row -- and it was worse than one extra read each,
    because `identity.access._user_row` memoises on the settings-row
    INSTANCE, so a fresh row per call defeated the `identity_user` cache
    too. Measured before the fix: 15 queries at one row, 63 at 25 --
    exactly +2 per conversation, on the two most-trafficked pages in the
    app.

    A FLAT COUNT, ASSERTED TWICE AT ONE NUMBER, rather than an
    inequality: "these two are equal" would also pass if both grew, and
    the number is what a later reader needs in order to know whether
    their change added a query or removed one.

    PINNED TO `POSTURE_ENTERPRISE` explicitly, for the reason
    `tools/rag/tests/test_access_documents.py`'s own count pin is
    pinned to `POSTURE_OPEN`: on an open box `sees_all_content`
    short-circuits before it reads anything, so the per-row question is
    free no matter how it is written and this test would pass against
    the very defect it exists to catch.

    `settings_row` is passed the way BOTH real callers pass it --
    `identity.request.settings_row_for(request)`, the row the gate
    middleware already read for this request.
    """

    #: `count()`, the row slice, the archived `count()`, and the
    #: identity reads `visible_conversations` itself makes -- see the
    #: class docstring for why the number is written down rather than
    #: compared.
    #:
    #: RAISED FROM 14 BY TASK 14 (+1), THEN FROM 15 BY TASK 17 (+2), THEN
    #: DROPPED TO 13 BY THE CONSOLIDATION WAVE (-4, audit A finding F1),
    #: THEN BACK TO 14 BY ROUND 20 (+1, the PINNED list -- itemised in
    #: the per-query breakdown below, which is the reading that governs),
    #: THEN DROPPED TO 12 BY TASK 9 (Task 8 review, R2, -2): `visible_
    #: conversations`'s own `owned_rows_q(principal)` call now threads
    #: `settings_row` (`agents/visibility.py:107`) -- the settings
    #: assistant panel's open branch is what R2 measured, but the active
    #: list's call here takes the identical non-cheap branch and threads
    #: the identical row, so `owned_rows_q`'s internal `is_admin()` hits
    #: the shared `_user_row` cache instead of reading `IdentitySettings`
    #: and `identity_user` fresh -- 2 queries gone from the active list,
    #: below.
    #: `visible_conversations` gains a SECOND `shared_keys` call (spec
    #: §12.4's container-implies-contents clause, `Share.Target.
    #: WORKSTREAM` alongside the existing `Share.Target.CONVERSATION`
    #: one) -- paid once per call into that function, and `sidebar_
    #: context` USED TO call it TWICE (the active list, the archived
    #: count), so +2 total at the time. The fourth clause `visible_
    #: conversations` also gains (`Q(workstream__in=Workstream.objects.
    #: filter(owned_rows_q(principal)))`, ruling C) reuses the SAME
    #: already-computed `owned` value rather than calling `owned_rows_q`
    #: a second time -- so it costs no query of its own; a naive second
    #: call would have cost 2 more per invocation (4 total), which is
    #: exactly the regression this comment's own prior measurement
    #: caught. `sidebar_context` now also asks `visible_workstreams(
    #: principal, settings_row=settings_row)` for the Workstreams
    #: section, and BOTH `visible_conversations` and `visible_
    #: workstreams` are threaded with `settings_row` (Task 14 fix round
    #: -- see each function's own `settings_row=` docstring in `agents/
    #: visibility.py`), so the render's THREE calls into this module
    #: (the active list, the Workstreams list, the archived count)
    #: share ONE `IdentitySettings`/`identity_user` read via `sees_all_
    #: content` -- not three.
    #:
    #: THE CONSOLIDATION (F1): `sidebar_context` used to call `visible_
    #: conversations(principal, settings_row=settings_row)` a SECOND
    #: time, fresh, to build the archived count -- paying that
    #: function's own identity reads (`owned_rows_q`'s un-threaded
    #: `is_admin()`, `shared_keys` twice) all over again for a value the
    #: active-list call just resolved. `sidebar.py` now builds ONE base
    #: queryset and `.filter()`s it twice: `.filter()` clones the
    #: queryset without re-running the identity work already paid for
    #: by the first evaluation, so the archived count's own second
    #: `visible_conversations` call -- and the 4 queries it cost -- are
    #: simply gone.
    #:
    #: Measured, query by query (the full 12, confirmed against a
    #: captured-queries dump):
    #:
    #:   * the active list (5): the ONE shared `_user_row` fetch (the
    #:     other two calls below hit its cache) + `owned_rows_q`'s now-
    #:     THREADED `is_admin()` (0: `settings_row` carries the same
    #:     instance whose `_user_row` cache the line above already
    #:     warmed, Task 9/R2) + `shared_keys` TWICE, once per
    #:     `Share.Target` (2) + `.count()` (1) + the row slice (1).
    #:   * the Workstreams list (5, UNCHANGED by this wave -- `visible_
    #:     workstreams` itself is untouched): `sees_all_content`'s
    #:     `_user_row` read is a CACHE HIT this time (0) + `owned_rows_q`'s
    #:     own un-threaded `is_admin()` (2, ITS OWN fresh instance, not
    #:     the shared one) + `shared_keys` (1) + `.count()` (1) + the row
    #:     slice (1).
    #:   * the archived count (1): the base queryset's own identity
    #:     reads were already paid by the active list above -- `.filter(
    #:     archived_at__isnull=False).count()` on the ALREADY-BUILT
    #:     `base` costs exactly its own `.count()` query and nothing
    #:     else.
    #:   * the PINNED list (1, ROUND 20): the SAME already-built `base`,
    #:     `.filter(archived_at__isnull=True, pinned_at__isnull=False).
    #:     order_by("-pinned_at")` -- a clone, free -- then `list(...)`
    #:     to actually evaluate it, ONE query, uncapped (no separate
    #:     `.count()`: there is no "N more" line for this section, so
    #:     nothing needs the total ahead of the list). The `.filter(
    #:     pinned_at__isnull=True)` the active list's OWN row query also
    #:     gained this round costs nothing extra -- it is one more clause
    #:     on the SAME clone the active list already paid for.
    #:
    #: `visible_workstreams`'s OWN internal `owned_rows_q(principal)`
    #: call (inside the Workstreams-list 5, above) stays UN-THREADED on
    #: purpose (2 of these 12 queries): it is a different call, on a
    #: different function, that Task 9/R2 did not touch -- and, same as
    #: before, `owned_rows_q`'s internal `is_admin(principal)` is shared
    #: with `tools/vision/visibility.py` and `tools/rag/access.py`, so
    #: widening IT is a cross-column change and its own question -- the
    #: same bounded edge `may_manage_conversation`'s own docstring
    #: already names for the identical function. The per-row `may_
    #: manage_conversation` call below costs ZERO of these 12 (neither
    #: the Chats rows' own calls nor the Pinned rows' own, round 20's
    #: addition): by the time rows are iterated, the shared `settings_
    #: row`'s `_user_row` cache is already warm. The number the FLATNESS
    #: pin below cares about is that this stays the SAME at one row and
    #: at twenty-five -- which it does, because none of these 12 queries
    #: scale with row count. It moves again only if the sidebar (or
    #: `visible_workstreams`/`visible_conversations`) genuinely gains or
    #: loses a query.
    EXPECTED_QUERIES = 12

    def _sidebar(self, owner, rows, django_assert_num_queries):
        agent = make_agent()
        for _ in range(rows):
            create_conversation(user_principal(owner), agent)
        with posture(POSTURE_ENTERPRISE):
            settings_row = IdentitySettings.get_solo()
            with django_assert_num_queries(self.EXPECTED_QUERIES):
                context = sidebar_context(user_principal(owner),
                                          settings_row=settings_row)
        return context

    def test_one_conversation(self, django_assert_num_queries):
        context = self._sidebar(make_user(), 1, django_assert_num_queries)
        assert len(context["sidebar_rows"]) == 1

    def test_twenty_five_conversations_cost_exactly_the_same(
        self, django_assert_num_queries,
    ):
        """The assertion that makes the one above mean something: the
        SAME number, with twenty-four more rows in the list."""
        context = self._sidebar(make_user(), 25, django_assert_num_queries)
        assert len(context["sidebar_rows"]) == 25
        # Non-vacuous: the per-row question really was asked, and
        # answered True, for every one of them.
        assert all(row["may_manage"] for row in context["sidebar_rows"])

    def _pinned_sidebar(self, owner, rows, django_assert_num_queries):
        """round20-confirm.md's own R5: every measurement above creates
        UNPINNED conversations, so the PINNED section's own per-row
        `may_manage_conversation` loop -- the one thing in that section
        that could scale per row -- was never actually exercised at more
        than zero rows. This is that measurement, over
        `sidebar_pinned_rows` instead of `sidebar_rows`."""
        agent = make_agent()
        for _ in range(rows):
            Conversation.objects.create(agent=agent, pinned_at=timezone.now(),
                                        **owner_fields(user_principal(owner)))
        with posture(POSTURE_ENTERPRISE):
            settings_row = IdentitySettings.get_solo()
            with django_assert_num_queries(self.EXPECTED_QUERIES):
                context = sidebar_context(user_principal(owner),
                                          settings_row=settings_row)
        return context

    def test_one_pinned_conversation(self, django_assert_num_queries):
        context = self._pinned_sidebar(make_user(), 1, django_assert_num_queries)
        assert len(context["sidebar_pinned_rows"]) == 1
        assert len(context["sidebar_rows"]) == 0

    def test_twenty_five_pinned_conversations_cost_exactly_the_same(
        self, django_assert_num_queries,
    ):
        """The non-vacuity half, for the pinned section specifically:
        the SAME query count with twenty-four more pinned rows, and the
        per-row `may_manage` question really was asked and answered True
        for every one of them (the shape `test_twenty_five_
        conversations_cost_exactly_the_same`, above, already uses for
        `sidebar_rows`)."""
        context = self._pinned_sidebar(make_user(), 25, django_assert_num_queries)
        assert len(context["sidebar_pinned_rows"]) == 25
        assert all(row["may_manage"] for row in context["sidebar_pinned_rows"])


class TestThePagesCostTheSameAtOneRowAndAtTwentyFive:
    """THE SAME CLAIM, THROUGH THE REAL VIEWS -- and the two pins above
    cannot make it (fix round 1 re-check).

    `TestItCostsTheSameAtOneRowAndAtThirty` calls `sidebar_context`
    directly and hands it a `settings_row` of its own, so it pins the
    BUILDER and is blind to its callers: delete
    `settings_row=settings_row_for(request)` from both views and those
    two tests stay green while `/chat/` and every thread page go
    straight back to +2 queries per listed conversation. Measured with
    the view-side argument removed, over the same 1-row/25-row pair this
    class renders: the index went 47 -> 95 and the thread page
    56 -> 104. That is the hole this class closes.

    EQUALITY BETWEEN TWO RENDERS, NOT AN ABSOLUTE NUMBER, and that is
    the difference from the builder-level pins. A view count carries
    session and authentication reads that are nobody's business here and
    would make an absolute pin brittle for reasons unrelated to the
    sidebar. What this class asserts is the only thing it cares about:
    that the number does not MOVE when the list gets twenty-four rows
    longer.

    `POSTURE_ENTERPRISE` and a signed-in owner, for the reason the
    builder-level pins are pinned the same way: on an open box
    `sees_all_content` short-circuits before reading anything, so the
    per-row question is free however it is written and this class would
    pass against the very defect it exists to catch.
    """

    def _render_twice(self, client, url, agent, owner):
        """`(queries at 1 conversation, queries at 25)` for `url`.

        A WARM-UP REQUEST FIRST, discarded: Django caches a few things
        on the first request of a test (content types, the resolved URL
        conf), and counting that once would make the FIRST measurement
        the larger one for a reason that has nothing to do with how many
        conversations exist -- which is precisely the direction that
        would hide a regression from the equality below.
        """
        client.get(url)
        with CaptureQueriesContext(connection) as one_row:
            assert client.get(url).status_code == 200
        for _ in range(24):
            create_conversation(user_principal(owner), agent)
        with CaptureQueriesContext(connection) as many_rows:
            response = client.get(url)
        assert response.status_code == 200
        # Non-vacuous: the second render really did list all 25 rows, so
        # the equality is about a longer list rather than about a cap or
        # a filter quietly dropping the extra ones.
        assert response.content.decode().count('class="chat-nav-row') == 25
        return len(one_row), len(many_rows)

    def test_the_index_renders_the_same_number_of_queries_either_way(self, client):
        owner, agent = make_user(), make_agent()
        create_conversation(user_principal(owner), agent)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            one, twenty_five = self._render_twice(
                client, reverse("chat-index"), agent, owner)
        assert twenty_five == one

    def test_a_thread_page_renders_the_same_number_of_queries_either_way(self, client):
        """The thread page joins the same cell because it renders the
        same sidebar from the same builder -- and it is the page an
        operator opens most often, so a regression there costs the
        most."""
        owner, agent = make_user(), make_agent()
        here = create_conversation(user_principal(owner), agent)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            one, twenty_five = self._render_twice(
                client, reverse("chat-conversation", args=[here.id]), agent, owner)
        assert twenty_five == one


class TestItIsReadThroughVisibility:
    def test_the_sidebar_asks_the_one_list_gate(self, monkeypatch, client):
        """RULING 4c, pinned behaviourally (the column-boundary AST guard
        is the other half): if the sidebar stopped calling
        `visible_conversations`, this fails. The patch target moved
        TWICE now: the builder is `agents/chat/sidebar.py`, not the
        index view (UI-3b); and since the settings-surface exclusion
        (Task 6) the sidebar calls `chat_surface_conversations`, a thin
        wrapper that calls `visible_conversations` from INSIDE
        `agents/visibility.py`'s own module scope -- `sidebar.py` no
        longer imports that name at all, so the patch has to land on
        `agents.visibility.visible_conversations` itself, the module
        `chat_surface_conversations`'s call actually resolves against."""
        calls = []
        import agents.visibility as module

        real = module.visible_conversations
        # `**kwargs`, not a bare `p`: Task 14's fix round threads
        # `settings_row` into every call this module makes into
        # `visible_conversations` (see that function's own `settings_row=`
        # docstring in `agents/visibility.py`), so a double that only
        # accepted the positional principal would raise `TypeError` on the
        # very call this test exists to observe -- pinning behaviour that
        # this test would then falsely report as never having run.
        monkeypatch.setattr(module, "visible_conversations",
                            lambda p, **kwargs: calls.append(p) or real(p, **kwargs))
        client.get(reverse("chat-index"))
        assert calls and set(calls) == {OPEN_PRINCIPAL}


def _count_queries(fn) -> int:
    """How many queries `fn()` runs, wrapping
    `django.test.utils.CaptureQueriesContext` -- beside the module's other
    count helpers (`TestItCostsTheSameAtOneRowAndAtThirty`,
    `TestThePagesCostTheSameAtOneRowAndAtTwentyFive`)."""
    with CaptureQueriesContext(connection) as captured:
        fn()
    return len(captured)


def test_the_sidebar_lists_workstreams_above_conversations():
    user = make_user()
    _workstream(user_principal(user), name="Q3 planning")
    with posture("enterprise"):
        context = sidebar_context(user_principal(user))
    assert [w.name for w in context["sidebar_workstreams"]] == ["Q3 planning"]


def test_the_section_renders_even_when_empty():
    """A feature that only appears once you have used it is a feature
    nobody finds."""
    user = make_user()
    with posture("enterprise"):
        context = sidebar_context(user_principal(user))
    assert context["sidebar_workstreams"] == []
    assert "sidebar_workstreams" in context


def test_the_workstream_cap_is_honest_on_screen():
    """`WORKSTREAM_SIDEBAR_LIMIT = 5` (lowered from 10 in the owner's
    chat layout fix round 2: "show the 5 most recent ones"), with the
    same honest "…N more" line the conversation cap already renders.
    `13 - WORKSTREAM_SIDEBAR_LIMIT`, not a bare literal, so this pin
    keeps meaning the same thing if the constant is ever retuned again."""
    user = make_user()
    for i in range(13):
        _workstream(user_principal(user), name=f"S{i}")
    with posture("enterprise"):
        context = sidebar_context(user_principal(user))
    assert len(context["sidebar_workstreams"]) == WORKSTREAM_SIDEBAR_LIMIT
    assert context["sidebar_workstreams_older_count"] == 13 - WORKSTREAM_SIDEBAR_LIMIT


def test_a_recipient_sees_the_dormant_marker_when_stream_access_fails():
    """Spec §15.1 (review round 1, finding 2): the sidebar's own
    Workstreams row gets a dormant marker for a recipient whose grants
    no longer cover the stream's tags -- the same fact `stream_access`
    itself would answer, read off the batched reader instead."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)), name="Q3 planning")
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        context = sidebar_context(user_principal(reader))
    assert context["sidebar_dormant_workstream_ids"] == frozenset({stream.pk})


def test_a_recipient_with_a_live_grant_sees_no_dormant_marker():
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    grant(ent, user=reader)
    stream = _workstream(**owner_fields(user_principal(owner)), name="Q3 planning")
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        context = sidebar_context(user_principal(reader))
    assert context["sidebar_dormant_workstream_ids"] == frozenset()


def test_the_owner_never_sees_their_own_stream_marked_dormant():
    """`stream_access`'s own owner branch runs no tag check at all
    (ruling B's consequence -- a recipient's turn can taint the owner's
    stream, so the owner may hold no grant for a tag on their own
    stream), and the batched reader must agree rather than re-derive a
    narrower rule."""
    owner = make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        context = sidebar_context(user_principal(owner))
    assert context["sidebar_dormant_workstream_ids"] == frozenset()


def test_the_dormant_marker_renders_in_the_template(client):
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)), name="Q3 planning")
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        body = client.get(reverse("chat-index")).content.decode()
    assert "Q3 planning" in body
    assert "Dormant" in body


def test_the_dormant_marker_costs_the_same_at_one_row_and_at_the_cap(django_assert_num_queries):
    """THE HARD CONSTRAINT (review round 1, finding 2): batched, not
    per-row. `may_manage_workstream`'s own `sees_all_content` check pays
    ONE user-row read for the whole slice (cached on the threaded
    `settings_row`, same as every other per-row predicate in this
    module), `held_entitlement_ids` is one more, and the batched
    `WorkstreamTaint` read is the third -- three queries flat, asserted
    at one recipient row and at `WORKSTREAM_SIDEBAR_LIMIT` (named, not a
    bare "ten": the owner's chat layout fix round 2 lowered the constant
    to 5, and this pin's own claim -- flat at the cap -- does not change
    meaning just because the cap did), never N+1."""
    from agents.visibility import dormant_recipient_workstream_ids

    def _dormant_ids(rows):
        owner = make_user()
        reader_user = make_user()
        reader = user_principal(reader_user)
        streams = []
        for i, _ in enumerate(range(rows)):
            stream = _workstream(**owner_fields(user_principal(owner)), name=f"S{rows}-{i}")
            WorkstreamTaint.objects.create(
                workstream=stream, entitlement=make_entitlement(name=f"E{rows}-{i}"))
            Share.objects.create(target_type=Share.Target.WORKSTREAM,
                                 target_key=str(stream.pk), user=reader_user,
                                 level=Share.Level.USE)
            streams.append(stream)
        with posture("enterprise"):
            row = settings_row()
            with django_assert_num_queries(3):
                ids = dormant_recipient_workstream_ids(streams, reader, settings_row=row)
        return ids, streams

    one_ids, one_streams = _dormant_ids(1)
    assert one_ids == {s.pk for s in one_streams}
    # Named for what it IS -- the cap, whatever it is currently tuned to
    # -- not for a specific number that stopped being true when the
    # owner's chat layout fix round 2 lowered it from 10 to 5.
    cap_ids, cap_streams = _dormant_ids(WORKSTREAM_SIDEBAR_LIMIT)
    assert cap_ids == {s.pk for s in cap_streams}


@pytest.fixture
def agent():
    """A plain `Agent` row -- this module's own fixture, not a shared
    one, matching `agents/tests/test_workstreams.py`'s own precedent: no
    `agent` fixture exists anywhere else in this app's tests (every
    sibling module calls `make_agent()` directly)."""
    return make_agent()


def test_the_rail_never_scopes_the_conversation_list_to_one_stream(agent):
    """ROUND 14 (owner amendment) RETIRES THE SCOPING ITSELF (owner: "I
    believe we should remove the workstream chats from the side bar") --
    `sidebar_context` no longer accepts a `workstream=` keyword at all,
    so a stream-contained conversation and a loose one appear in the
    SAME global list, side by side, from every caller alike. The
    workstream page's own stream-filtered list still exists -- it lives
    on that page's MAIN content (`chat/workstream.html`'s own
    `conversations` context key), never in the rail this function
    builds."""
    user = make_user()
    stream = _workstream(user_principal(user))
    inside = Conversation.objects.create(agent=agent, workstream=stream,
                                         **owner_fields(user_principal(user)))
    loose = Conversation.objects.create(agent=agent, **owner_fields(user_principal(user)))
    with posture("enterprise"):
        rail = sidebar_context(user_principal(user))
    assert {r["conversation"].pk for r in rail["sidebar_rows"]} == {inside.pk, loose.pk}
    assert "sidebar_workstream" not in rail


@pytest.mark.parametrize("streams", [1, 25])
def test_the_sidebar_is_flat_in_the_number_of_workstreams(streams, django_assert_num_queries):
    """GLOBAL CONSTRAINT 6, on the list THIS task adds. The scoped-vs-plain
    comparison below cannot see it -- both sides pay the identical +2 --
    so the Workstreams section needs its own equality pin, plus one
    absolute pin so a future third query is visible rather than merely
    equal.

    `WORKSTREAM_SIDEBAR_LIMIT` bounds what is LISTED; it does not bound
    what is COUNTED, and `.count()` before the slice is the honest "…N
    more" line's own query."""
    user = make_user()
    for _ in range(streams):
        _workstream(user_principal(user))
    with posture("enterprise"):
        row = settings_row()
        with django_assert_num_queries(_SIDEBAR_BASELINE_QUERIES):
            sidebar_context(user_principal(user), settings_row=row)


# The absolute pin's number, named once so the two parametrized runs
# assert the SAME count and a reviewer sees what it is. It moves only
# when the sidebar genuinely gains a query, which is the event this pin
# exists to make visible.
#
# SEVENTEEN (raised from 15 by Task 17, WS-2's sharing gates): with zero
# conversations and zero-or-more workstreams, `sidebar_context`'s three
# calls into `agents/visibility.py` (the active list, the Workstreams
# list, the archived count) are ALL threaded with the ONE `settings_row`
# this test passes in, so they share a single `_user_row` read via
# `sees_all_content` rather than three (`agents/visibility.py`'s own
# `settings_row=` docstring on `visible_conversations` and `visible_
# workstreams`). What remains is `owned_rows_q`'s own internal,
# UN-THREADED `is_admin()` call, paid fresh in each of the three (2
# queries -- its own `IdentitySettings` + its own `identity_user` --
# every time, 6 total): that function is shared with `tools/vision/
# visibility.py` and `tools/rag/access.py` too, so widening it is a
# cross-column change outside this task's file list, the same bounded
# edge `may_manage_conversation`'s own docstring names for the identical
# function. `visible_conversations` (called twice here -- the active
# list and the archived count) runs a SECOND `shared_keys` per call, one
# per `Share.Target` (§12.4's container-implies-contents clause), on the
# active-list call; `visible_workstreams` itself is untouched, so the
# Workstreams list's own cost does not move. `TestItCostsTheSameAtOneRow
# AndAtThirty.EXPECTED_QUERIES`'s own comment has the full per-call
# breakdown (7 + 5 + 1 = 13 as of the consolidation wave; 14 as of round
# 20, see that same comment's own later note).
#
# DROPPED FROM 17 TO 13 BY THE CONSOLIDATION WAVE'S OWN F1: the archived
# count used to call `visible_conversations` a SECOND time, fresh,
# paying `owned_rows_q`'s own un-threaded `is_admin()` (2) and
# `shared_keys` twice (2) all over again -- `agents/chat/sidebar.py` now
# builds one base queryset and `.filter()`s it twice, so the archived
# count costs only its own `.count()` query.
#
# RAISED FROM 13 TO 14 BY ROUND 20: the PINNED section's own row query
# (`base.filter(archived_at__isnull=True, pinned_at__isnull=False).
# order_by("-pinned_at")`, evaluated with `list()`) is a clone of the
# SAME already-built `base` -- ONE new query, uncapped, no `.count()` of
# its own (there is no "…N more" line for this section) --
# `TestItCostsTheSameAtOneRowAndAtThirty.EXPECTED_QUERIES`'s own comment
# has the full per-call breakdown (7 + 5 + 1 + 1 = 14, confirmed against
# a captured-queries dump). What THIS pin actually needs is flatness
# (asserted below, at streams=1 and streams=25 alike), and 14 is that
# measured, honest number.
#
# DROPPED FROM 14 TO 12 BY TASK 9 (Task 8 review, R2): `visible_
# conversations`'s own `owned_rows_q(principal)` call now threads
# `settings_row` (`agents/visibility.py:107`), so its internal
# `is_admin()` hits the shared `_user_row` cache on the active-list call
# instead of paying its own fresh `IdentitySettings` + `identity_user`
# read -- 2 of the 6 queries the paragraph above counted for the three
# un-threaded `owned_rows_q` calls are now gone. `visible_workstreams`'s
# own `owned_rows_q` call is untouched, so 4 of those 6 remain (2 on the
# Workstreams list, and -- because the archived count reuses the SAME
# `base` queryset the active list already built -- the active list's own
# 2 do not repeat a second time either way).
# `TestItCostsTheSameAtOneRowAndAtThirty.EXPECTED_QUERIES`'s own comment
# has the full, current per-call breakdown (5 + 5 + 1 + 1 = 12).
_SIDEBAR_BASELINE_QUERIES = 12


@pytest.mark.parametrize("rows", [1, 25])
def test_a_sidebar_of_stream_conversations_costs_the_same_as_one_of_loose_ones(
        agent, django_assert_num_queries, rows):
    """RULING C COSTS NO QUERIES PER ROW (r4, §6.4/§17.3), asserted as a
    PINNED COUNT and not an inequality, because the regression this
    guards against was found by MEASUREMENT and would be invisible to a
    correctness test. `agents/chat/sidebar.py`'s own docstring records
    the last per-row read costing 47 -> 95 queries at 25 rows.

    `select_related("workstream")` is what makes it flat -- not an
    optimisation, but the condition of ruling C being affordable, since
    `may_manage_conversation` gains a stream-owner branch (and, round
    14, the per-row stream CHIP) that both read `conversation.
    workstream`'s own columns once per listed row.

    ROUND 14 REWORKS THE COMPARISON, NOT THE CLAIM: `sidebar_context` no
    longer accepts `workstream=` to isolate a stream-only read, so this
    now compares TWO SEPARATE principals' own worlds instead -- one
    whose `rows` conversations are all stream-contained, one whose are
    all loose -- each read through the SAME unscoped call. The property
    under test is unchanged: whether the workstream JOIN costs anything
    extra per row, never a scoping difference that no longer exists."""
    stream_owner = make_user()
    stream = _workstream(user_principal(stream_owner))
    for _ in range(rows):
        Conversation.objects.create(agent=agent, workstream=stream,
                                    **owner_fields(user_principal(stream_owner)))

    loose_owner = make_user()
    for _ in range(rows):
        Conversation.objects.create(agent=agent,
                                    **owner_fields(user_principal(loose_owner)))
    # MEASURE BOTH AND ASSERT EQUALITY, so the absolute number can move
    # with the platform while the DIFFERENCE stays zero -- which is the
    # property ruling C actually needs, and the one a fixed count would
    # make brittle for the wrong reason.
    with posture("enterprise"):
        stream_world = _count_queries(lambda: sidebar_context(
            user_principal(stream_owner), settings_row=settings_row()))
        loose_world = _count_queries(lambda: sidebar_context(
            user_principal(loose_owner), settings_row=settings_row()))
    assert stream_world == loose_world


class TestTheRailOrder:
    """ROUND 14 (OWNER AMENDMENT) gave the brief's own words: "structural
    pins for rail order (brand -> new -> nav -> workstreams -> chats ->
    account)". ROUND 15 (OWNER FEEDBACK), verbatim: "the navigation bar
    needs to be consistant on the top, you should not override the main
    navigation template for main, what the fuck, this looks like dog
    shit" -- a PARTIAL REVERSAL: brand, the app-nav rows, and the
    account block moved back to the restored top bar, so the rail's own
    fixed order is "+ New chat" -> Workstreams -> Chats, and the other
    three classes must be ABSENT from the rail (present instead in the
    restored `header.app-bar`, which `test_mount.py::TestTheNav`
    covers). ROUND 20 ADDS ONE CONDITIONAL SECTION, Pinned, strictly
    BETWEEN Workstreams and Chats when it renders at all (owner,
    verbatim: "always shown in its own se[c]tion ibetween Workstreams
    and chats") -- `test_the_pinned_section_lands_between_workstreams_
    and_chats_when_present`, below, is its own order pin; the two
    fixed-order tests above it are UNCHANGED, since neither ever creates
    a pinned conversation and so never renders that section at all.

    One test proves the three FIXED sections nest inside `nav.chat-nav`
    in that order on an otherwise-empty rail; a second proves the same
    order holds once every section has real content -- an empty rail
    alone cannot show a row landing in the WRONG section, since there
    are no rows to misplace.
    """

    def _rail(self, client):
        body = client.get(reverse("chat-index")).content.decode()
        start = body.index('<nav class="chat-nav"')
        return body[start:body.index("</nav>", start) + len("</nav>")]

    def test_the_three_sections_nest_in_order_on_an_empty_rail(self, client):
        user = make_user()
        with posture("enterprise"):
            sign_in(client, user)
            rail = self._rail(client)
        primary = rail.index('class="chat-rail-primary"')
        workstreams = rail.index('class="chat-workstreams')
        chats = rail.index('class="chat-nav-section chat-nav-chats"')
        assert primary < workstreams < chats
        # ABSENCE PINS (round 15): the three round-14 rail additions
        # that moved back to the restored top bar must not linger here.
        assert 'class="chat-rail-brand"' not in rail
        assert 'class="chat-rail-navlinks"' not in rail
        assert 'class="chat-rail-account"' not in rail

    def test_the_three_sections_nest_in_order_with_real_content_in_each(self, client):
        user = make_user()
        stream = _workstream(user_principal(user))
        Conversation.objects.create(agent=make_agent(slug="rail-order"), workstream=stream,
                                    **owner_fields(user_principal(user)))
        with posture("enterprise"):
            sign_in(client, user)
            rail = self._rail(client)
        primary = rail.index('class="chat-rail-primary"')
        workstreams = rail.index('class="chat-workstreams')
        chats = rail.index('class="chat-nav-section chat-nav-chats"')
        assert primary < workstreams < chats
        # Non-vacuous: the stream row this test actually created lands
        # strictly between the Workstreams section's own start and the
        # Chats section's own start, not merely somewhere on the page.
        assert workstreams < rail.index(stream.name) < chats
        assert 'class="chat-rail-brand"' not in rail
        assert 'class="chat-rail-navlinks"' not in rail
        assert 'class="chat-rail-account"' not in rail

    def test_the_pinned_section_lands_between_workstreams_and_chats_when_present(
        self, client
    ):
        """ROUND 20's own order claim, the one this class exists to
        pin: Pinned sits strictly between Workstreams and Chats, not
        merely somewhere on the page, and the pinned row itself lands
        inside that section's own bounds."""
        user = make_user()
        make_conversation(title="bookmarked", pinned_at=timezone.now(),
                          **owner_fields(user_principal(user)))
        with posture("enterprise"):
            sign_in(client, user)
            rail = self._rail(client)
        workstreams = rail.index('class="chat-workstreams')
        pinned = rail.index('class="chat-nav-section chat-nav-pinned"')
        chats = rail.index('class="chat-nav-section chat-nav-chats"')
        assert workstreams < pinned < chats
        assert pinned < rail.index("bookmarked") < chats


class TestTheRowMenusAreDropdownsNotToggles:
    """ROUND 21, ITEM B (owner, verbatim: "when I click on the ..., the
    prior one doesn't go away when I click on another one as seen in the
    image. it should be like a drop down not a toggle menu").

    Plain `<details>` elements are independent toggles and two can be
    open at once; a dropdown closes its predecessor. There is no markup
    or CSS that expresses "close that other element", so this is the
    surface's third sanctioned progressive-enhancement script
    (`chat/_menu_exclusive.html`, beside `_enter_to_send.html` and the
    drag-drop handler). What is testable server-side is that the script
    reaches every page that draws the rail, and that the properties it
    depends on are the ones actually written -- the same shape
    `test_the_page_carries_exactly_the_shared_enter_to_send_script`
    already takes for its own fragment.
    """

    def test_the_rail_includes_the_shared_script_once(self):
        """ONE INCLUDE, in the ONE fragment that renders the menus, so
        every rail surface gets it and no leaf page includes it
        itself."""
        source = (Path(__file__).resolve().parents[1]
                  / "templates" / "chat" / "_sidebar.html").read_text()
        assert source.count('{% include "chat/_menu_exclusive.html" %}') == 1

    @pytest.mark.parametrize("page", ["chat-index", "chat-all"])
    def test_every_rail_surface_serves_the_listener(self, client, page):
        make_conversation(title="a thread")
        body = client.get(reverse(page)).content.decode()
        assert 'rail.addEventListener("toggle"' in body

    def test_the_thread_page_serves_it_too(self, client):
        conversation = make_thread()
        body = client.get(reverse("chat-conversation",
                                  args=[conversation.id])).content.decode()
        assert 'rail.addEventListener("toggle"' in body

    def test_the_listener_is_registered_in_the_CAPTURE_phase(self, client):
        """LOAD-BEARING, not a style choice: `toggle` does not bubble, so
        a delegated listener on the rail never sees a row's own toggle in
        the bubble phase at all. Capture reaches ancestors regardless of
        `bubbles`.

        A SLICE, NOT A BARE MARKER (fix round 1, M4): the first cut
        asserted `"}, true);" in body`, which ANY script on the page
        ending in a `true` third argument satisfies -- it would have
        stayed green with THIS listener's `true` dropped and some other
        script keeping one.

        THE SLICE ENDS AT THE NEXT REGISTRATION (polish, R1), not at the
        script's closing `})();`: this file has exactly one of those, so
        an end-of-IIFE slice still spanned the `pointerdown` listener
        below and its `true` could have answered for this one -- the same
        looseness M4 named, narrowed rather than removed. Bounded by the
        `pointerdown` registration's own index, the slice contains this
        listener and nothing else. What that proves is precise and worth
        stating exactly: SOME `addEventListener` between the `toggle`
        registration and the next one is capture-phase, and there is only
        one."""
        body = _index(client)
        start = body.index('rail.addEventListener("toggle"')
        end = body.index('document.addEventListener("pointerdown"', start)
        assert "}, true);" in body[start:end]

    def test_the_outside_click_dismissal_is_also_capture_phase_and_pointer_based(
            self, client):
        """FIX ROUND 1 (M3). "Like a drop down" also means it closes when
        you click elsewhere. `pointerdown` (not `click`) covers touch and
        pen as well as mouse, and CAPTURE means the menu closes before
        the click reaches whatever was clicked, so dismissal never
        swallows or re-orders the interaction that caused it."""
        body = _index(client)
        start = body.index('document.addEventListener("pointerdown"')
        end = body.index('document.addEventListener("keydown"', start)
        assert "}, true);" in body[start:end]

    def test_a_click_inside_an_open_menu_does_not_dismiss_it(self, client):
        """The guard that makes outside-click safe: without the
        `closest("details.chat-menu")` test, clicking Rename, its text
        field, or Save would close the menu out from under the very
        interaction it belongs to."""
        body = _index(client)
        assert 'event.target.closest("details.chat-menu")' in body

    def test_escape_dismisses_too(self, client):
        """Where a keyboard user expects it, and it works whether focus
        is on the summary, inside the Rename field, or nowhere."""
        body = _index(client)
        assert 'event.key === "Escape"' in body

    def test_all_three_paths_close_through_ONE_function(self, client):
        """One `closeMenus`, three callers -- not three copies of the
        same loop drifting apart. `closeMenus(opened)` for the exclusive
        open (keep the one just opened), `closeMenus(null)` for both
        dismissals (keep nothing)."""
        body = _index(client)
        assert body.count("function closeMenus(") == 1
        assert "closeMenus(opened);" in body
        assert body.count("closeMenus(null);") == 2

    def test_closing_a_menu_also_collapses_its_nested_forms(self, client):
        """POLISH (R3), and the reason it is user-visible: a nested
        `<details>` keeps its OWN `open` attribute when its parent menu
        closes, so a row whose Rename you had expanded reopens -- a page
        load or a week later -- still showing a stale, pre-filled form
        nobody asked for. Invisible while the parent is shut, which is
        exactly why it has to be cleared on the way OUT.

        EVERY CLOSE PATH GETS IT because all three route through
        `closeMenus` (pinned by the test above), so this is one loop
        inside that one function rather than three call sites to keep in
        step."""
        body = _index(client)
        start = body.index("function closeMenus(")
        end = body.index('rail.addEventListener("toggle"', start)
        closer = body[start:end]
        assert 'querySelectorAll("details[open]")' in closer
        assert "nested[j].open = false;" in closer

    def test_a_non_rail_menu_click_is_documented_as_not_dismissing(self):
        """POLISH (R2). The `closest` guard is document-wide, not
        rail-scoped, so clicking `chat/workstream_settings.html`'s
        "Manage" disclosure -- the same `.chat-menu` idiom deliberately
        OUTSIDE the rail -- spares an open rail menu instead of
        dismissing it. That is the friendlier reading and is kept, but
        the script has to SAY so rather than let a reader infer from
        "a click INSIDE an open panel" that the guard is narrower than
        it is.

        READS THE TEMPLATE SOURCE, not a rendered page: a `{% templatetag
        openblock %} comment {% templatetag closeblock %}` block never
        reaches the response, which is the whole reason it is the right
        home for reasoning like this -- and the same reason
        `test_the_rail_includes_the_shared_script_once` above reads the
        file too."""
        source = (Path(__file__).resolve().parents[1]
                  / "templates" / "chat" / "_menu_exclusive.html").read_text()
        assert "DOCUMENT-WIDE, NOT RAIL-SCOPED" in source
        assert "workstream_settings.html" in source

    def test_it_closes_only_OTHER_row_menus(self, client):
        """The `chat-menu` class guard is what keeps opening the nested
        Rename disclosure from closing the very menu it lives inside;
        the `open` check is what keeps `other.open = false` from
        re-entering the handler it triggers."""
        body = _index(client)
        assert 'opened.classList.contains("chat-menu")' in body
        assert "!opened.open" in body
        assert 'querySelectorAll("details.chat-menu[open]")' in body

    def test_the_menu_still_works_with_no_script_at_all(self, client):
        """PROGRESSIVE ENHANCEMENT. Every menu is still a native
        `<details>` whose entries are real POST forms -- with JavaScript
        off, two menus can simply be open at once, which is the
        untidy-but-not-broken behaviour this surface shipped with before
        this round. Nothing here needs the script to function."""
        conversation = make_conversation(title="a thread")
        nav = _nav(_index(client))
        assert '<details class="chat-menu">' in nav
        assert "onclick" not in nav
        assert "javascript:" not in nav
        assert "<script" not in nav
        assert f'action="{reverse("chat-conversation-pin", args=[conversation.id])}"' in nav


class TestThePinnedMenuIsNotClipped:
    """ROUND 21, ITEM C (owner, verbatim: "Also when i click the ... on
    pinned, it expands but it's hidden and I'm not able to see the ...
    clicked").

    Round-20-confirm R7 bounded the Pinned section's row group
    (`max-height` + `overflow-y: auto`) to fix a real spill defect, and
    thereby made it a clipping context for the absolutely-positioned
    menu panels inside it. BOTH PROPERTIES HAVE TO SURVIVE: the bound
    (so many pins can never crush the rail) and a fully visible open
    menu. The fix is the house idiom `.chat-nav-chats` already uses one
    rule up -- lift the CLIP, keep the BOUND, scoped by `:has()` to the
    moment a menu is open.
    """

    def test_the_bound_on_the_pinned_group_is_still_there(self, client):
        """THE ROUND-20 DEFECT MUST NOT REOPEN: without this, an
        operator with many pins collapses `.chat-nav-chats` to zero and
        spills past the rail with no scrollbar anywhere."""
        body = _index(client)
        assert ".chat-nav-pinned { flex-shrink: 0; }" in body
        assert (".chat-nav-pinned .chat-nav-group { max-height: 14rem; "
                "overflow-y: auto; }") in body

    def test_an_open_menu_lifts_the_clip_on_the_pinned_group(self, client):
        body = _index(client)
        assert (".chat-nav-pinned .chat-nav-group:has(.chat-menu[open]) "
                "{ overflow-y: visible; }") in body

    def test_the_chats_sections_own_unclip_is_untouched(self, client):
        """The rule this one is modelled on still stands -- the fix adds
        a second ancestor, it does not move the first."""
        body = _index(client)
        assert ".chat-nav-chats:has(.chat-menu[open]) { overflow-y: visible; }" in body

    def test_the_panel_itself_is_still_the_standing_overlay(self, client):
        """NOT option (c). The standing owner ruling -- "a menu that is
        above, not something that pushes all the other html down" --
        governs until the owner changes it, so the fix is on the
        clipping ancestor and the panel is untouched."""
        body = _index(client)
        assert ".chat-menu-body {" in body
        assert "position: absolute;" in body
        assert "z-index: 5;" in body
