"""`/chat/all/` -- the conversations browser (round 14, Part 2; owner,
verbatim: "I believe we should remove the workstream chats from the
side bar, but have a button and a new associated window that lets you
see all chats where it shows all the chats in a list with searchable
features that show workstream (if it's a part of one), entitlements
tagged for that conversation, ect. ... Maybe on the new window we can
preview the most recent language/chat of the conversation we click.").

WHAT THIS FILE PINS: the search/filter/pagination GET forms, the
`?selected=` server-rendered preview pane (round 13's own attachment
chips included), tag-name disclosure honesty (`agents.workstreams.
tags_for_conversations`'s own parity test lives in `agents/tests/
test_workstreams.py::TestTagsForConversations` -- this file only pins
that the TEMPLATE renders what the view already computed correctly),
the `/chat/?archived=1` redirect landing here, and the two archive-list
claims `test_sidebar.py::TestTheArchive`'s own docstring names as
having moved here (which rows an archived list shows; an empty
archive's own message) now that reaching "Archived" no longer has a
thread page of its own to land on.

ASSERTIONS ABOUT "WHICH ROWS APPEAR" ARE SCOPED TO `_table(body)`, NOT
THE WHOLE PAGE, throughout this file. The rail's own CHATS section
(`chat/_sidebar.html`) renders the GLOBAL most-recent list on every
chat page including this one (round 14, Part 1) -- unfiltered by this
page's own `q=`/`workstream=`/`tag=`/`archived=`, by design. A body-
wide substring check would see a title through the RAIL even when the
TABLE correctly excluded it, which would make a real regression in the
table's own filtering invisible.

NO FARABUNKER_FEATURES OVERRIDE (see `test_mount.py`'s own docstring)
-- every test here does an HTTP request or a `reverse()`.
"""
from __future__ import annotations

import re

import pytest
from django.urls import reverse
from django.utils import timezone

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    grant, make_agent, make_conversation, make_entitlement, make_turn, make_user, posture,
    sign_in, user_principal,
)
from agents.models import ConversationTaint, Turn
from agents.tests._helpers import _workstream, make_document
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE
from tools.rag.models import Document, DocumentAttachment

pytestmark = pytest.mark.django_db


def _all(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = reverse("chat-all") + (f"?{query}" if query else "")
    return client.get(url).content.decode()


def _table(body: str) -> str:
    """Just the `<table class="all-table">...</table>` region -- see
    this module's own docstring for why row-presence assertions never
    read the whole page."""
    start = body.index('<table class="all-table">')
    return body[start:body.index("</table>", start)]


def _strip_csrf(body: str) -> str:
    """Django's masked CSRF token is randomised on EVERY render, by
    design (BREACH mitigation) -- even for the identical session and
    the identical underlying secret. A byte-equality check across two
    separate requests must normalise this field away first, or it
    fails on every page that carries a CSRF-protected form regardless
    of whether the page's own content actually changed."""
    return re.sub(r'csrfmiddlewaretoken" value="[^"]*"', 'csrfmiddlewaretoken" value="X"', body)


class TestSearch:
    def test_q_matches_the_title_case_insensitively(self, client):
        agent = make_agent(slug="general")
        make_conversation(agent=agent, title="Quarterly Budget Review")
        make_conversation(agent=agent, title="unrelated chat")
        body = _table(_all(client, q="budget"))
        assert "Quarterly Budget Review" in body
        assert "unrelated chat" not in body

    def test_an_empty_q_is_the_whole_list(self, client):
        agent = make_agent(slug="general")
        make_conversation(agent=agent, title="one")
        make_conversation(agent=agent, title="two")
        body = _table(_all(client))
        assert "one" in body and "two" in body

    def test_a_clear_link_appears_only_once_there_is_something_to_clear(self, client):
        make_conversation(title="one")
        assert "Clear" not in _all(client)
        assert "Clear" in _all(client, q="one")

    def test_no_match_says_so_rather_than_rendering_an_empty_table_silently(self, client):
        make_conversation(title="one")
        assert "No conversations match" in _all(client, q="nothing-matches-this")


class TestWorkstreamFilter:
    def test_it_narrows_to_one_stream(self, client):
        agent = make_agent(slug="general")
        user = make_user()
        stream = _workstream(user_principal(user))
        make_conversation(agent=agent, title="in stream", workstream=stream,
                          **owner_fields(user_principal(user)))
        make_conversation(agent=agent, title="loose", **owner_fields(user_principal(user)))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            body = _table(_all(client, workstream=stream.pk))
        assert "in stream" in body
        assert "loose" not in body

    def test_a_stream_id_this_viewer_cannot_see_is_silently_ignored_not_a_500(self, client):
        """Filter values are never a gate on this platform (`_all_
        context`'s own comment) -- a hand-typed id naming a stream this
        principal cannot see degrades to "no narrowing", the honest
        GET-filter answer, never a refusal. ENTERPRISE POSTURE, not
        open: under the open posture every principal IS the same
        shared identity, so `other`'s own stream would be visible to
        the caller too and this test would be proving nothing -- the
        permission boundary this test needs only exists once there are
        two distinct principals."""
        other = make_user()
        stream = _workstream(user_principal(other))
        viewer = make_user()
        make_conversation(title="mine", **owner_fields(user_principal(viewer)))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _table(_all(client, workstream=stream.pk))
        assert "mine" in body


def _all_tags(client, tag_ids, **params):
    """Like `_all` above, but for MULTI-VALUE `tag=` -- `_all`'s own
    kwargs shape cannot repeat a query key, and ROUND 16 makes `tag=`
    repeatable (`getlist`, not `get`). Builds `tag=<id>&tag=<id>...`
    alongside whatever else `**params` names, the same plain-GET shape
    the checkbox dropdown's own Apply button submits."""
    query = "&".join([f"tag={t}" for t in tag_ids] + [f"{k}={v}" for k, v in params.items()])
    url = reverse("chat-all") + (f"?{query}" if query else "")
    return client.get(url).content.decode()


class TestTagFilter:
    """ROUND 16 (owner feedback: "tags for this page should have a drop
    down with checkboxes and search, not a shown list that is
    selected") makes `tag=` MULTI-VALUE, OR-matched across the selected
    set -- these tests cover the single-tag cases the pre-round-16 UI
    already proved (unchanged in substance, `tag=<id>` still narrows
    and an unheld/unknown id is still silently ignored) plus the new
    multi-tag union and `getlist` edge cases round 16 itself adds.
    """

    def test_it_narrows_to_conversations_tainted_with_the_held_tag(self, client):
        agent = make_agent(slug="general")
        viewer = make_user()
        held = make_entitlement(name="held-tag")
        grant(held, user=viewer)
        tainted = make_conversation(agent=agent, title="tagged",
                                    **owner_fields(user_principal(viewer)))
        ConversationTaint.objects.create(conversation=tainted, entitlement=held)
        make_conversation(agent=agent, title="untagged",
                          **owner_fields(user_principal(viewer)))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _table(_all(client, tag=held.pk))
        assert "tagged" in body
        assert "untagged" not in body

    def test_two_tags_together_are_a_union_not_an_intersection(self, client):
        """THE BRIEF'S OWN WORDS: "multi-tag OR filtering (two tags ->
        union, non-vacuous)". Three conversations: one tainted with
        tag A only, one with tag B only, one with neither -- checking
        BOTH A and B must surface the first two and exclude the third.
        Non-vacuous because the third conversation proves this is not
        merely "no filter at all"."""
        agent = make_agent(slug="general")
        viewer = make_user()
        tag_a = make_entitlement(name="tag-a")
        tag_b = make_entitlement(name="tag-b")
        grant(tag_a, user=viewer)
        grant(tag_b, user=viewer)
        only_a = make_conversation(agent=agent, title="only-a",
                                   **owner_fields(user_principal(viewer)))
        ConversationTaint.objects.create(conversation=only_a, entitlement=tag_a)
        only_b = make_conversation(agent=agent, title="only-b",
                                   **owner_fields(user_principal(viewer)))
        ConversationTaint.objects.create(conversation=only_b, entitlement=tag_b)
        neither = make_conversation(agent=agent, title="neither",
                                    **owner_fields(user_principal(viewer)))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _table(_all_tags(client, [tag_a.pk, tag_b.pk]))
        assert "only-a" in body
        assert "only-b" in body
        assert "neither" not in body

    def test_offered_options_are_only_the_viewers_own_holdable_set(self, client):
        """Part 2's own words: "tag= entitlement filter (offered from
        viewer's own holdable set)". An entitlement the viewer does not
        hold must not appear as a filter CHOICE at all -- a stronger
        claim than merely not being named in a row's own tag chips."""
        viewer = make_user()
        held = make_entitlement(name="held-tag")
        grant(held, user=viewer)
        make_entitlement(name="somebody-elses-tag")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all(client)
        assert "held-tag" in body
        assert "somebody-elses-tag" not in body

    def test_a_tag_id_the_viewer_does_not_hold_is_silently_ignored(self, client):
        viewer = make_user()
        not_held = make_entitlement(name="not-held")
        make_conversation(title="mine", **owner_fields(user_principal(viewer)))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _table(_all(client, tag=not_held.pk))
        assert "mine" in body

    def test_getlist_edges_unknown_and_duplicate_values_are_dropped_not_a_gate(self, client):
        """THE BRIEF'S OWN WORDS: "getlist parsing edges (unknown/dup
        values ignored honestly)". `?tag=<held>&tag=<held>&tag=99999999
        &tag=not-a-number` must behave EXACTLY like `?tag=<held>` alone
        -- the duplicate and the two garbage values change nothing,
        never a 400 and never an accidental narrowing to "nothing"."""
        viewer = make_user()
        held = make_entitlement(name="held-tag")
        grant(held, user=viewer)
        tainted = make_conversation(title="tagged",
                                    **owner_fields(user_principal(viewer)))
        ConversationTaint.objects.create(conversation=tainted, entitlement=held)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            response = client.get(
                reverse("chat-all")
                + f"?tag={held.pk}&tag={held.pk}&tag=99999999&tag=not-a-number")
        assert response.status_code == 200
        assert "tagged" in _table(response.content.decode())

    def test_all_unknown_tags_degrades_to_the_unfiltered_list(self, client):
        viewer = make_user()
        make_entitlement(name="not-held-one")
        make_entitlement(name="not-held-two")
        make_conversation(title="mine", **owner_fields(user_principal(viewer)))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = client.get(reverse("chat-all") + "?tag=1&tag=2").content.decode()
        assert "mine" in _table(body)


class TestTagsDropdown:
    """ROUND 16 (owner feedback: "tags for this page should have a drop
    down with checkboxes and search, not a shown list that is
    selected") -- the dropdown control itself: the closed summary's own
    count, the checkbox round-trip from the URL, and the one sanctioned
    `<script>` this round adds, plus its own JS-off honesty."""

    def test_the_closed_summary_reads_plain_tags_when_nothing_is_selected(self, client):
        viewer = make_user()
        held = make_entitlement(name="held-tag")
        grant(held, user=viewer)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all(client)
        assert "<summary" in body
        assert ">Tags<" in body
        assert "Tags ·" not in body

    def test_the_closed_summary_shows_a_count_once_tags_are_selected(self, client):
        viewer = make_user()
        tag_a = make_entitlement(name="tag-a")
        tag_b = make_entitlement(name="tag-b")
        grant(tag_a, user=viewer)
        grant(tag_b, user=viewer)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all_tags(client, [tag_a.pk, tag_b.pk])
        assert "Tags · 2" in body

    def test_checkbox_checked_state_round_trips_from_the_url(self, client):
        """`?tag=<a>` alone: `a`'s own checkbox carries `checked`, `b`'s
        own does not -- proven from the SAME rendered markup, not two
        separate fetches, so an accidental swap between the two rows
        fails this test rather than passing it by coincidence."""
        viewer = make_user()
        tag_a = make_entitlement(name="tag-a-checked")
        tag_b = make_entitlement(name="tag-b-unchecked")
        grant(tag_a, user=viewer)
        grant(tag_b, user=viewer)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all(client, tag=tag_a.pk)
        # `chat/_search_checklist.html`'s own row markup, ROUND 17 (the
        # shared component both consumers now render through).
        checked_row = (f'<input type="checkbox" name="tag" value="{tag_a.pk}" checked>'
                       f'\n        tag-a-checked')
        unchecked_row = (f'<input type="checkbox" name="tag" value="{tag_b.pk}">'
                         f'\n        tag-b-unchecked')
        assert checked_row in body
        assert unchecked_row in body

    def test_no_tags_script_at_all_when_there_are_no_offered_tags(self, client):
        """The TAGS script (like the dropdown itself) is gated on `tag_
        options` -- a viewer with nothing to filter by never ships an
        empty dropdown OR a script with nothing to attach to. ROUND 21
        makes the count, not the absence, the honest assertion: this
        page renders the rail, and the rail now carries one shared
        script of its own (`chat/_menu_exclusive.html`) on every chat
        page alike. The checklist marker is what pins that the missing
        one is the TAGS one."""
        make_conversation(title="mine")
        body = _all(client)
        assert body.count("<script") == 1
        assert 'class="checklist-search"' not in body

    def test_exactly_one_script_renders_once_tags_are_offered(self, client):
        """THE BRIEF'S OWN WORDS: "script markers pinned (present +
        the JS-off honesty)". Exactly one -- the house convention
        `test_thread.py`/`test_workstream_page.py` already use
        (`body.count("<script")`) for a page with one sanctioned
        block, updated here from the pre-round-16 `not in body` claim
        `TestZeroJsAndShareableUrls` used to make unconditionally. TWO
        since round 21: the tags dropdown's own, plus the rail's shared
        exclusive-open handler that every chat page serves."""
        viewer = make_user()
        held = make_entitlement(name="held-tag")
        grant(held, user=viewer)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all(client)
        assert body.count("<script") == 2
        assert 'class="checklist-search"' in body

    def test_the_search_input_carries_the_js_off_honesty_title(self, client):
        """THE JS-OFF HONESTY PIN: the search input is visible either
        way, but its own `title=` says plainly that filtering needs
        JavaScript -- proven by SOURCE text, not by simulating a JS-off
        browser (this house convention has no headless browser)."""
        viewer = make_user()
        held = make_entitlement(name="held-tag")
        grant(held, user=viewer)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all(client)
        start = body.index('class="checklist-search"')
        tag = body[body.rindex("<input", 0, start):body.index(">", start) + 1]
        assert "requires JavaScript" in tag

    def test_clear_inside_the_dropdown_is_a_plain_get_link_dropping_tag(self, client):
        """Point 3: "a Clear control inside the dropdown unchecks all
        (server-side: absence of tag params)" -- `all_tags_url` is a
        real `<a href>`, not a script-driven uncheck-all, so it works
        identically with JS on or off."""
        viewer = make_user()
        held = make_entitlement(name="held-tag")
        grant(held, user=viewer)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all(client, tag=held.pk)
        assert f'href="{reverse("chat-all")}">Clear</a>' in body


class TestTagDisclosureHonesty:
    """The rule `tags_for_conversations`'s own parity test
    (`agents/tests/test_workstreams.py::TestTagsForConversations`)
    proves the VIEW computes correctly; this class proves the TEMPLATE
    renders that computation honestly -- an unheld tag's NAME never
    reaches the page, only a count."""

    def test_an_unheld_tags_name_never_appears_only_its_count(self, client):
        viewer = make_user()
        held = make_entitlement(name="held-tag")
        grant(held, user=viewer)
        hidden = make_entitlement(name="a-name-nobody-should-see")
        conversation = make_conversation(title="multi-tagged",
                                         **owner_fields(user_principal(viewer)))
        ConversationTaint.objects.create(conversation=conversation, entitlement=held)
        ConversationTaint.objects.create(conversation=conversation, entitlement=hidden)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all(client)
        assert "held-tag" in body
        assert "a-name-nobody-should-see" not in body
        assert "+1 more" in body

    def test_a_conversation_tagged_with_nothing_the_viewer_holds_shows_only_a_count(
            self, client):
        viewer = make_user()
        hidden_one = make_entitlement(name="hidden-one")
        hidden_two = make_entitlement(name="hidden-two")
        conversation = make_conversation(title="opaque to me",
                                         **owner_fields(user_principal(viewer)))
        ConversationTaint.objects.create(conversation=conversation, entitlement=hidden_one)
        ConversationTaint.objects.create(conversation=conversation, entitlement=hidden_two)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all(client)
        assert "hidden-one" not in body
        assert "hidden-two" not in body
        assert "+2 more" in body


class TestArchived:
    """The two claims `test_sidebar.py::TestTheArchive`'s own docstring
    names as having moved here: which rows an archived list shows, and
    an empty archive's own message -- neither has a thread page to
    reach any more now that `/chat/?archived=1` only ever redirects."""

    def test_the_archived_list_shows_archived_rows_not_active_ones(self, client):
        agent = make_agent(slug="general")
        make_conversation(agent=agent, title="still active")
        make_conversation(agent=agent, title="put away", archived_at=timezone.now())
        body = _table(_all(client, archived=1))
        assert "put away" in body
        assert "still active" not in body

    def test_an_empty_archive_says_so_rather_than_rendering_an_empty_table_silently(
            self, client):
        make_conversation(title="active")
        assert "Nothing archived yet" in _all(client, archived=1)

    def test_the_chat_index_redirect_lands_here_with_the_flag_carried(self, client):
        response = client.get(reverse("chat-index") + "?archived=1")
        assert response.status_code == 302
        assert response.url == reverse("chat-all") + "?archived=1"

    def test_the_redirect_carries_other_params_too(self, client):
        """`request.GET.urlencode()` copies the WHOLE query string, not
        merely the one key the redirect branches on."""
        response = client.get(reverse("chat-index") + "?archived=1&q=hello")
        assert response.status_code == 302
        assert "archived=1" in response.url
        assert "q=hello" in response.url

    def test_a_plain_chat_index_get_is_not_redirected(self, client):
        response = client.get(reverse("chat-index"))
        assert response.status_code == 200


class TestPagination:
    def test_the_default_page_size_is_fifty(self, client):
        from agents.chat.views.all_conversations import PAGE_SIZE

        assert PAGE_SIZE == 50

    def test_a_second_page_is_reachable_and_disjoint_from_the_first(self, client):
        agent = make_agent(slug="general")
        for i in range(60):
            make_conversation(agent=agent, title=f"conv-{i:03d}")
        page_one = _table(_all(client))
        page_two = _table(_all(client, page=2))
        assert page_one != page_two
        assert "Page 1 of 2" in _all(client)
        assert "Page 2 of 2" in _all(client, page=2)

    def test_the_honest_total_is_shown(self, client):
        agent = make_agent(slug="general")
        for i in range(3):
            make_conversation(agent=agent, title=f"conv-{i}")
        assert "3 conversations" in _all(client)


class TestPreviewPane:
    def test_selecting_a_row_renders_its_last_three_turns(self, client):
        conversation = make_conversation(title="a thread")
        for i in range(5):
            make_turn(conversation=conversation, role=Turn.Role.USER, text=f"turn body {i}",
                     index=Turn.next_index(conversation))
        body = _all(client, selected=conversation.id)
        assert "turn body 4" in body
        assert "turn body 3" in body
        assert "turn body 2" in body
        assert "turn body 1" not in body
        assert "turn body 0" not in body

    def test_no_selected_param_renders_no_pane(self, client):
        conversation = make_conversation(title="a thread")
        make_turn(conversation=conversation, role=Turn.Role.USER, text="hello")
        assert "hello" not in _all(client)

    def test_the_pane_reuses_the_shared_turn_card_fragment(self, client):
        """`_preview_cards` calls `agents.chat.rendering.turn_card` and
        the template includes `chat/_turn_block.html` -- the SAME
        fragment family the full thread page renders through, proven
        here by the marker class `chat/_turn_card.html` emits on every
        card's own root element."""
        conversation = make_conversation(title="a thread")
        make_turn(conversation=conversation, role=Turn.Role.USER, text="hello")
        body = _all(client, selected=conversation.id)
        assert 'class="turn turn-user' in body

    def test_the_selected_row_is_highlighted(self, client):
        agent = make_agent(slug="general")
        one = make_conversation(agent=agent, title="row one")
        make_conversation(agent=agent, title="row two")
        body = _all(client, selected=one.id)
        assert '<tr class="selected"' in body

    def test_an_open_in_full_link_is_offered(self, client):
        conversation = make_conversation(title="a thread")
        body = _all(client, selected=conversation.id)
        assert reverse("chat-conversation", args=[conversation.id]) in body

    def test_a_malformed_selected_value_degrades_to_no_pane_not_a_500(self, client):
        make_conversation(title="a thread")
        response = client.get(reverse("chat-all") + "?selected=not-a-real-uuid")
        assert response.status_code == 200

    def test_a_conversation_this_viewer_cannot_see_selects_nothing(self, client):
        other = make_user()
        theirs = make_conversation(title="not mine", **owner_fields(user_principal(other)))
        viewer = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = _all(client, selected=theirs.id)
        assert '<tr class="selected"' not in body

    def test_selecting_a_row_on_page_two_does_not_bounce_back_to_page_one(self, client):
        agent = make_agent(slug="general")
        conversations = [make_conversation(agent=agent, title=f"conv-{i:03d}")
                         for i in range(60)]
        # Newest first (`-updated_at`): the 51st-created row is the last
        # (oldest) one on page 2.
        target = conversations[0]
        body = _all(client, page=2, selected=target.id)
        assert "Page 2 of 2" in body

    def test_round_13_attachment_chips_render_in_the_preview(self, client):
        """Part 2's own explicit requirement: "attachments chips render
        if round 13 landed them on turns" -- proven with a REAL
        `DocumentAttachment` row, the same shape `test_turn_attachments.
        py::TestChipsOnTheTurnBubble` uses for the full thread page."""
        owner = make_user()
        conversation = make_conversation(**owner_fields(user_principal(owner)))
        turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="see attached",
                         state=Turn.State.DONE)
        doc = make_document(title="Report.pdf", scope=Document.Scope.CONVERSATION,
                            status=Document.Status.READY, **owner_fields(user_principal(owner)))
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id,
                                          turn_id=turn.pk)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = _all(client, selected=conversation.id)
        assert "Report.pdf" in body
        assert "turn-attachment-row" in body


class TestZeroJsAndShareableUrls:
    def test_the_search_form_is_a_plain_get(self, client):
        body = _all(client)
        start = body.index('<form class="search"')
        end = body.index("</form>", start)
        assert 'method="get"' in body[start:end]

    def test_every_filter_and_pagination_control_is_a_plain_anchor(self, client):
        """No `<script>` on this page WHEN THERE IS NO TAGS DROPDOWN TO
        SEARCH (no entitlements offered here, the same as the anonymous
        default posture) -- every control that changes what is shown is
        a real `<a href>` or a GET `<form>`. ROUND 16 (owner feedback:
        "tags for this page should have a drop down with checkboxes
        and search... ") gates its one sanctioned script on `tag_
        options` exactly the way it gates the dropdown itself, so the
        no-tags-offered case pinned here still ships no script of this
        PAGE's own -- `TestTagsDropdown`'s own tests cover the
        tags-offered case, where a second one legitimately renders. The
        one script counted below is the RAIL's (round 21), shared by
        every chat page and about the sidebar's row menus, not about any
        control on this page's body."""
        agent = make_agent(slug="general")
        for i in range(60):
            make_conversation(agent=agent, title=f"conv-{i}")
        body = _all(client)
        assert body.count("<script") == 1
        assert 'rail.addEventListener("toggle"' in body

    def test_a_filtered_url_is_reproducible_directly(self, client):
        """The whole point of a GET filter: the SAME url, fetched a
        second time with no prior state, renders the identical
        content -- once Django's own per-render CSRF-token masking
        (`_strip_csrf`'s own docstring) is normalised out of the
        comparison, which is noise this page's own filtering never
        produced."""
        make_conversation(title="Budget Talk")
        url = reverse("chat-all") + "?q=budget"
        first = _strip_csrf(client.get(url).content.decode())
        second = _strip_csrf(client.get(url).content.decode())
        assert first == second
        assert "Budget Talk" in first


class TestBoundedQueries:
    """Part 2's own explicit enumeration: "one page = list query + tags
    join + (when selected) one bounded preview query", pinned as
    flatness (equality across row counts and across conversation
    sizes), the house idiom (`test_sidebar.py`'s own `_SIDEBAR_
    BASELINE_QUERIES` comment) -- not a fixed count this file would
    have to keep hand-in-sync with the platform's own moving baseline.

    Each measurement is taken TWICE in the SAME test, on two different
    row/size counts, and compared for EQUALITY -- never re-measured and
    compared against itself, which would pass no matter what the view
    did. A throwaway WARM-UP request precedes both measured windows:
    the FIRST request a fresh `Client` ever makes pays a one-off
    session/cookie-establishment cost neither measured window should be
    charged for, or the two windows would differ for a reason that has
    nothing to do with row count.
    """

    def test_the_list_query_count_is_flat_in_row_count(self, client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        agent = make_agent(slug="general")
        make_conversation(agent=agent, title="row one")
        _all(client)  # warm-up: establishes the session/CSRF cookie once
        with CaptureQueriesContext(connection) as small:
            _all(client)

        for i in range(29):
            make_conversation(agent=agent, title=f"row-{i}")
        with CaptureQueriesContext(connection) as large:
            _all(client)

        assert len(large) == len(small)

    def test_selecting_a_row_adds_a_bounded_fixed_amount_regardless_of_conversation_size(
            self, client):
        """Selecting a row costs the SAME extra whether the preview
        turn carries zero attachments or several, and whether the
        conversation holds one turn or many -- the preview path is
        bounded by `PREVIEW_TURN_COUNT`, never by conversation size."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        owner = make_user()
        agent = make_agent(slug="general")
        bare = make_conversation(agent=agent, title="bare",
                                 **owner_fields(user_principal(owner)))
        rich = make_conversation(agent=agent, title="rich",
                                 **owner_fields(user_principal(owner)))
        for i in range(5):
            make_turn(conversation=rich, role=Turn.Role.USER, text=f"turn {i}",
                     index=Turn.next_index(rich))

        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            _all(client)  # warm-up
            with CaptureQueriesContext(connection) as unselected:
                _all(client)
            with CaptureQueriesContext(connection) as bare_selected:
                _all(client, selected=bare.id)
            with CaptureQueriesContext(connection) as rich_selected:
                _all(client, selected=rich.id)

        added_by_bare = len(bare_selected) - len(unselected)
        added_by_rich = len(rich_selected) - len(unselected)
        assert added_by_bare == added_by_rich
        # Non-vacuous: selecting a row really does cost something, so
        # the equality above could not pass merely because both sides
        # are zero.
        assert added_by_bare > 0


class TestRound14ReviewMinors:
    """ROUND-14 REVIEW, four Minors, each its own test."""

    def test_minor_1_total_is_read_from_the_paginator_not_a_second_count_call(self):
        """`paginator.count`, not a separate `rows.count()` -- pinned
        as SOURCE, not as a runtime query-log text match: the rail's
        own sidebar counts happen to render coincidentally-identical
        `COUNT(*)` SQL on this same page (an unrelated, pre-existing
        cost this Minor never touched), which makes a query-LOG-based
        assertion unable to isolate `/chat/all/`'s OWN duplicate from
        that unrelated one. What the fix actually changed -- THIS
        function no longer calls `.count()` on `rows` itself -- is what
        this test checks directly."""
        import inspect

        from agents.chat.views.all_conversations import _all_context

        source = inspect.getsource(_all_context)
        # `= rows.count()` -- the LIVE assignment the fix removed --
        # never a bare substring match, which would also catch this
        # function's own explanatory comment about the fix itself.
        assert "= rows.count()" not in source
        assert "total = paginator.count" in source

    def test_minor_2_archived_title_and_h1_agree(self, client):
        make_conversation(title="put away", archived_at=timezone.now())
        body = _all(client, archived=1)
        assert "<title>Archived chats — farabunker</title>" in body
        assert "<h1>Archived chats</h1>" in body

    def test_minor_2_the_active_title_and_h1_also_agree(self, client):
        """THE OTHER DIRECTION: the fix must not have hard-coded
        "Archived chats" into the title unconditionally."""
        body = _all(client)
        assert "<title>All chats — farabunker</title>" in body
        assert "<h1>All chats</h1>" in body

    def test_minor_3_the_updated_header_states_what_it_actually_tracks(self, client):
        body = _all(client)
        start = body.index("<th title=")
        header = body[start:body.index("</th>", start) + len("</th>")]
        assert "Updated" in header
        assert "created, renamed, or archived" in header
        assert "most recent message" in header

    def test_minor_4_the_filtered_cue_appears_only_while_a_filter_narrows_the_table(
            self, client):
        # The note's own TEXT, never the bare class name: the class
        # name alone also matches this page's own `<style>` block
        # (`.all-filtered-note { ... }`), which is ALWAYS present
        # regardless of whether the note itself renders.
        marker = "Showing filtered results below"
        make_conversation(title="one")
        assert marker not in _all(client)
        assert marker in _all(client, q="one")
        assert marker in _all(client, archived=1)

    def test_nit_2_a_tool_heavy_exchange_still_shows_the_final_answer_in_preview(self, client):
        """PREVIEW_TURN_COUNT now excludes TOOL turns from the SLICE
        (not merely the render) -- an exchange whose last several
        depth=0 rows are TOOL turns must still surface the actual
        USER/ASSISTANT text in the preview pane, not an empty-looking
        pane crowded out by audit-trail rows."""
        conversation = make_conversation(title="a tool-heavy thread")
        make_turn(conversation=conversation, role=Turn.Role.USER, text="do the thing",
                 index=Turn.next_index(conversation))
        for i in range(4):
            make_turn(conversation=conversation, role=Turn.Role.TOOL, text=f"tool result {i}",
                     index=Turn.next_index(conversation))
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="done, here it is",
                 index=Turn.next_index(conversation), state=Turn.State.DONE)
        body = _all(client, selected=conversation.id)
        assert "done, here it is" in body


class TestDeltaReviewPreviewPaneConsistency:
    """DELTA WHOLE-BRANCH REVIEW (2d8719c..4e9a37e), Minor 2: the detach
    control in the preview pane used to redirect the operator OUT of
    `/chat/all/` and into the conversation. (Minor 3, the legacy-
    attachments block's own absence from the preview, is HISTORY --
    the whole legacy renderer it named was removed by the consolidation
    wave's own S10 ruling, production-unreachable.)"""

    def test_minor_2_detaching_from_the_preview_returns_to_chat_all(self, client):
        owner = make_user()
        conversation = make_conversation(**owner_fields(user_principal(owner)))
        turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="see attached",
                         state=Turn.State.DONE)
        doc = make_document(title="Report.pdf", scope=Document.Scope.CONVERSATION,
                            status=Document.Status.READY, **owner_fields(user_principal(owner)))
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id,
                                          turn_id=turn.pk)
        preview_url = reverse("chat-all") + f"?selected={conversation.id}"
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(preview_url).content.decode()
            # The detach form inside the preview carries the preview's
            # own URL as `next` — proven by source before proving by
            # behaviour, so a failure here points straight at the
            # template rather than at the redirect logic below.
            assert f'value="{preview_url}"' in body
            detach_url = reverse("chat-attachment-detach", args=[conversation.id, doc.id])
            response = client.post(detach_url, {"next": preview_url})
        assert response.status_code == 302
        assert response.url == preview_url
        assert not DocumentAttachment.objects.filter(document=doc).exists()

    def test_minor_2_the_ordinary_thread_page_chip_is_unaffected(self, client):
        """NON-VACUOUS OTHER DIRECTION: a detach posted with no `next`
        at all (the ordinary thread-page chip's own form, which carries
        none) still falls back to the conversation, exactly as before
        this fix."""
        owner = make_user()
        conversation = make_conversation(**owner_fields(user_principal(owner)))
        turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="see attached",
                         state=Turn.State.DONE)
        doc = make_document(title="Report.pdf", scope=Document.Scope.CONVERSATION,
                            status=Document.Status.READY, **owner_fields(user_principal(owner)))
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id,
                                          turn_id=turn.pk)
        detach_url = reverse("chat-attachment-detach", args=[conversation.id, doc.id])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(detach_url)
        assert response.status_code == 302
        assert response.url == reverse("chat-conversation", args=[conversation.id])

    def test_minor_2_an_off_site_next_from_the_preview_is_ignored(self, client):
        owner = make_user()
        conversation = make_conversation(**owner_fields(user_principal(owner)))
        turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="see attached",
                         state=Turn.State.DONE)
        doc = make_document(title="Report.pdf", scope=Document.Scope.CONVERSATION,
                            status=Document.Status.READY, **owner_fields(user_principal(owner)))
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id,
                                          turn_id=turn.pk)
        detach_url = reverse("chat-attachment-detach", args=[conversation.id, doc.id])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(detach_url, {"next": "https://example.invalid/steal"})
        assert response.status_code == 302
        assert response.url == reverse("chat-conversation", args=[conversation.id])

    def test_the_turn_card_fragment_includes_the_one_chip_fragment(self):
        """CONSOLIDATION WAVE (S10 ruling) NARROWS THIS FROM "both
        callers share the fragment" (`_turn_card.html` and the now-
        removed `_legacy_attachments.html`) TO ITS OWN SURVIVING HALF:
        `_turn_card.html` still includes `chat/_attachment_chip.html`
        -- proven at the source level, the shape this session's own
        "no headless browser" convention already uses for a structural
        claim rather than a rendered-pixel one. Resolved through
        Django's own template loader, never a relative filesystem path,
        so this passes regardless of the process's own working
        directory."""
        from django.template import loader

        turn_card_source = loader.get_template("chat/_turn_card.html").origin.name
        assert 'include "chat/_attachment_chip.html"' in open(turn_card_source).read()
