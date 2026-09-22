"""The thread page. NO FARABUNKER_FEATURES OVERRIDE (test_mount.py)."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from agents.chat.tests._helpers import (   # noqa: F401
    bound_chat_role, fake_queued_job, fake_running_job, grant, make_admin, make_agent,
    make_conversation, make_entitlement, make_thread, make_turn, make_user, posture,
    sign_in, user_principal,
)
from agents.models import Share, ToolEntitlement, ToolInvocation, Turn, WorkstreamScopeEntitlement
from agents.tests._helpers import _workstream, make_document
from agents.visibility import create_conversation
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE
from tools.rag.models import Document, DocumentAttachment, DocumentEntitlement

pytestmark = pytest.mark.django_db


class TestTheThread:
    def test_it_renders_the_whole_conversation_in_order(self, client):
        conversation = make_thread()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        # Scoped to the thread region, not the whole document: the shared
        # shell's own `<head>` CSS (`foundation/templates/_shell.html`'s
        # `--chip-bg`) contains the literal substring "hi", which would
        # otherwise make ANY unscoped `body.index("hi")` match inside the
        # stylesheet rather than inside a rendered turn.
        thread_start = body.index('class="thread"')
        assert body.index("hello", thread_start) < body.index("hi", thread_start)

    def test_an_unknown_conversation_is_a_404_not_a_500(self, client):
        response = client.get(
            reverse("chat-conversation", args=[uuid.uuid4()])
        )
        assert response.status_code == 404

    def test_the_page_names_its_agent(self, client):
        conversation = make_conversation(agent=make_agent(name="General assistant"))
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "General assistant" in body

    def test_the_message_form_is_a_plain_post_that_needs_no_javascript(self, client):
        """Progressive enhancement, not JS-dependence: the form must be
        complete and submittable before a single script runs."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert f'action="{reverse("chat-turn", args=[conversation.id])}"' in body
        assert 'method="post"' in body
        assert "csrfmiddlewaretoken" in body

    def test_the_picked_connection_survives_into_the_form(self, client):
        """Deviation P3-D7: `?connection=` is what makes the redirect
        after a submission and a bookmark agree."""
        conversation = make_conversation()
        url = reverse("chat-conversation", args=[conversation.id])
        body = client.get(url, {"connection": "3"}).content.decode()
        assert 'name="connection"' in body

    def test_the_browser_tab_names_the_conversation(self, client):
        """U5: `<title>` used to show only the agent's name; a person
        with several threads open could not tell them apart by tab."""
        conversation = make_conversation(title="hello there")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "<title>hello there — Chat — farabunker</title>" in body

    def test_an_untitled_conversation_falls_back_to_the_agent_name_in_the_tab(
        self, client
    ):
        conversation = make_conversation(agent=make_agent(name="General assistant"))
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "<title>General assistant — Chat — farabunker</title>" in body

    def test_a_disabled_agents_past_conversation_still_renders(self, client):
        """`ConversationView` reads `conversation.agent` directly, never
        through `visible_agents` (which applies `enabled=True`) --
        `enabled` is a STARTABILITY rule for the index and the start
        form, not a readability rule for a thread that already exists.
        A disabled agent's past conversation must still be a 200 that
        names the agent, never a 404 and never a 500."""
        agent = make_agent(name="Retired assistant", enabled=False)
        conversation = make_conversation(agent=agent)
        response = client.get(reverse("chat-conversation", args=[conversation.id]))
        assert response.status_code == 200
        assert "Retired assistant" in response.content.decode()


class TestAnswerMarkdown:
    """U2: the rendered page's counterpart of `test_rendering.py::
    TestRenderAnswer` -- proving `render_answer`'s output actually
    reaches an assistant turn's card on the page, not just the function
    in isolation."""

    def test_assistant_markdown_renders_and_a_script_tag_stays_escaped(self, client):
        conversation = make_conversation()
        make_turn(
            conversation=conversation, role=Turn.Role.ASSISTANT, state=Turn.State.DONE,
            text="**attention.pdf** says <script>alert(1)</script>\n\n- a point",
        )
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "<strong>attention.pdf</strong>" in body
        assert "<li>a point</li>" in body
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body


class TestThePoller:
    def test_the_page_carries_the_polling_constants_from_python(self, client):
        """Declared once, in the view, so the page and its tests can never
        disagree about the ceiling."""
        from agents.chat.service import (
            MAX_POLL_DURATION_MS, MAX_TRANSPORT_RETRIES, POLL_INTERVAL_MS,
        )

        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert f"var POLL_INTERVAL_MS = {POLL_INTERVAL_MS};" in body
        assert f"var MAX_TRANSPORT_RETRIES = {MAX_TRANSPORT_RETRIES};" in body
        assert f"var MAX_POLL_DURATION_MS = {MAX_POLL_DURATION_MS};" in body

    def test_the_give_up_notes_minutes_are_derived_and_its_settings_link_is_real(
        self, client
    ):
        """The give-up note's `POLL_DURATION_MINUTES` is computed in the
        inline script from `MAX_POLL_DURATION_MS` (the template's own
        comment: "never a hardcoded '10 minutes'"), so the note-building
        code must never carry that literal string -- and the note's hint
        points at a REAL `reverse('settings-index')` target, not a typed
        path that could rot out from under it."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        # Scoped past `function poll(`: the top-of-script comment
        # explaining WHY the note is derived rather than typed quotes the
        # literal phrase "10 minutes" as the example of what NOT to
        # hardcode, which would otherwise make an unscoped search fail on
        # the comment itself rather than catching a real regression in
        # the note-building code below it.
        note_region = body[body.index("function poll("):]
        assert "10 minutes" not in note_region
        assert reverse("settings-index") in body

    def test_the_poll_cap_stays_above_the_settings_max_response_timeout(self):
        """I-5 (final review, fix round 3): `MAX_POLL_DURATION_MS` must
        stay strictly greater than `JobSettings.RESPONSE_TIMEOUT_SECONDS_
        MAX * 1000`, or the poller re-arms fix round 1's own I3 defect
        the moment either number moves -- a comment alone does not fail
        a build when someone edits just one side of it. A TEST module
        may import both `agents.chat.service` and `models.queue.models`;
        the PRODUCTION modules on either side still may not (import
        law) -- this pin lives here, beside the poller's own constants,
        rather than in either production module."""
        from agents.chat.service import MAX_POLL_DURATION_MS
        from models.queue.models import JobSettings

        assert MAX_POLL_DURATION_MS > JobSettings.RESPONSE_TIMEOUT_SECONDS_MAX * 1000

    def test_the_pending_turn_id_from_the_no_js_redirect_reaches_the_script(self, client):
        """`?pending=<id>` is what makes the JS-off redirect and the JS-on
        poller converge: the page renders the turn as pending either way,
        and with a script running it starts watching it immediately."""
        conversation = make_conversation()
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.QUEUED, queue_job_id=1)
        url = reverse("chat-conversation", args=[conversation.id])
        body = client.get(url, {"pending": turn.pk}).content.decode()
        assert f'data-pending-turn="{turn.pk}"' in body
        assert (
            f'data-pending-status-url="{reverse("chat-turn-status", args=[turn.pk])}"'
            in body
        )

    def test_a_malformed_pending_value_renders_200_with_no_poller_target(self, client):
        """Review fix wave item 1: `?pending=` is a raw query-string read
        (`thread.py::thread_context`), not a path segment the URL
        resolver ever validates. A non-digit or negative value must
        never reach `reverse('chat-turn-status', ...)` -- it used to,
        via the template's own `{% url %}` call, and a bad value there
        is a `NoReverseMatch`, i.e. a 500."""
        conversation = make_conversation()
        url = reverse("chat-conversation", args=[conversation.id])
        for bad in ("abc", "-1"):
            response = client.get(url, {"pending": bad})
            assert response.status_code == 200
            body = response.content.decode()
            # Scoped to the thread region, not the whole document: the
            # poller's own inline script (`{% block scripts %}`) names
            # the attribute in a comment, which would otherwise make an
            # unscoped search find the word rather than the attribute
            # (the same scoping `test_a_user_turn_carries_no_poll_block_
            # marker`, elsewhere in this module, uses).
            thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
            assert "data-pending-status-url" not in thread_region
            assert 'data-pending-turn=""' in thread_region

    def test_a_card_already_on_the_page_carries_the_same_poll_block_as_its_pending_sibling(
        self, client
    ):
        """Review round 2 regression pin. A tool call that finishes
        while its assistant turn is still queued/running writes a DONE
        tool turn well before the job's own done body ever renders --
        `agents/runtime/loop.py` stamps every row a job writes with the
        SAME `queue_job_id`, so a full-thread render (this GET) already
        shows that tool card beside the still-pending assistant card.
        The poller's "done" swap later replaces the pending card with
        the WHOLE job block (M6), which includes that SAME tool card
        again -- and without a shared marker to dedupe on, the browser
        was left holding two copies of it. `data-poll-block` is that
        marker: present, and IDENTICAL, on every card sharing a
        `queue_job_id`, which is what lets the script find and remove
        every stale card for a job rather than just the one placeholder
        it was polling."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.TOOL,
                  text="two results", state=Turn.State.DONE, queue_job_id=7,
                  tool_call={"tool": "rag.search", "args": {}, "agent": "general",
                             "id": "", "discarded": []})
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.RUNNING, queue_job_id=7)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert body.count('data-poll-block="7"') == 2
        assert f'data-turn-id="{turn.pk}"' in body

    def test_a_user_turn_carries_no_poll_block_marker(self, client):
        """The USER turn never gets a `queue_job_id` (the view writes
        it before any job exists -- M6's own guard elsewhere), so it
        must never carry a marker a job's cards could be mistaken to
        share -- UNLESS a job actually follows it (the next test):
        this pin is specifically about a USER turn with no assistant
        turn after it at all."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER, text="hi")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        # Scoped to the thread region: the poller's own inline script
        # (this page's `{% block scripts %}`) names the attribute in a
        # comment, which would otherwise make an unscoped search find
        # the word rather than the marker.
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert "data-poll-block" not in thread_region

    def test_the_full_render_and_the_polled_fragment_share_one_poll_block_marker(
        self, client
    ):
        """R1 (chat-polish P3.1, fix round 1) -- THE regression this
        pins directly. Before this fix, a full-thread render (this GET)
        stamped `data-poll-block` on the pending ASSISTANT card only;
        `turn_status`'s own polled fragment (`rendering.
        turn_group_cards`) always stamped it on the USER card too. That
        asymmetry is exactly what let a mid-turn reload -- or the no-JS
        `?pending=` redirect -- leave TWO copies of the user's bubble
        once a poll swap landed: the script's dedup-by-marker swap could
        never find the server-rendered USER card, because it carried no
        marker to find. Both renders now agree BY CONSTRUCTION
        (`thread_cards`'s own docstring has the mechanism), which is
        what a browser-level "reload mid-turn, then let the poller swap
        the group in" check would show as exactly one user bubble."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER,
                  text="what is the paper about?", state=Turn.State.DONE)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.RUNNING, queue_job_id=1)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        # Exactly two cards share the marker: the user's own bubble and
        # the still-running assistant placeholder -- the SAME count
        # `turn_status`'s own fragment produces for this exact turn
        # (`test_turn_status.py::
        # test_the_queued_and_running_blocks_share_one_poll_block_marker_
        # with_the_user_card`).
        assert thread_region.count('data-poll-block="1"') == 2
        assert f'data-turn-id="{turn.pk}"' in thread_region

    def test_a_finished_exchange_also_carries_the_shared_marker(self, client):
        """The same parity holds for an ALREADY-DONE turn -- a plain
        reload with no `?pending=` at all still renders the user card
        with the marker its own job's cards carry, so a stray later poll
        (or a second tab still watching the same turn) swaps correctly
        rather than assuming a done turn's page render is unmarked."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER,
                  text="what is the paper about?", state=Turn.State.DONE)
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  text="a summary", state=Turn.State.DONE, queue_job_id=1)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert thread_region.count('data-poll-block="1"') == 2

    def test_a_refreshed_page_carries_the_status_url_and_seeded_note_for_a_pending_card(
        self, client
    ):
        """chat-resume-poll. A plain reload mid-turn -- no `?pending=`
        at all -- must still let the bootstrap resume polling on its
        own: `_turn_card.html` seeds `data-status-url` (`{% url
        'chat-turn-status' %}`, never a hardcoded path) and a "Working…"
        note on the pending card, which is exactly the shape
        `conversation.html`'s bootstrap scans for
        (`.turn-pending[data-status-url]`). The USER card of the same
        in-flight group must carry none of it -- only ASSISTANT turns
        are ever pending (`rendering.turn_card`'s own docstring)."""
        conversation = make_conversation()
        user_turn = make_turn(conversation=conversation, role=Turn.Role.USER,
                              text="hi", state=Turn.State.DONE)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.QUEUED, queue_job_id=1)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        status_url = reverse("chat-turn-status", args=[turn.pk])
        assert f'data-status-url="{status_url}"' in body
        assert "Working…" in body
        # Scoped to the USER card's own opening tag -- from its
        # `data-turn-id` attribute through the tag's closing `>` -- so
        # this proves the attribute is absent from THAT card specifically,
        # not merely that the page as a whole has only one occurrence.
        user_tag_start = body.index(f'data-turn-id="{user_turn.pk}"')
        user_tag_end = body.index(">", user_tag_start)
        assert "data-status-url" not in body[user_tag_start:user_tag_end]

    def test_a_done_assistant_turn_carries_no_status_url_or_seeded_note(self, client):
        """The counterpart of the test above: once a turn is DONE,
        `card.pending` is false, so neither the attribute nor the
        seeded note has any reason to render."""
        conversation = make_thread()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        # Scoped to the thread region, not the whole document: the
        # poller's own inline script (`{% block scripts %}`) names both
        # strings in its comments, which would otherwise make an
        # unscoped search find the words rather than rendered markup
        # (the same scoping several `TestThePoller` tests above use).
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert "data-status-url" not in thread_region
        assert "no-js-note" not in thread_region

    def test_the_bootstrap_scans_for_pending_cards_carrying_a_status_url(self, client):
        """String-pinning the bootstrap's own querySelectorAll literal,
        the same style `test_the_page_carries_the_polling_constants_
        from_python` above uses for the poll tuning constants -- this
        is what makes a plain refresh resume polling without `?pending=`
        (chat-resume-poll)."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '.turn-pending[data-status-url]' in body

    def test_the_bootstrap_no_longer_carries_a_second_wrapper_only_path(
        self, client
    ):
        """chat-poller-cleanup: the scan above is the sole starter now --
        the wrapper's own `data-pending-turn` branch, and the
        `startedTurnIds` bookkeeping that used to keep it from racing
        the scan over the same card, are both gone. The wrapper's
        `data-pending-turn`/`data-pending-status-url` ATTRIBUTES stay
        (pinned by `test_the_pending_turn_id_from_the_no_js_redirect_
        reaches_the_script` above) -- only the now-redundant script
        branch reading them is removed."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "startedTurnIds" not in body

    def test_the_unchanged_html_guard_is_present(self, client):
        """chat-poller-cleanup: every 2s tick on a still-queued/running
        turn used to re-swap the SAME group `html` even when nothing had
        changed, snapping every open `<details>` in it shut and
        re-creating every `<img>` in it from scratch. `lastHtml` is the
        guard against that -- string-pinned the same style
        `test_the_bootstrap_scans_for_pending_cards_carrying_a_status_
        url` above pins the scan's own literal."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "var lastHtml = null;" in body
        assert "if (data.html && data.html !== lastHtml) {" in body


class TestEnterSubmits:
    """Owner-requested UX fix: in the composer, Enter submits and
    Shift+Enter inserts a newline. Template-presence pins, the same
    shape `TestThePoller.test_the_page_carries_the_polling_constants_
    from_python` above uses for the poller's own inline script -- there
    is no browser here to actually press Enter, so this pins the
    handler's source rather than its runtime effect."""

    def test_the_composer_still_carries_required_for_the_no_js_and_js_paths_alike(
        self, client
    ):
        """`required` is what blocks an empty composer either way --
        clicking Send or pressing Enter (`form.requestSubmit()` runs the
        SAME native validation a real click would, per MDN's own
        description of the method) -- so Enter introduces no new way to
        submit a blank message."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<textarea name="text" required' in body

    def test_the_script_wires_a_document_delegated_keydown_handler(
        self, client
    ):
        """`document.addEventListener`, not a parse-time `textarea.
        addEventListener` (final review, m1): the handler is delegated
        so it survives a composer swap the settings assistant panel
        performs on other pages."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert 'document.addEventListener("keydown"' in body

    def test_shift_enter_is_left_alone(self, client):
        """Shift+Enter must return before anything else runs -- the
        newline stays the default `<textarea>` behaviour."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "event.shiftKey" in body

    def test_an_ime_composition_enter_is_not_treated_as_submit(self, client):
        """`isComposing` plus the classic `keyCode === 229` fallback --
        an IME's own Enter-to-confirm keystroke must never submit the
        form out from under someone still composing text."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "event.isComposing" in body
        assert "event.keyCode === 229" in body

    def test_enter_submits_via_requestsubmit_not_submit(self, client):
        """`requestSubmit()`, never `submit()`: only the former fires the
        `submit` event this page's own listener (and native validation)
        depend on -- `submit()` would bypass both."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "form.requestSubmit();" in body
        assert "form.submit();" not in body

    def test_a_disabled_send_button_is_checked_before_requestsubmit_is_called(
        self, client
    ):
        """`requestSubmit()` called with no argument sets `submitter` to
        the form element itself, never one of its buttons, so it never
        consults a button's own `disabled` attribute -- verified against
        the spec's `requestSubmit(submitter)` algorithm, not assumed.
        Without this explicit check, Enter would still submit while
        `setFormBusy(true)` (U4, a turn already running) has disabled
        the Send button."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "if (button && button.disabled) { return; }" in body


class TestTheAppShellStructure:
    """Owner-requested chat layout fix, review round: the new markup
    (`.thread-scroll` wrapping the transcript, with the composer kept
    OUTSIDE it) had no pins of its own -- these are the structure
    equivalent of `TestEnterSubmits` above, string-slicing the rendered
    page the same way `TestThePoller`'s own `thread_region` pins do.

    ROUND 9 NARROWED `.thread-scroll` FURTHER: it used to also hold the
    share panel and the delete disclosure (UX point 4's "reachable by
    scrolling past the transcript"); both moved out to `.thread-actions`
    below the composer once sharing stopped being in-flow content at
    all (owner feedback: "the share shouldn't be a main feature ... a
    suble side feature"). `TestTheQuietActionsRow` below is where that
    new row's own structure is pinned; this class keeps only the claim
    that survived -- `.thread-scroll` holds the transcript and nothing
    else."""

    def test_the_scroll_region_holds_only_the_transcript_now(self, client):
        """`.thread-scroll` is the ONE scrolling region on this page
        (`chat_style`'s `flex: 1 1 auto; overflow-y: auto`). `POSTURE_
        ENTERPRISE` + the owner signed in is what WOULD make the share
        panel render at all (`may_see_shares and identity_posture !=
        "open"`) -- used here (the same setup `test_conversation_share.
        py::TestThePanel.test_the_owner_sees_the_share_list_and_a_
        recipient_does_not` uses) so "absent from this region" is not
        silently vacuous for a viewer the panel would never render to
        regardless."""
        owner = make_user()
        agent = make_agent()
        conversation = create_conversation(user_principal(owner), agent)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()

        assert '<div class="thread-scroll">' in body
        assert "Shared with" in body  # the panel DOES render -- just not here
        region = body[body.index('class="thread-scroll"'):body.index('id="turn-errors"')]
        assert 'class="thread"' in region
        assert "Shared with" not in region
        assert "Delete this conversation" not in region

    def test_the_composer_sits_outside_the_scrolling_region(self, client):
        """UX point 2: the composer must never be inside anything that
        scrolls. `id="turn-form"` appearing nowhere in the slice up to
        `id="turn-errors"` -- the scroll region's own end boundary, the
        same landmark `TestThePoller`'s `thread_region` pins already
        trust -- is what proves the form is not nested inside
        `.thread-scroll`, not merely that it renders somewhere on the
        page."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        region = body[body.index('class="thread-scroll"'):body.index('id="turn-errors"')]
        assert 'id="turn-form"' not in region
        assert 'id="turn-form"' in body
        assert body.index('id="turn-errors"') < body.index('id="turn-form"')


class TestTheStayAtBottomGuard:
    """UX point 1: on load the transcript opens scrolled to its newest
    content, and a poll tick that appends turns keeps it pinned to the
    bottom only while the reader is already there. Template-presence
    pins, the same shape `TestEnterSubmits` above uses for the OTHER
    half of this page's one sanctioned script -- there is no browser
    here to actually scroll, so this pins the guard's source rather
    than its runtime effect."""

    def test_the_guard_functions_are_defined(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "function isNearThreadBottom() {" in body
        assert "function scrollThreadToBottom() {" in body
        assert "function threadBottomOffset() {" in body

    def test_both_poll_branches_read_the_guard_before_swapping_and_repin_after(
        self, client
    ):
        """The `queued`/`running` branch and the `done` branch each
        re-swap the SAME card (D1's own doc comment above `swapBlock`
        already pins that a tool call finishing mid-turn does this more
        than once), so BOTH need the read-before/re-pin-after pattern,
        not just the final `done` swap -- pinned as an exact count of
        two, one per branch."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert body.count("var wasNearBottom = isNearThreadBottom();") == 2
        assert body.count("if (wasNearBottom) { scrollThreadToBottom(); }") == 2

    def test_the_page_load_and_the_just_sent_message_both_pin_unconditionally(
        self, client
    ):
        """Two unconditional callers, neither gated on
        `isNearThreadBottom()`: the bootstrap (a fresh load has no
        earlier reader position to preserve) and the submit handler's
        `insertBlock()` path (sending a message IS the reader saying
        "I'm at the bottom"). Four bare `scrollThreadToBottom();` calls
        total across the script -- these two plus the two guarded ones
        the test above already counts, since `if (wasNearBottom) {
        scrollThreadToBottom(); }` also contains the same literal
        substring."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert body.count("scrollThreadToBottom();") == 4


class TestTheNoScriptHint:
    """D3: with scripts off, nothing ever swaps a running card out, so
    "Working..." would otherwise read as stuck forever."""

    def test_a_running_turn_carries_the_hint(self, client):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.RUNNING, queue_job_id=1)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "<noscript>" in body
        assert "does not update on its own" in body

    def test_a_queued_turn_carries_the_hint_too(self, client):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.QUEUED, queue_job_id=1)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "<noscript>" in body

    def test_a_finished_thread_carries_no_hint(self, client):
        conversation = make_thread()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "<noscript>" not in body

    def test_a_queued_placeholder_seeds_queued_not_working(self, client):
        """chat-poller-cleanup: the seed line is state-aware -- the
        server knows a queued turn is not yet "working", so a JS-off
        reader (who only ever sees this line, permanently, alongside
        `<noscript>`) must not be told something not true yet. Scoped
        to the thread region, not the whole document: the poller's own
        inline script names both strings in its comments and its
        `data.state` branch, which would otherwise make an unscoped
        search find the words there instead of the rendered seed (the
        same scoping `test_a_done_assistant_turn_carries_no_status_url_
        or_seeded_note` above uses)."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.QUEUED, queue_job_id=1)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert '<p class="muted no-js-note">Queued…</p>' in thread_region
        assert '<p class="muted no-js-note">Working…</p>' not in thread_region

    def test_a_running_placeholder_seeds_working(self, client):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.RUNNING, queue_job_id=1)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert '<p class="muted no-js-note">Working…</p>' in thread_region
        assert '<p class="muted no-js-note">Queued…</p>' not in thread_region


class TestToolCardsOnThePage:
    def _tool_turn(self, conversation, **overrides):
        invocation = ToolInvocation.objects.create(
            principal_kind="resident_agent", principal_key="general",
            tool_key="rag.search", outcome=ToolInvocation.Outcome.OK,
            text="two results", finished_at=timezone.now(),
        )
        fields = dict(
            conversation=conversation, role=Turn.Role.TOOL, text="two results",
            state=Turn.State.DONE, invocation=invocation,
            tool_call={"tool": "rag.search", "args": {"query": "attention"},
                       "agent": "general", "id": "", "discarded": []},
        )
        fields.update(overrides)
        return make_turn(**fields)

    def test_a_citation_links_to_the_document_file_view(self, client):
        """Spec section 8.4: citations link to `rag-document-file`."""
        conversation = make_conversation()
        self._tool_turn(conversation,
                        data={"citations": [{"document_id": "7", "title": "a.pdf",
                                             "locator_text": ", p. 5", "score": 0.8}]})
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert reverse("rag-document-file", args=[7]) in body
        assert "a.pdf" in body and ", p. 5" in body

    def test_a_vision_output_renders_as_an_img_pointing_at_the_output_view(
        self, client
    ):
        """Spec section 8.4: an `output:<id>` artifact is an `<img>`
        whose src is `vision-output-file`, which streams strictly by pk
        and clamps its Content-Type -- so the chat never learns or
        exposes a filesystem path."""
        conversation = make_conversation()
        self._tool_turn(conversation, artifacts=["output:12"],
                        tool_call={"tool": "vision.generate", "args": {},
                                   "agent": "general", "id": "", "discarded": []})
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert f'<img src="{reverse("vision-output-file", args=[12])}"' in body

    def test_a_document_artifact_renders_as_a_files_link_on_the_answer_card(
        self, client
    ):
        """CQ-13, updated by fix round 1's R6: `turn_card`/`tool_card`
        compute `files` from a `document:<id>` artifact (`rag.tools.py`
        mints one for its source documents), and it renders on the
        ANSWER (assistant) card -- never the tool card, which would
        show the SAME document a second time (a turn's `artifacts` is
        the union of every tool call the job made,
        `agents/runtime/loop.py`'s own `_finish`). R5: the link text is
        the document's own title when one was minted, else a numbered
        placeholder -- "document:7" (the bare reference) is never shown
        to a reader."""
        conversation = make_conversation()
        self._tool_turn(conversation, artifacts=["document:7"],
                        tool_call={"tool": "rag.search", "args": {"query": "x"},
                                   "agent": "general", "id": "", "discarded": []})
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.DONE, text="Here is what I found.",
                  artifacts=["document:7"])
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert f'<a href="{reverse("rag-document-file", args=[7])}">Document 7</a>' in body
        assert ">Files<" in body

    def test_a_document_artifact_does_not_also_render_on_the_tool_card(
        self, client
    ):
        """R6: the SAME reference sits on both rows in practice (the
        loop unions every tool call's artifacts onto the final
        ASSISTANT turn) -- the file link must appear exactly once, on
        the answer card, never a second time on the tool card that
        cited it."""
        conversation = make_conversation()
        self._tool_turn(conversation, artifacts=["document:7"],
                        tool_call={"tool": "rag.search", "args": {"query": "x"},
                                   "agent": "general", "id": "", "discarded": []})
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.DONE, text="Here is what I found.",
                  artifacts=["document:7"])
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert body.count(reverse("rag-document-file", args=[7])) == 1

    def test_a_source_path_in_the_citation_data_never_reaches_the_page(
        self, client
    ):
        """The rendered counterpart of `test_rendering.py`'s
        `test_source_path_never_reaches_the_card`. `run_ask` hands its
        citation dicts back unfiltered and they really do carry a
        server filesystem path; the page is the last place that could
        leak one, so it is asserted where a person would see it."""
        conversation = make_conversation()
        self._tool_turn(conversation,
                        data={"citations": [{"document_id": "7", "title": "a.pdf",
                                             "source_path": "/srv/farabunker/media/a.pdf",
                                             "score": 0.8}]})
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "a.pdf" in body
        assert "/srv/farabunker" not in body

    def test_the_tool_arguments_are_shown(self, client):
        conversation = make_conversation()
        self._tool_turn(conversation)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "attention" in body

    def test_a_long_tool_result_collapses_behind_a_details_disclosure(self, client):
        """U1: a long `Search the library` result used to dump the whole
        concatenated text above its own citation list. Now it sits
        behind a closed-by-default `<details>`, and the citation ABOVE
        it is what a reader sees first."""
        conversation = make_conversation()
        self._tool_turn(
            conversation, text="x" * 500,
            data={"citations": [{"document_id": "7", "title": "a.pdf",
                                 "locator_text": "", "score": 0.8}]},
        )
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert "<details class=\"tool-output\">" in thread_region
        assert "<summary>Show output</summary>" in thread_region
        # The citation sits BEFORE the (collapsed) output in the markup.
        assert thread_region.index("a.pdf") < thread_region.index("tool-output")

    def test_a_short_tool_result_is_not_collapsed(self, client):
        conversation = make_conversation()
        self._tool_turn(conversation, text="two results")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert "tool-output" not in thread_region

    def test_null_arguments_sit_behind_an_unused_arguments_disclosure(self, client):
        """R3 (fix round 1): "Unused arguments (N)", not "All arguments
        (N)" -- N is the HIDDEN row count, and the old label read as if
        it counted every argument the call declared."""
        conversation = make_conversation()
        self._tool_turn(
            conversation,
            tool_call={"tool": "vision.generate",
                       "args": {"prompt": "a cat", "seed": None, "mask": ""},
                       "agent": "general", "id": "", "discarded": []},
        )
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert "a cat" in thread_region
        assert "Unused arguments (2)" in thread_region
        assert "All arguments" not in thread_region
        # Scoped: the disclosure exists and holds the null rows, rather
        # than the null rows also leaking into the always-visible `dl`.
        details_start = thread_region.index("tool-args-all")
        assert "seed" not in thread_region[:details_start]
        assert "seed" in thread_region[details_start:]

    def test_a_running_tool_call_says_so_rather_than_showing_an_error(self, client):
        """P2 ledger, end to end through the template this time."""
        conversation = make_conversation()
        invocation = ToolInvocation.objects.create(
            principal_kind="resident_agent", principal_key="general",
            tool_key="rag.search", outcome=ToolInvocation.Outcome.ERROR,
        )
        self._tool_turn(conversation, invocation=invocation, text="")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "still running" in body.lower()


class TestImagesStayInsideTheirCard:
    """ROUND 2, item 3, from the owner's live proof: an image in an
    assistant reply painted straight out through the card's right edge.

    THE CAUSE WAS A MISSING WRAPPER, not a missing markdown rule.
    `_tool_card.html` wraps its `_artifact_images.html` include in
    `.tool-images`, which carried the `max-width`; `_turn_card.html`
    includes the SAME fragment for an ANSWER card's images with no
    wrapper, so those `<img>`s inherited no constraint at all. The guard
    is scoped to `.turn` now, which is what makes it cover the answer
    card, the tool card, a nested delegated turn, and the next include
    that forgets a wrapper.

    (`render_answer` never emits an `<img>` -- it escapes first and
    recognises only bold/em/code/quote/list/fence -- so literal markdown
    image syntax in a reply stays literal text. The images an assistant
    turn really shows are its artifacts, which is what this covers.)

    Paint cannot be unit-tested; the declaration's presence can, the way
    `models/registry/tests/test_views.py` pins its own overflow guard.
    """

    def test_the_stylesheet_caps_every_image_in_a_turn(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert ".turn img { max-width: 100%; height: auto; }" in body

    def test_an_answer_cards_own_image_sits_inside_a_turn_so_the_cap_reaches_it(
        self, client,
    ):
        """The half a stylesheet assertion cannot make on its own: the
        unwrapped `<img>` really does render inside `<article class="turn
        ...">`, so `.turn img` governs it. Without this, the rule above
        could stay pinned while sitting on a selector that matches
        nothing on the page."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  text="here is the picture", state=Turn.State.DONE,
                  artifacts=["output:12"])

        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()

        card_start = body.index('<article class="turn turn-assistant"')
        card = body[card_start:body.index("</article>", card_start)]
        assert f'<img src="{reverse("vision-output-file", args=[12])}"' in card


class TestTheGenerationLinkOnThePage:
    """UI-3c, at the page: the owner's "take me to that submission"
    button, and the flag-off regression it could have been.

    `_generation_turn` builds the turn `tools/vision/tools.py::
    run_generate` really writes -- the image generator's tool key, and
    `Turn.data` as the `services.job_json` payload whose `"id"` is the
    GENERATION uuid (never `queue_job_id`, which is in the same payload).
    """

    JOB = "6d1a2f30-0c1b-4a55-9d0e-1f2a3b4c5d6e"

    def _generation_turn(self, conversation, *, data=..., **overrides):
        invocation = ToolInvocation.objects.create(
            principal_kind="resident_agent", principal_key="general",
            tool_key="vision.generate", outcome=ToolInvocation.Outcome.OK,
            text=f"Generation {self.JOB} finished as done.",
            finished_at=timezone.now(),
        )
        fields = dict(
            conversation=conversation, role=Turn.Role.TOOL,
            text=f"Generation {self.JOB} finished as done.",
            state=Turn.State.DONE, invocation=invocation, artifacts=["output:12"],
            tool_call={"tool": "vision.generate", "args": {"prompt": "a cat"},
                       "agent": "general", "id": "", "discarded": []},
            data=({"id": self.JOB, "status": "done", "queue_job_id": 4321}
                  if data is ... else data),
        )
        fields.update(overrides)
        return make_turn(**fields)

    def test_the_card_carries_a_link_to_this_submission_on_the_image_surface(
        self, client,
    ):
        conversation = make_conversation()
        self._generation_turn(conversation)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        href = f"{reverse('vision-gallery')}?job={self.JOB}#job-{self.JOB}"
        assert f'href="{href}"' in body
        assert "View in Gallery" in body

    def test_it_is_a_link_and_never_a_form(self, client):
        """It opens a page and changes nothing: a GET is what a link is
        for, and this surface adds no JavaScript to make a button behave
        like one."""
        conversation = make_conversation()
        self._generation_turn(conversation)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        start = body.index("View in Gallery")
        card = body[body.rindex("<div class=\"tool-card", 0, start):start]
        assert "<a " in card
        assert "<form" not in card

    def test_with_the_image_surface_unmounted_the_page_still_renders(
        self, client, monkeypatch,
    ):
        """THE REGRESSION THAT MATTERS. `vision-gallery` is mounted only
        while `"vision"` is in FARABUNKER_FEATURES, and a conversation
        outlives a feature flag: an unguarded `{% url %}` in the card
        would raise `NoReverseMatch` mid-render and 500 this whole
        historical thread. The card renders exactly as it did before the
        button existed -- image and sentence, no link, no crash.

        Patched at the renderer's own `reverse`, never by overriding the
        flag: THE VISION-FLAG RULE (`test_mount.py`) makes a flag
        override in this package a process-wide URL resolution hazard
        for every later test in the run. The page's own `{% url %}` tags
        resolve through `django.urls` and are untouched by this patch,
        which is what makes the 200 below a real render.
        """
        def _unmounted(*args, **kwargs):
            raise NoReverseMatch("vision is not mounted")

        conversation = make_conversation()
        self._generation_turn(conversation)
        monkeypatch.setattr("agents.chat.rendering.reverse", _unmounted)
        response = client.get(reverse("chat-conversation", args=[conversation.id]))
        body = response.content.decode()
        assert response.status_code == 200
        assert "View in Gallery" not in body
        assert f"Generation {self.JOB} finished as done." in body

    def test_a_generation_that_never_submitted_gets_no_link(self, client):
        """A call refused or failed before it submitted has no
        submission to open -- and no crash on the way to saying so."""
        conversation = make_conversation()
        self._generation_turn(conversation, data={}, artifacts=[])
        response = client.get(reverse("chat-conversation", args=[conversation.id]))
        assert response.status_code == 200
        assert "View in Gallery" not in response.content.decode()

    def test_a_library_card_gets_no_such_link(self, client):
        conversation = make_conversation()
        self._generation_turn(
            conversation, artifacts=[], data={"citations": []},
            tool_call={"tool": "rag.search", "args": {"query": "x"},
                       "agent": "general", "id": "", "discarded": []},
        )
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "View in Gallery" not in body


class TestDelegatedTurns:
    def test_they_render_inside_a_collapsed_disclosure(self, client):
        """Spec section 8.4: `depth > 0` turns render inside a
        collapsed disclosure under the delegating turn -- a delegate is
        a subroutine, and its steps are auditable without being the
        thread."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.TOOL, depth=1,
                  text="the library searched", state=Turn.State.DONE,
                  tool_call={"tool": "rag.search", "args": {}, "agent": "library",
                             "id": "", "discarded": []})
        make_turn(conversation=conversation, role=Turn.Role.TOOL, depth=0,
                  text="library answered", state=Turn.State.DONE,
                  tool_call={"tool": "agent.library", "args": {"task": "t"},
                             "agent": "general", "id": "", "discarded": []})
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "<details" in body
        assert "the library searched" in body
        # Collapsed: no `open` attribute on the disclosure.
        assert "<details open" not in body


class TestFailedAndCancelledTurns:
    def test_a_failed_turn_shows_its_error_and_a_way_back_to_setup(self, client):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error="the worker stopped responding")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "the worker stopped responding" in body
        assert reverse("inference-console") in body

    def test_a_cancelled_turn_says_so(self, client):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.CANCELLED,
                  error="Cancelled from the queue before it ran.")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "Cancelled from the queue before it ran." in body

    def test_a_cancelled_turn_carries_no_model_setup_link(self, client):
        """U6: "Cancelled from the queue before it ran." then a Models
        link is a non sequitur -- a cancel is never a reason to bind a
        model. The link stays on a genuine failure (the test above)."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.CANCELLED,
                  error="Cancelled from the queue before it ran.")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        # Scoped to the thread region: the shared shell's own nav links
        # to `inference-console` on every page (`chat/base.html`), which
        # would otherwise make an unscoped search find the nav bar
        # rather than a link this card rendered.
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert reverse("inference-console") not in thread_region


class TestArtifactFallbackOnAnAssistantTurn:
    def test_an_unmounted_image_artifact_renders_as_text_not_a_broken_img(
        self, client, monkeypatch
    ):
        """THE VISION-OFF CASE, on a non-tool turn this time (Task 7's
        own pin covers `artifact_links` directly; this is its rendered
        counterpart through an ASSISTANT turn's own `card.images` loop
        in `_turn_card.html`). Patched at the renderer's own `reverse`
        rather than by overriding `FARABUNKER_FEATURES`, exactly as
        `test_rendering.py::test_a_reference_whose_url_is_not_mounted_
        still_renders_as_text` does -- this test uses `reverse()`
        itself, and THE VISION-FLAG RULE makes a flag override here a
        process-wide URL resolution hazard for every later test."""
        def _unmounted(*args, **kwargs):
            raise NoReverseMatch("vision is not mounted")

        monkeypatch.setattr("agents.chat.rendering.reverse", _unmounted)
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.DONE, text="here you go",
                  artifacts=["output:12"])
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "<img" not in body
        assert "output:12 (not available on this install)" in body


class TestAnExistingConversationStaysReadable:
    def test_the_page_still_renders_when_its_agent_became_restricted(self, client):
        """ACCESS TO ONE'S OWN ROWS IS OWNERSHIP, not a label question.
        `agents/chat/views/thread.py` reads `conversation.agent` directly
        and never calls `visible_agents` -- its own docstring records that
        as deliberate -- and retroactively hiding somebody's own history
        would be a worse answer than the one the label was asked for. The
        NEW TURN is what is refused."""
        from agents.models import AgentEntitlement
        owner = make_user()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(owner), agent)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.get(reverse("chat-conversation", args=[conversation.pk]))
            assert response.status_code == 200
            assert client.post(reverse("chat-turn", args=[conversation.pk]),
                               {"text": "hello"}).status_code == 403

    @staticmethod
    def _banner(body: str) -> str:
        """The refusal banner's own markup, scoped away from the shared
        shell's nav (`foundation/templates/_shell.html`), which renders
        its own way into the operator surfaces on every page -- an
        unscoped substring check would pass or fail on the
        nav bar rather than on `chat/_unavailable.html`'s fragment,
        exactly the trap `test_a_cancelled_turn_carries_no_model_setup_
        link` above already scopes around for the turn-card link."""
        start = body.index('class="banner warn"')
        return body[start:body.index("</div>", start)]

    def test_the_banner_carries_no_setup_links_for_the_member_seeing_it(self, client):
        """W-4 (IA-2 walkthrough finding): the restricted-agent banner
        ("This agent needs an entitlement...") reused `_unavailable.
        html`'s "Models"/"Install guides" links -- both point at admin-only
        surfaces (`inference-console` is class S) and both 403/are
        useless for the very member the banner is naming. The sentence
        stays; the links go, for anybody who is not an administrator."""
        from agents.models import AgentEntitlement
        owner = make_user()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(owner), agent)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])).content.decode()
        assert "This agent needs an entitlement" in body
        banner = self._banner(body)
        assert "This agent needs an entitlement" in banner
        assert "Models" not in banner
        assert reverse("inference-console") not in banner

    def test_an_admin_still_sees_the_links(self, client):
        """The fix is audience-gated, not a blanket removal: an
        administrator viewing the identical banner can actually act on
        it (`inference-console` is exactly their surface)."""
        from agents.models import AgentEntitlement
        admin = make_admin()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(admin), agent)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])).content.decode()
        banner = self._banner(body)
        assert reverse("inference-console") in banner


class TestQueuedCopyIsHonest:
    """`data.position` is null whenever the queue cannot say where a job
    sits -- a job claimed between two polls, or a backend that answers
    without a position. "Position null of the queue…" is the page telling
    the operator something that is not a sentence."""

    _ROOT = Path(__file__).resolve().parents[3]

    def test_the_chat_card_falls_back_to_Queued(self):
        source = (self._ROOT / "agents/chat/templates/chat/conversation.html").read_text(
            encoding="utf-8")
        assert 'typeof data.position === "number"' in source
        assert '"Queued…"' in source

    def test_the_ask_page_carries_the_same_fix(self):
        """The identical expression, the identical cause, one line each.
        Fixing one and leaving the other ships a known defect."""
        source = (self._ROOT / "tools/rag/templates/rag/ask.html").read_text(
            encoding="utf-8")
        assert 'typeof data.position === "number"' in source
        assert '"Queued…"' in source


class TestTheQuietActionsRow:
    """Owner feedback round 9, verbatim: "when I'm on the chat screen
    the share shouldn't be a main feature. it should be a suble side
    feature (like below the chat and if i click on it I get more
    options (via a popup)." `Share…` and `Delete this conversation`
    render in `.thread-actions`, below the composer, the first as a
    popup overlay -- these pin the STRUCTURE (the popup wrapper, the
    row's position, the gates), never the runtime behaviour of
    actually opening one, which no test here has a browser to press
    (the same caveat `TestEnterSubmits` above states for its own
    template-presence pins).

    `+ Add files` moved OUT of `.thread-actions` in round 9d (owner:
    the muted-text treatment "failed discoverability twice with the
    real user") -- it now renders as a real button on the composer's
    own row instead; see `TestTheAttachDoor`'s own class docstring for
    where its pins live."""

    def test_the_row_sits_after_the_composer(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<div class="thread-actions">' in body
        assert body.index('id="turn-form"') < body.index('class="thread-actions"')

    def test_the_share_control_is_a_popup_overlay(self, client):
        """The exact chat-menu pattern (`chat/base.html`'s sidebar
        popup, reused): a `<details class="chat-menu">` whose summary
        is the visible trigger and whose `.chat-menu-body` panel holds
        the UNCHANGED `_share_panel.html` content."""
        owner = make_user()
        agent = make_agent()
        conversation = create_conversation(user_principal(owner), agent)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()
        actions = body[body.index('class="thread-actions"'):]
        assert '<details class="chat-menu">' in actions
        assert "<summary>Share…</summary>" in actions
        assert '<div class="chat-menu-body">' in actions
        assert "Shared with" in actions
        assert actions.index("Share…") < actions.index("Shared with")

    def test_the_share_control_is_absent_entirely_on_an_open_box(self, client):
        """No dead control: `may_see_shares and identity_posture !=
        "open"` gates the WHOLE `<details>`, not merely its panel."""
        conversation = make_conversation()
        with posture("open"):
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).content.decode()
        assert "Share…" not in body
        assert "Shared with" not in body

    def test_delete_is_muted_not_a_primary_button(self, client):
        """Owner's ruling: "not a primary button". Delete shares the
        SAME muted `chat-menu` summary styling as its two neighbours
        now, not the old accent-coloured `.delete-disclosure` class;
        its own confirm stays IN-FLOW -- no `chat-menu-body` wraps it,
        unlike the share/attach panels (the brief's own "Delete keeps
        its existing confirm flow")."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        actions = body[body.index('class="thread-actions"'):]
        assert "<summary>Delete this conversation</summary>" in actions
        # Scoped to THIS row, not the whole page: `.delete-disclosure`
        # is still a real, still-used class elsewhere (the sidebar's
        # own per-conversation menu, `chat/_sidebar.html`) -- what
        # changed is which class wraps THIS details element.
        assert "delete-disclosure" not in actions
        assert "thread-delete" not in actions
        assert '<div class="delete-confirm">' in actions


class TestTheAttachDoor:
    """Owner feedback round 9, verbatim: "I also don't have the ability
    in the chat to add any additional objects like photos/documents/
    pdfs/ect. I sould be able to click a button to add somehting and
    have a modal box that elts me load document(s) and then select
    document scope." OWNER AMENDMENT (binding): ONE shared fragment
    (`chat/_attach_files.html`) for both a plain conversation and one
    inside a workstream -- these tests exercise it from both sides,
    never two separate code paths.

    ROUND 9d MOVED THE DOOR ITSELF: the owner had already seen a
    screenshot containing the words "+ Add files" and still reported
    "neither chat is showing the ability to add an image or document"
    -- the muted `.thread-actions` treatment failed discoverability
    twice, so the trigger moved onto the composer's own row (round 9d:
    `.composer-row`; ROUND 9e RENAMED that row to `.composer-card`, a
    full composition pass -- see `chat/conversation.html`'s own
    `chat_style` comment for the "before" this pass fixed -- but the
    door's LOCATION claim this test pins is unaffected by the rename;
    retargeted to the new class, never weakened). The tests below that
    only check text/field presence are UNCHANGED from round 9 (the
    marker text and the fragment's own fields did not move);
    `test_the_door_now_lives_on_the_composer_card_not_the_quiet_one`
    is the one that pins WHERE."""

    def test_the_door_now_lives_on_the_composer_card_not_the_quiet_one(self, client):
        """Non-vacuous both ways: fails if the door is still inside
        `.thread-actions` (round 9's own location), and fails if it
        disappeared from `.composer-card` entirely.

        ROUND 18 RETIRES THE `<details class="attach-trigger">` POPUP
        this test used to pin (owner feedback: "the box that allows me
        to add it just stays hovered"): `+ Add files` is a plain
        `<label class="attach-label-button" for="attach-files">` now,
        always in flow, never a disclosure -- see `chat/_attach_files.
        html`'s own module comment for the full rewrite."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<div class="composer-card">' in body
        composer_region = body[
            body.index('class="composer-card"'):body.index('class="thread-actions"')
        ]
        assert '<label class="attach-label-button" for="attach-files">' in composer_region
        assert "+ Add files" in composer_region
        actions_region = body[body.index('class="thread-actions"'):]
        assert "+ Add files" not in actions_region
        assert "attach-trigger" not in body

    def test_the_door_renders_for_a_plain_conversation_by_default(self, client):
        """`rag.ingest` UNLABELLED (the default everywhere else in this
        codebase) means everyone holds it -- the door renders with no
        setup at all, matching `_may_upload`'s own default-allow shape.
        ROUND 12: a plain (loose) conversation gets the TWO-option
        chooser (chat only / universal library), never a `workstream`
        field, and "This chat only" is pre-checked -- the door's own
        default."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "+ Add files" in body
        # ROUND 13: no `action=`/`next`/`conversation` fields any more --
        # this fragment is form CONTROLS of `#turn-form` now, not its own
        # `<form>` (`chat/_attach_files.html`'s own docstring). The file
        # input is `multiple` but deliberately NOT `required` (most
        # messages carry no files at all, and this control now shares a
        # form with the always-required message text).
        assert 'name="files" multiple' in body
        assert 'name="files" multiple required' not in body
        assert 'name="placement" value="conversation" checked required' in body
        assert 'name="placement" value="universal" required' in body
        assert 'name="placement" value="contained" required' not in body
        assert 'name="workstream"' not in body

    def test_the_door_is_absent_when_rag_ingest_is_labelled_and_not_held(self, client):
        """THE GENERAL PREDICATE, not the Documents panel's owner-only
        one -- but still a REAL gate: a principal missing the label
        sees no control at all, matching `document_upload`'s own 403
        for the identical case (render-vs-gate)."""
        owner = make_user()
        agent = make_agent()
        conversation = create_conversation(user_principal(owner), agent)
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()
        assert "+ Add files" not in body
        # ROUND 13: no separate `action=` to check any more -- the
        # fragment's own file input is the structural marker instead.
        assert 'name="files"' not in body

    def test_the_door_still_renders_when_the_label_is_held(self, client):
        """The sibling case: labelled, but this principal DOES hold the
        labelling entitlement -- the door renders exactly as when the
        tool is unlabelled entirely."""
        legal = make_entitlement(name="Legal")
        user = make_user()
        grant(legal, user=user)
        ToolEntitlement.objects.create(tool_key="rag.ingest", entitlement=legal)
        agent = make_agent()
        conversation = create_conversation(user_principal(user), agent)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()
        assert "+ Add files" in body

    def test_the_stream_owner_sees_the_placement_chooser(self, client):
        """In a workstream, the SAME fragment renders the THREE-option
        half (round 12): a hidden `workstream` field plus this door's
        own inline chooser -- "This chat only" (pre-checked, the
        default), "This workstream only", "The universal library" --
        never `rag/_placement_choice.html`'s own two-value one (that
        stays the panel's, unchanged)."""
        owner = make_user()
        stream = _workstream(**owner_fields(user_principal(owner)))
        conversation = make_conversation(workstream=stream)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).content.decode()
        assert "+ Add files" in body
        assert f'name="workstream" value="{stream.pk}"' in body
        assert 'name="placement" value="conversation" checked required' in body
        assert 'name="placement" value="contained" required' in body
        assert 'name="placement" value="universal" required' in body

    def test_a_streams_own_remembered_default_does_not_auto_apply_on_the_chat_door(
        self, client
    ):
        """ROUND 12 (owner ruling): the chat door ALWAYS ASKS, even when
        this workstream has a remembered `default_upload_placement` --
        the note-instead-of-radios shortcut stays the WORKSTREAM
        DOCUMENTS PANEL's own behaviour (`rag/_placement_choice.html`,
        untouched); this door still renders its full three-radio
        chooser regardless."""
        owner = make_user()
        stream = _workstream(**owner_fields(user_principal(owner)),
                             default_upload_placement="contained")
        conversation = make_conversation(workstream=stream)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).content.decode()
        assert "per this workstream" not in body.lower()
        assert 'name="placement" value="conversation" checked required' in body
        assert 'name="placement" value="contained" required' in body
        assert 'name="placement" value="universal" required' in body

    def test_a_stream_recipient_who_may_not_manage_it_sees_no_attach_door_at_all(
        self, client
    ):
        """THE BRIEF'S OWN EITHER/OR, RESOLVED: hide the door entirely
        rather than fall back to a universal-only variant -- the SAME
        line `rag/panels/documents.html`'s own Upload section already
        draws for this exact recipient (`workstream_scope(...).
        may_upload`, owner-only). `rag.ingest` is left UNLABELLED here
        (held by everyone, including this reader), which is what
        proves the STREAM half of the gate is doing the work, not the
        general one."""
        owner, reader = make_user(), make_user()
        stream = _workstream(**owner_fields(user_principal(owner)))
        conversation = make_conversation(workstream=stream)
        Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                             user=reader, level=Share.Level.USE)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, reader)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).content.decode()
        assert "+ Add files" not in body
        assert 'name="files"' not in body


class TestTheDragAndDropStaging:
    """Owner feedback round 10, verbatim: "I should be able to drag and
    drop documents either on the chat window or the add file browser
    rather than just clicking browse to add documents." A SANCTIONED
    SCRIPT (the Enter-to-send/poller precedent, round 8): progressive
    enhancement only, and there is no browser here to actually drag a
    file onto the page -- these are template-presence pins for the
    script's SOURCE (it exists, it is gated correctly, it carries the
    document-level navigation guard), the same shape `TestEnterSubmits`
    and `TestTheStayAtBottomGuard` already use for this page's other
    scripted behaviour."""

    def test_the_script_renders_when_the_door_does(self, client):
        """Non-vacuous markers: the delegated drop target, the drop
        handler's own staging line, and the document-level guard that
        stops the browser navigating away to a dropped file.

        ROUND 18: `attachTrigger.open = true;` is GONE -- there is no
        popup left to open (`chat/_attach_dragdrop.html`'s own module
        comment, point 2); `mergeInFiles(files);` is the drop handler's
        own staging call now, ACCUMULATING rather than replacing."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert 'var chatWrap = card ? card.closest(".chat-wrap") : null;' in body
        assert "mergeInFiles(files);" in body
        assert "attachTrigger.open" not in body
        assert 'document.addEventListener("dragover"' in body
        assert 'document.addEventListener("drop"' in body

    def test_the_script_is_absent_when_the_door_is(self, client):
        """THE TWO-DIRECTIONAL PIN (brief's own requirement): the SAME
        gate that hides `+ Add files` itself must also ship zero drag
        bytes -- not merely an inert script, no script at all. Reuses
        the exact labelled-and-not-held setup `TestTheAttachDoor::
        test_the_door_is_absent_when_rag_ingest_is_labelled_and_not_
        held` already proves hides the button.

        ROUND 18: TWO script blocks, not one, even with the door
        absent -- `chat/_composer.html` always includes `chat/_enter_
        to_send.html` (Enter-to-send is unconditional; only the
        drag-and-drop script is gated on `may_attach_files`), beside
        the page's own `{% block scripts %}` poller. ROUND 21: THREE,
        the third being the RAIL's own exclusive-open handler
        (`chat/_menu_exclusive.html`), which every page rendering
        `chat/_sidebar.html` serves and which is unrelated to the
        attach door either way. The drag-specific markers below are
        what keep this a two-directional pin rather than a count."""
        owner = make_user()
        agent = make_agent()
        conversation = create_conversation(user_principal(owner), agent)
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()
        assert "+ Add files" not in body
        assert 'var chatWrap = card ? card.closest(".chat-wrap") : null;' not in body
        assert "mergeInFiles(files);" not in body
        assert 'document.addEventListener("dragover"' not in body
        assert body.count("<script") == 3

    def test_the_script_count_when_the_door_renders_is_also_pinned(self, client):
        """WHOLE-BRANCH REVIEW Minor 2: the sibling of the pin just above
        only covered the door-ABSENT case -- nothing pinned the count for
        the door-PRESENT case, so a fourth script block could ship
        alongside the drag-and-drop one with every existing pin still
        green. FOUR, as of round 21: the poller (`{% block scripts %}`),
        `chat/_enter_to_send.html` (unconditional), `chat/_attach_
        dragdrop.html` (gated on `may_attach_files`, present here), and
        `chat/_menu_exclusive.html` (the rail's own, on every chat
        page)."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "+ Add files" in body
        assert body.count("<script") == 4

    def test_the_dragover_types_check_excludes_text_selections(self, client):
        """`carriesFiles()` reads `dataTransfer.types` for `"Files"` --
        pinned by source, since a real drag of selected text (which
        carries `"text/plain"`, never `"Files"`) needs a browser to
        actually exercise; this proves the check exists and names the
        right type string, not that dragging text does nothing (no
        browser here to try)."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert 'indexOf.call(types, "Files")' in body


class TestTheAttachDoorsQueryDiscipline:
    """I1 (round 9 review): `tool_access_for`'s own two real queries --
    `held_entitlement_ids` (reads `identity_entitlementgrant`) and
    `tool_entitlement_ids()` (reads `agents_toolentitlement`) -- ran
    TWICE per GET for any signed-in, non-`sees_all_content` principal:
    once for `may_attach_files` here, and again inside `preflight_
    turn`'s own internal `tool_access_for` call, since neither read was
    cached anywhere. `preflight_turn`'s new `tool_access=` keyword
    (`agents/runtime/preflight.py`) reuses THIS page's own no-wall value
    instead -- but ONLY when `wall` is empty (that function's own
    docstring states why a non-empty wall must always recompute fresh,
    rather than narrow a value that might be the `sees_all_content`
    shortcut's empty placeholder).

    NOT AN ABSOLUTE COUNT: the page reads entitlement grants for OTHER
    axes too (`model_access_for` inside the SAME `preflight_turn` call,
    at minimum), so "exactly one grant-read total" would be a false
    claim this test cannot honestly make. Asserted as a DELTA instead --
    a wall-scoped conversation (owner decision: a REAL `WorkstreamScope
    Entitlement` row) costs exactly ONE MORE grant-read than an
    otherwise-identical wall-less one, for the SAME principal on the
    SAME view -- which isolates precisely the shortcut this fix adds
    (reused when wall is empty, freshly computed when it is not) from
    every other axis's own, unrelated grant-read, without this test
    having to know or assert their number."""

    def test_a_non_empty_wall_costs_exactly_one_more_grant_read(
        self, client, bound_chat_role
    ):
        """`bound_chat_role` is REQUIRED, not incidental: an unbound
        chat role makes `preflight_turn` return UNBOUND right after its
        own `model_access_for` call, before it ever reaches the tool-
        access line this test is actually about -- both conversations
        would then short-circuit at the SAME early point regardless of
        `wall`, making the two GETs cost an IDENTICAL number of grant
        reads for a reason that has nothing to do with this fix."""
        user = make_user()
        ent = make_entitlement(name="Legal")
        grant(ent, user=user)

        agent = make_agent()
        plain = create_conversation(user_principal(user), agent)
        stream = _workstream(**owner_fields(user_principal(user)))
        WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
        walled = make_conversation(agent=agent, workstream=stream)

        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            with CaptureQueriesContext(connection) as plain_queries:
                plain_response = client.get(
                    reverse("chat-conversation", args=[plain.pk]))
            with CaptureQueriesContext(connection) as walled_queries:
                walled_response = client.get(
                    reverse("chat-conversation", args=[walled.id]))
        assert plain_response.status_code == 200
        assert walled_response.status_code == 200

        def _grant_reads(captured):
            return sum(1 for q in captured.captured_queries
                       if "entitlementgrant" in q["sql"].lower())

        assert _grant_reads(walled_queries) == _grant_reads(plain_queries) + 1

    def test_attachment_count_does_not_scale_the_grant_read_cost(
        self, client, bound_chat_role
    ):
        """FIX-2 VERIFY MINOR: the pin above never exercised the
        attachments read's OWN second entitlement-grant read (`tools.
        rag.workstreams.stream_documents`'s own internal `document_
        visibility` call, run when there is something in-corpus to
        narrow, `tools.rag.access.attached_documents`'s own "third
        query runs only when there is something to narrow" comment) --
        bounded, not N+1, but previously UNPINNED. One attachment and
        ten, on an otherwise-identical walled conversation, must cost
        the IDENTICAL number of grant reads."""
        from agents.tests._helpers import make_document
        from tools.rag.models import DocumentAttachment

        user = make_user()
        ent = make_entitlement(name="Legal")
        grant(ent, user=user)
        agent = make_agent()
        stream = _workstream(**owner_fields(user_principal(user)))
        WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)

        one = make_conversation(agent=agent, workstream=stream)
        DocumentAttachment.objects.create(
            document=make_document(workstream=stream), conversation_id=one.id)
        many = make_conversation(agent=agent, workstream=stream)
        for _ in range(10):
            DocumentAttachment.objects.create(
                document=make_document(workstream=stream), conversation_id=many.id)

        def _grant_reads(captured):
            return sum(1 for q in captured.captured_queries
                       if "entitlementgrant" in q["sql"].lower())

        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            # A WARM-UP GET, unmeasured (the same reason every other
            # query-cost pin in this file takes one): the first request
            # a test client makes pays one-time framework setup cost
            # unrelated to attachments.
            client.get(reverse("chat-conversation", args=[one.id]))
            with CaptureQueriesContext(connection) as one_queries:
                one_response = client.get(reverse("chat-conversation", args=[one.id]))
            with CaptureQueriesContext(connection) as many_queries:
                many_response = client.get(reverse("chat-conversation", args=[many.id]))
        assert one_response.status_code == 200
        assert many_response.status_code == 200
        assert _grant_reads(many_queries) == _grant_reads(one_queries)


def _attach(document, conversation_id, **overrides) -> None:
    """A `DocumentAttachment` row, bound to a REAL user turn by default
    (round 13: chips render on the TURN that carried the file, not on
    a conversation-level strip any more -- `agents.chat.rendering.
    turn_card`'s own new `attachments` key, grouped by `turn_id`). A
    turn is created here, automatically, ONLY so every EXISTING call
    site in this class (`_attach(doc, conversation.id)`, unchanged)
    keeps proving what it always proved -- chip rendering/corpus
    behaviour -- without each one having to build its own throwaway
    turn first. `turn_id=<real pk>` is still accepted for a test that
    needs to name a PRE-EXISTING turn explicitly rather than a fresh
    throwaway one."""
    turn_id = overrides.pop("turn_id", "unset")
    if turn_id == "unset":
        turn = Turn.objects.create(
            conversation_id=conversation_id, index=Turn.next_index(conversation_id),
            role=Turn.Role.USER, text="attached", state=Turn.State.DONE,
        )
        turn_id = turn.pk
    DocumentAttachment.objects.create(document=document, conversation_id=conversation_id,
                                      turn_id=turn_id)


class TestTheAttachmentsStrip:
    """Owner feedback round 11, verbatim: "i added a pdf to this chat
    and I don't see it as part of my chat entry, I just see a new text
    saying 1 queued file ... and there is no access to the document I
    uploade." Ready/Processing…/Failed status chips, corpus-filtered
    (round 11 review I-3) -- ROUND 13 MOVES THEM off a conversation-
    level strip and onto the USER TURN BUBBLE that carried the file
    (`card.attachments`, `.turn-attachments`), so this class's own name
    now names the ORIGINAL owner complaint this whole feature answers,
    not the specific (retired) widget that first answered it. Class
    kept, tests updated in place, rather than renamed/split, so its
    `git log` stays one continuous story."""

    def test_a_ready_attachment_renders_a_title_link_and_a_ready_chip(self, client):
        conversation = make_conversation()
        doc = make_document(title="Resume.pdf")
        _attach(doc, conversation.id)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<div class="turn-attachments">' in body
        assert "Resume.pdf" in body
        assert '<span class="chip chip-done">Ready</span>' in body
        assert f'href="{reverse("rag-document-file", args=[doc.pk])}"' in body

    def test_a_chat_scoped_attachment_gets_the_this_chat_only_badge(self, client):
        """ROUND 12 REVIEW NIT 2: legible from inside the chat which
        files are chat-only -- the ordinary universal attachment above
        gets no such chip at all."""
        conversation = make_conversation()
        doc = make_document(title="Private.pdf", scope=Document.Scope.CONVERSATION)
        _attach(doc, conversation.id)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<span class="chip chip-queued">This chat only</span>' in body

    def test_an_ordinary_attachment_gets_no_chat_only_badge(self, client):
        """The specific CHIP, not the substring: the attach door's own
        placement chooser also renders the words "This chat only" as a
        radio label, unrelated to the strip -- this checks the badge
        markup itself, never confused with that."""
        conversation = make_conversation()
        doc = make_document(title="Shared.pdf")
        _attach(doc, conversation.id)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<span class="chip chip-queued">This chat only</span>' not in body

    def test_a_processing_attachment_shows_the_processing_chip(self, client):
        conversation = make_conversation()
        _attach(make_document(status="processing"), conversation.id)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<span class="chip chip-running">Processing…</span>' in body

    def test_a_pending_attachment_also_shows_the_processing_chip(self, client):
        """PENDING and PROCESSING both read "still processing" to a
        chat reader -- the distinction is internal plumbing, not a fact
        worth a fourth chip colour."""
        conversation = make_conversation()
        _attach(make_document(status="pending"), conversation.id)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<span class="chip chip-running">Processing…</span>' in body

    def test_a_failed_attachment_shows_the_failed_chip_and_honest_detail(self, client):
        conversation = make_conversation()
        _attach(make_document(status="failed", status_detail="unsupported codec"),
               conversation.id)
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<span class="chip chip-failed">Failed</span>' in body
        assert "unsupported codec" in body

    def test_a_recipient_without_read_access_does_not_see_the_title(self, client):
        owner, reader = make_user(), make_user()
        agent = make_agent()
        conversation = create_conversation(user_principal(owner), agent)
        legal = make_entitlement(name="Legal")
        doc = make_document(title="Confidential.pdf")
        _attach(doc, conversation.id)
        DocumentEntitlement.objects.create(document=doc, entitlement=legal)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=reader,
                             level=Share.Level.VIEW)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, reader)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()
        assert "Confidential.pdf" not in body
        assert '<div class="turn-attachments">' not in body

    def test_no_attachments_renders_no_strip_at_all(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert '<div class="turn-attachments">' not in body

    def test_the_strip_renders_for_a_view_only_recipient_too(self, client):
        """UNGATED by `may_attach_files`: the strip is a RECORD, not a
        door -- a `view`-level recipient (who may not even post, let
        alone attach) still sees what is already there, the same way
        the transcript itself renders for them."""
        owner, viewer = make_user(), make_user()
        agent = make_agent()
        conversation = create_conversation(user_principal(owner), agent)
        _attach(make_document(title="Notes.pdf"), conversation.id)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=viewer,
                             level=Share.Level.VIEW)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()
        assert "Notes.pdf" in body

    def test_a_universal_attachment_outside_the_streams_wall_shows_an_honest_state(
        self, client
    ):
        """R-1 (round 11 RE-review, replacing this test's own first cut
        which asserted absence -- the re-review's own finding: silent
        absence reproduces the owner's original symptom exactly, "i
        added a pdf to this chat and I don't see it"). A universal
        document labelled OUTSIDE this stream's own wall is READABLE
        (the principal genuinely holds that label) but not admitted by
        this stream's own corpus -- it STILL renders, with an honest
        distinct state, rather than vanishing with nothing to explain
        where it went."""
        user = make_user()
        inside = make_entitlement(name="Inside the wall")
        outside = make_entitlement(name="Outside the wall")
        grant(outside, user=user)
        agent = make_agent()
        stream = _workstream(**owner_fields(user_principal(user)))
        WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=inside)
        conversation = make_conversation(agent=agent, workstream=stream)
        doc = make_document(title="Outside.pdf")
        DocumentEntitlement.objects.create(document=doc, entitlement=outside)
        _attach(doc, conversation.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).content.decode()
        assert '<div class="turn-attachments">' in body
        assert "Outside.pdf" in body
        assert '<span class="chip chip-queued">Not retrievable here</span>' in body
        assert "outside this stream" in body

    def test_include_universal_off_shows_the_new_honest_cause(self, client):
        """ROUND 17 (owner, verbatim: "a bool in settings to use all rag
        documents ... default it on"). The SAME "Not retrievable here"
        chip, a DIFFERENT detail sentence -- the wall is EMPTY here (so
        it could never have excluded anything on its own), proving the
        toggle alone is what the page is naming."""
        user = make_user()
        agent = make_agent()
        stream = _workstream(**owner_fields(user_principal(user)),
                             include_universal=False)
        conversation = make_conversation(agent=agent, workstream=stream)
        doc = make_document(title="Library.pdf")
        _attach(doc, conversation.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).content.decode()
        assert '<span class="chip chip-queued">Not retrievable here</span>' in body
        assert "full document library" in body
        assert "outside this stream's scope" not in body

    def test_sees_nothing_gets_its_own_honest_sentence_not_the_wall_ones(self, client):
        """CONSOLIDATION WAVE (audit A, F5) -- the one truth defect the
        whole wave names: a CHAT-SCOPED attachment can ALSO be
        `in_corpus=False`, for a completely different reason
        (`DocumentVisibility.sees_nothing` -- a signed-in member with
        ZERO entitlements on a LOCKED library) that has nothing to do
        with the wall or the universal-library toggle. Before this fix,
        that case fell through to the WALL sentence verbatim -- "it
        lives in the universal library, outside this stream's scope" --
        about a document that is chat-scoped and lives in neither."""
        from identity.contracts.postures import LIBRARY_LOCKED

        member = make_user()
        agent = make_agent()
        conversation = create_conversation(user_principal(member), agent)
        doc = make_document(title="Locked-out.pdf", scope=Document.Scope.CONVERSATION)
        _attach(doc, conversation.id)
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            sign_in(client, member)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).content.decode()
        assert '<span class="chip chip-queued">Not retrievable here</span>' in body
        # THE HONEST SENTENCE, NOT THE WALL ONE.
        assert "no entitlements on this box's locked library" in body
        assert "outside this stream's scope" not in body
        assert "full document library" not in body

    def test_a_contained_attachment_shows_no_such_state(self, client):
        """The positive twin: a document CONTAINED in this stream is
        in-corpus regardless of the wall (author decision 5 narrows the
        universal leg only) -- no "not retrievable" chip for it."""
        user = make_user()
        agent = make_agent()
        stream = _workstream(**owner_fields(user_principal(user)))
        conversation = make_conversation(agent=agent, workstream=stream)
        doc = make_document(title="Inside.pdf", workstream=stream)
        _attach(doc, conversation.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).content.decode()
        assert "Inside.pdf" in body
        assert '<span class="chip chip-queued">Not retrievable here</span>' not in body

    def test_the_query_cost_does_not_scale_with_attachment_count(self, client):
        """GC-6, the same equality idiom this suite already uses
        elsewhere: one attachment and ten cost the SAME number of
        queries for the whole page -- proof the strip's own read is not
        a per-row N+1 hiding behind the transcript's own cost."""
        # ONE shared agent for both conversations, deliberately: two
        # DIFFERENT agents (or two default `make_agent()` calls, which
        # collide on that helper's own fixed "test-agent" slug) would
        # let an unrelated per-agent difference -- not the attachment
        # count -- explain a query-count difference this pin exists to
        # rule out.
        agent = make_agent()
        conversation_one = make_conversation(agent=agent)
        _attach(make_document(), conversation_one.id)
        conversation_many = make_conversation(agent=agent)
        for _ in range(10):
            _attach(make_document(), conversation_many.id)
        # A WARM-UP GET, unmeasured, before either capture below --
        # precisely the direction that would hide a regression from the
        # equality otherwise (`test_workstream_page.py`'s own identical
        # pin uses the same shape): the FIRST request a test client
        # makes pays one-time framework setup cost (content-type/session
        # caches warming up) that has nothing to do with attachments,
        # and comparing a COLD first call against a WARM second one
        # would make an unrelated cost look like -- or mask -- a real
        # per-row scaling difference.
        client.get(reverse("chat-conversation", args=[conversation_one.id]))
        with CaptureQueriesContext(connection) as one:
            response_one = client.get(
                reverse("chat-conversation", args=[conversation_one.id]))
        with CaptureQueriesContext(connection) as many:
            response_many = client.get(
                reverse("chat-conversation", args=[conversation_many.id]))
        assert response_one.status_code == 200
        assert response_many.status_code == 200
        assert len(many) == len(one)


def _connection_in_set_attached_to(entitlement, *, name):
    """One chat-capable `ModelConnection` in a `ModelSet` attached to
    `entitlement`. A standalone copy of `agents/tests/test_wall_seams.
    py`'s own helper of the same name, kept local for the reason that
    file's own copy documents (`models/registry/tests/_helpers.py` has
    no model-set builder, and the real one, `_set_with`, is module-
    private to `models/registry/tests/test_model_sets.py`) -- the
    convention `tools/rag/tests/test_jobs.py:41-45` also follows."""
    from models.contracts.roles import CHAT_CONVERSE_ROLE
    from models.registry.models import (
        ModelConnection, ModelSet, ModelSetEntitlement, ModelSetMember, RoleBinding,
    )

    connection = ModelConnection.objects.create(
        name=name, engine="test-inference", endpoint="http://fake-inference:1",
        model_id="a-chat-model", capabilities=["chat"])
    model_set = ModelSet.objects.create(name=f"set for {name}")
    ModelSetMember.objects.create(model_set=model_set, connection=connection)
    ModelSetEntitlement.objects.create(model_set=model_set, entitlement=entitlement)
    RoleBinding.objects.get_or_create(role_key=CHAT_CONVERSE_ROLE,
                                      defaults={"connection": connection})
    return connection


class TestTheWallOnAConversationOnlyShare:
    """F10's `else` leg (`agents/entitlements.py::wall_for`'s own
    docstring): a principal who may READ a conversation through a
    CONVERSATION-level share, but who is not admitted to its stream
    (`workstream_scope` answers `None` -- no `WorkstreamScope` in hand
    at all), still has the stream's wall narrow their picker through
    `thread.py`'s own `wall_for(conversation)` fallback. Proved on the
    picker, the one observable the render half of the wall has
    (`chat_picker_options`' own docstring: a connection the principal
    may not use "is never in the offered options"), the same surface
    `agents/tests/test_wall_seams.py::
    test_the_picker_omits_exactly_what_the_walled_turn_would_refuse`
    proves the OTHER three call sites on."""

    def test_a_not_admitted_recipients_picker_is_still_narrowed(self, client):
        from agents.workstreams import workstream_scope

        owner, reader = make_user(), make_user()
        agent = make_agent()
        inside_ent, outside_ent = make_entitlement(), make_entitlement()
        # READER HOLDS BOTH -- so an unnarrowed picker would offer both
        # models, and only the wall's own fallback keeps "outside" out.
        grant(inside_ent, user=reader)
        grant(outside_ent, user=reader)
        stream = _workstream(user_principal(owner))
        WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=inside_ent)
        inside = _connection_in_set_attached_to(inside_ent, name="inside the wall")
        outside = _connection_in_set_attached_to(outside_ent, name="outside the wall")
        conversation = create_conversation(user_principal(owner), agent, workstream=stream)
        # `USE`, not `VIEW`: the composer -- and the picker inside it --
        # only renders for a recipient who `may_post_to` the thread, and
        # the property under test lives in the picker's own markup.
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=reader,
                             level=Share.Level.USE)

        with posture(POSTURE_ENTERPRISE):
            # THE PRECONDITION ITSELF: a conversation-level share admits
            # no stream membership, so `thread.py` must take the `wall_for`
            # fallback leg, not the `stream_scope.wall` one -- if this
            # ever stopped being `None`, the test below would no longer
            # be exercising the fallback it exists to pin.
            assert workstream_scope(user_principal(reader), stream.pk) is None
            sign_in(client, reader)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()

        assert f'<option value="{inside.pk}"' in body
        assert f'<option value="{outside.pk}"' not in body


class TestTheContextMeter:
    """Feature A on the page: the line, the bar, the disclosure and the
    two clauses. Server-rendered, inside `.composer-block`, above the
    thread actions row."""

    def _long_prompt_agent(self, chars):
        return make_agent(slug=f"meter-{chars}", system_prompt="x" * chars)

    def test_the_line_renders_with_a_value_a_ceiling_and_a_percentage(
        self, client, bound_chat_role
    ):
        from agents.usage import CHARS_PER_TOKEN

        bound_chat_role.context_window = 1000
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 410)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Context ~" in body
        assert "of 1,000 tokens" in body
        assert 'id="context-tokens"' in body
        assert 'id="context-percent"' in body
        assert "estimate" in body

    def test_the_disclosure_is_a_details_element_carrying_the_method(
        self, client, bound_chat_role
    ):
        from agents.usage import DISCLOSURE_BODY, DISCLOSURE_SUMMARY

        conversation = make_conversation(agent=make_agent(slug="meter-disclosure"))
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert DISCLOSURE_SUMMARY in body
        assert DISCLOSURE_BODY[:60] in body

    def test_the_bar_width_comes_from_the_server_never_the_client(
        self, client, bound_chat_role
    ):
        from agents.usage import CHARS_PER_TOKEN

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 41)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "--context-fill: 41%" in body
        assert 'data-window="100"' in body

    def test_the_truncation_clause_is_present_but_hidden_when_nothing_is_dropped(
        self, client, bound_chat_role
    ):
        conversation = make_conversation(agent=make_agent(slug="meter-short"))
        make_turn(conversation=conversation, text="hi", state=Turn.State.DONE)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert 'id="context-truncation"' in body
        assert "no longer sent" in body
        marker = body[body.index('id="context-truncation"'):]
        assert "hidden" in marker[:200]

    def test_the_truncation_clause_is_unhidden_on_a_long_conversation(
        self, client, bound_chat_role
    ):
        from agents.limits import HISTORY_TURNS

        conversation = make_conversation(agent=make_agent(slug="meter-long"))
        for _ in range(HISTORY_TURNS + 3):
            make_turn(conversation=conversation, text="hi", state=Turn.State.DONE)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        marker = body[body.index('id="context-truncation"'):]
        assert "hidden" not in marker[:200]
        assert f"the oldest 3 of {HISTORY_TURNS + 3} messages are no longer sent" in body

    def test_at_ninety_percent_the_page_says_what_to_do_about_it(
        self, client, bound_chat_role
    ):
        # REVIEW FIX (task-3 review, finding 1): `{{ context_full_clause }}`
        # renders through Django's default autoescape -- no `|safe`, per
        # the repo's own gate against opting out of it on a rendered path
        # (`tools/rag/tests/test_views_upload_and_settings.py::
        # test_question_and_answer_are_escaped`). `FULL_CLAUSE` carries a
        # literal apostrophe, so the body holds `&#x27;`, not `'`; pinned
        # here through `django.utils.html.escape`, the same helper that
        # test uses, rather than hand-escaping or dropping the apostrophe.
        from django.utils.html import escape

        from agents.usage import CHARS_PER_TOKEN, FULL_CLAUSE

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 95)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert escape(FULL_CLAUSE) in body
        assert 'data-band="full"' in body

    def test_below_ninety_percent_it_does_not(self, client, bound_chat_role):
        # REVIEW FIX (task-3 review, finding 1): same escaped-form pin as
        # the ninety-percent test above, and just as load-bearing here --
        # pinning the raw (unescaped) `FULL_CLAUSE` would make this
        # negative assertion vacuously true forever, since the raw form
        # never appears in autoescaped output.
        from django.utils.html import escape

        from agents.usage import CHARS_PER_TOKEN, FULL_CLAUSE

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 10)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert escape(FULL_CLAUSE) not in body
        assert 'data-band="ok"' in body

    def test_a_refusal_that_still_has_a_binding_still_shows_a_ceiling(
        self, client, bound_chat_role, monkeypatch
    ):
        """`preflight_turn`'s NO_TOOL_CALLING leg returns `ok=False`
        with a REAL `resolved`. The branch is `check.resolved is None`,
        never `check.ok` (spec review m1)."""
        from agents.runtime.preflight import Preflight, preflight_turn

        bound_chat_role.context_window = 4096
        bound_chat_role.save()
        conversation = make_conversation(agent=make_agent(slug="meter-refused"))
        real = preflight_turn

        def refusing(agent, connection, **kwargs):
            check = real(agent, connection, **kwargs)
            return Preflight(False, "no_tool_calling",
                             "The bound model cannot call this agent's tools.",
                             check.resolved, check.answered_by, ())

        monkeypatch.setattr("agents.chat.views.thread.preflight_turn", refusing)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "of 4,096 tokens" in body

    def test_with_no_model_bound_the_page_is_200_with_a_ceiling_free_line(self, client):
        conversation = make_conversation(agent=make_agent(slug="meter-unbound"))
        response = client.get(reverse("chat-conversation", args=[conversation.id]))
        body = response.content.decode()
        assert response.status_code == 200
        assert "Context ~" in body
        assert "no limit set for this connection" not in body

    def test_the_engine_default_sentence_is_admin_only_and_never_built_for_a_member(
        self, client, bound_chat_role
    ):
        # REVIEW FIX (task-3 review, finding 1): `ENGINE_DEFAULT_SENTENCE`
        # also carries a literal apostrophe (`engine's`), autoescaped the
        # same way as `FULL_CLAUSE` above -- both assertions pinned
        # through `escape(...)`, the negative one included, for the same
        # vacuous-pass reason.
        from django.utils.html import escape

        from agents.usage import ENGINE_DEFAULT_SENTENCE

        member = make_user()
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            conversation = make_conversation(
                agent=make_agent(slug="meter-default"),
                **owner_fields(user_principal(member)))
            sign_in(client, member)
            member_body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
            assert escape(ENGINE_DEFAULT_SENTENCE) not in member_body
            client.logout()
            sign_in(client, make_admin())
            admin_body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert escape(ENGINE_DEFAULT_SENTENCE) in admin_body

    def test_the_script_count_is_unchanged(self, client, bound_chat_role):
        conversation = make_conversation(agent=make_agent(slug="meter-scripts"))
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count("<script") == 4

    def test_the_meter_costs_the_same_on_a_short_and_a_long_conversation(
        self, client, bound_chat_role
    ):
        """Equality under scale -- the pin this feature's query budget is
        actually made of. A new CONSTANT query is allowed here; a
        per-turn one is not.

        CHAT CLUSTER, FEATURE C EXTENDS IT RATHER THAN ADDING A SECOND
        COPY: the per-turn edit disclosure asks a predicate that would be
        the obvious per-row N+1 on exactly this page, so it is asked ONCE
        per render (`agents.visibility.may_edit_any_turn`) and this
        equality is what would go red if a later reader moved it onto the
        card. The two assertions below the captures are what keep that
        non-vacuous: every turn here really does render the disclosure,
        so the flat cost is being measured with the predicate live rather
        than on a page that never asks it.

        TASK 14 EXTENDS IT AGAIN, THE SAME WAY: both threads are branches
        of the SAME parent, so the provenance banner's own one-query
        resolution (`thread.py`'s own `branched_from` lookup, threaded
        through the SAME `settings_row`) runs on both renders this test
        measures rather than on neither -- a bare `branched_from_id is
        None` conversation would leave that lookup entirely unexercised
        by this pin, and a later reader who moved it onto a per-turn
        path would have nothing here to catch it. The two assertions
        below the meter's own pair are what make that non-vacuous: the
        banner really renders on both pages.

        THE CLOSING WAVE EXTENDS IT ONCE MORE, for the same reason and in
        the same shape: whole-branch review I-2 added a SECOND bounded
        read to this page -- `agents.chat.service.branch_point_ordinal`,
        the parent-side `.count()` behind "at your message N" -- and it
        is bounded by the PARENT's length, not this conversation's, so
        equality under scale is exactly the right pin for it. The parent
        below now carries a real user turn so the ordinal is a live
        number on both renders rather than a count over nothing, and the
        two "at your message 1" assertions at the end are what keep that
        non-vacuous.
        """
        from agents.limits import HISTORY_TURNS

        agent = make_agent(slug="meter-scale")
        parent = make_conversation(agent=agent, title="Meter parent")
        make_turn(conversation=parent, text="parent message", state=Turn.State.DONE)
        short = make_conversation(agent=agent, branched_from=parent, branched_at_index=0)
        make_turn(conversation=short, text="hi", state=Turn.State.DONE)
        long_one = make_conversation(agent=agent, branched_from=parent, branched_at_index=0)
        for _ in range(HISTORY_TURNS * 3):
            make_turn(conversation=long_one, text="hi", state=Turn.State.DONE)
        client.get(reverse("chat-conversation", args=[short.id]))   # warm-up, unmeasured
        with CaptureQueriesContext(connection) as one:
            short_response = client.get(reverse("chat-conversation", args=[short.id]))
        with CaptureQueriesContext(connection) as many:
            long_response = client.get(reverse("chat-conversation", args=[long_one.id]))
        assert short_response.status_code == 200
        assert long_response.status_code == 200
        assert len(many) == len(one)
        short_body = short_response.content.decode()
        long_body = long_response.content.decode()
        assert short_body.count("Send from here") == 1
        assert long_body.count("Send from here") == HISTORY_TURNS * 3
        assert short_body.count("Branched from") == 1
        assert long_body.count("Branched from") == 1
        assert short_body.count("at your message 1") == 1
        assert long_body.count("at your message 1") == 1

    def test_a_reader_who_may_not_upload_pays_no_extra_query_for_the_stream(
        self, client, bound_chat_role
    ):
        """`visible_conversations` only `select_related("agent")`, and
        `thread_context` dereferences `conversation.workstream` ONLY on
        its `may_upload_here` branch -- so without
        `visible_conversation_or_404`'s widened read this render would
        pay a lazy FK read that the spec's own query budget did not
        allow for."""
        stream = _workstream(name="Meter stream", instructions="be brief")
        loose = make_conversation(agent=make_agent(slug="meter-loose"))
        in_stream = make_conversation(agent=make_agent(slug="meter-stream"),
                                      workstream=stream)
        client.get(reverse("chat-conversation", args=[loose.id]))   # warm-up, unmeasured
        with CaptureQueriesContext(connection) as loose_queries:
            client.get(reverse("chat-conversation", args=[loose.id]))
        with CaptureQueriesContext(connection) as stream_queries:
            client.get(reverse("chat-conversation", args=[in_stream.id]))

        # THE ONE EXCLUDED SHAPE IS PRE-EXISTING AND UNRELATED TO THIS
        # TASK: `thread_context`'s own `stream_scope =
        # scope_for_conversation(...)` call (present before this task;
        # it also decides `may_upload_here`, the wall, and the
        # attachments corpus) runs a real AUTHORIZATION check --
        # `workstream_scope` -> `_visible_row` ->
        # `visible_workstreams(principal).filter(pk=...).first()` -- for
        # any IN-STREAM conversation, whatever this task does. Its
        # shape, `... ORDER BY "agents_workstream"."updated_at" DESC
        # LIMIT 1`, is a `.first()` call, and it is DISTINCT from the
        # LAZY FK READ this task's Step 3 exists to remove: an
        # unguarded `conversation.workstream` dereference is a
        # `.get()`-shaped fetch, `... LIMIT 21`, with no `ORDER BY`.
        # Verified by temporarily reverting `visible_conversation_or_
        # 404`'s `select_related("workstream")`: the `LIMIT 21` read
        # reappears in the stream case and only there -- confirming it
        # is exactly what widening the read removes, and that the
        # `LIMIT 1` read survives regardless, because it was never this
        # task's lazy-FK defect to begin with.
        _EXCLUDED_SHAPE = 'order by "agents_workstream"."updated_at" desc limit 1'

        def _standalone_stream_reads(captured):
            # Excluding only that one known shape keeps this pin honest:
            # it still fails the moment `context_usage`'s own
            # dereference regresses into a NEW standalone read.
            return sum(
                1 for q in captured.captured_queries
                if "agents_workstream" in q["sql"].lower()
                and " join " not in q["sql"].lower()
                and _EXCLUDED_SHAPE not in q["sql"].lower()
            )

        # REVIEW FIX (task-3 review, finding 3): the exclusion above is a
        # negative string match, which would silently swallow a SECOND,
        # different authorization read too. Pin the assumption it
        # actually rests on -- exactly one pre-existing `scope_for_
        # conversation` read on the in-stream render, none on the loose
        # one (a loose conversation's `workstream_id` is `None`, and
        # `scope_for_conversation` returns `None` without querying) --
        # so a future second read is caught here rather than excluded.
        excluded_in_stream = [q for q in stream_queries.captured_queries
                              if _EXCLUDED_SHAPE in q["sql"].lower()]
        excluded_in_loose = [q for q in loose_queries.captured_queries
                             if _EXCLUDED_SHAPE in q["sql"].lower()]
        assert len(excluded_in_stream) == 1, (
            "exactly one pre-existing scope_for_conversation read expected")
        assert len(excluded_in_loose) == 0, (
            "a loose conversation should never run scope_for_conversation's query")

        assert _standalone_stream_reads(stream_queries) == _standalone_stream_reads(
            loose_queries)


class TestTheContextMeterOnThePollPath:
    """Three integers, on two of the five bodies, with no binding
    resolved anywhere on this path (spec review M3, m7, R2)."""

    def _queued_pair(self):
        conversation = make_conversation(agent=make_agent(slug="poll-meter"))
        make_turn(conversation=conversation, text="hello", state=Turn.State.DONE)
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="", state=Turn.State.QUEUED, queue_job_id=1)
        return conversation, assistant

    def test_a_queued_body_carries_the_three_integers_and_nothing_else(
        self, client, fake_queued_job
    ):
        _conversation, assistant = self._queued_pair()
        body = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        assert set(body["context"]) == {"estimated_tokens", "replayed_turns", "total_turns"}
        assert all(isinstance(value, int) for value in body["context"].values())

    def test_the_corpus_grows_at_QUEUE_time_not_only_at_finish(
        self, client, fake_queued_job
    ):
        """`agents.chat.service.start_turn` writes the USER turn DONE in
        the same transaction as the QUEUED placeholder, so a meter that
        waited for `done` would be stale for the whole in-flight window
        -- precisely when the reader is deciding whether to compact."""
        _conversation, assistant = self._queued_pair()
        body = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        assert body["context"]["replayed_turns"] == 1
        assert body["context"]["estimated_tokens"] > 0

    def test_a_done_body_carries_it_too(self, client):
        conversation = make_conversation(agent=make_agent(slug="poll-done"))
        make_turn(conversation=conversation, text="hello", state=Turn.State.DONE)
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="the answer", state=Turn.State.DONE, queue_job_id=2)
        body = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        assert body["context"]["replayed_turns"] == 2

    def test_no_window_no_percentage_and_no_sentence_ever_travel(self, client):
        """DEVIATION FROM THE BRIEF: the brief's own `assert "estimate"
        not in serialized` cannot pass alongside the interface's own
        mandated key name -- `"estimated_tokens"` starts with the
        eight letters "estimate", so the substring is present in EVERY
        legal body, including the one `_context_body`'s own docstring
        and `test_a_queued_body_carries_the_three_integers_and_nothing_
        else` require. Re-pinned to the assertion's real intent -- no
        rendered SENTENCE (a "~30% estimate" clause, the page's own
        prose) rides the JSON body -- by counting occurrences: "estimate"
        may appear only as the fixed prefix of the key name itself,
        never as a free word a second time."""
        conversation = make_conversation(agent=make_agent(slug="poll-window"))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="a", state=Turn.State.DONE, queue_job_id=3)
        serialized = str(client.get(
            reverse("chat-turn-status", args=[assistant.pk])).json())
        assert "window" not in serialized
        assert "percent" not in serialized
        assert serialized.count("estimate") == serialized.count("estimated_tokens")

    def test_a_running_body_carries_no_context_key(self, client, fake_running_job):
        conversation = make_conversation(agent=make_agent(slug="poll-running"))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="", state=Turn.State.RUNNING, queue_job_id=4)
        assert "context" not in client.get(
            reverse("chat-turn-status", args=[assistant.pk])).json()

    def test_a_failed_and_a_cancelled_body_carry_no_context_key(self, client):
        conversation = make_conversation(agent=make_agent(slug="poll-terminal"))
        failed = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                           state=Turn.State.FAILED, error="nope", queue_job_id=5)
        cancelled = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                              state=Turn.State.CANCELLED, error="stopped", queue_job_id=6)
        assert "context" not in client.get(
            reverse("chat-turn-status", args=[failed.pk])).json()
        assert "context" not in client.get(
            reverse("chat-turn-status", args=[cancelled.pk])).json()

    def test_no_binding_is_resolved_on_the_poll_path(self, client, monkeypatch):
        """No `preflight_turn`, no `resolve_chat`, no wall read, no
        tool-access read. The inverse of "the page pays nothing for it"
        would be every open tab paying for it every two seconds.

        PATCHED AT THE LEAF, NOT AT A NAME THIS MODULE NEVER IMPORTS.
        `agents/chat/views/turns.py`'s import block does not carry
        `preflight_turn` at all, so patching THAT name intercepts
        nothing and the assertion holds whatever the implementation
        does -- a test that pins nothing.
        `models.contracts.bindings.resolve` is what `resolve_chat`
        really reaches, and the query sweep beside it is the second
        belt: a binding resolution cannot happen without touching a
        `models_`-prefixed table."""
        called = []
        monkeypatch.setattr("models.contracts.bindings.resolve",
                            lambda *a, **k: called.append("resolve"))
        conversation = make_conversation(agent=make_agent(slug="poll-nobind"))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="a", state=Turn.State.DONE, queue_job_id=7)
        with CaptureQueriesContext(connection) as captured:
            client.get(reverse("chat-turn-status", args=[assistant.pk]))
        assert called == []
        assert [q for q in captured.captured_queries
                if "models_" in q["sql"].lower()] == []

    def test_the_context_key_costs_exactly_the_two_reads_it_budgets(self, client):
        """THE ABSOLUTE-DELTA PIN spec §9 asks for, re-budgeted from one
        read to two (see "Deviations from the spec", §1).

        Equality-under-scale alone cannot notice a THIRD constant query
        arriving later, which is exactly the regression this pin exists
        to catch. `_context_body` is asked DIRECTLY, over a turn loaded
        the way `visible_turn` loads it, so the number is the feature's
        own rather than the surrounding body's."""
        from agents.chat.views.turns import _context_body

        conversation = make_conversation(agent=make_agent(slug="poll-delta"))
        make_turn(conversation=conversation, text="hello", state=Turn.State.DONE)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                         state=Turn.State.DONE, queue_job_id=13)
        turn = Turn.objects.select_related(
            "conversation", "conversation__agent", "conversation__workstream").get(
                pk=turn.pk)
        with CaptureQueriesContext(connection) as captured:
            body = _context_body(turn)
        assert set(body) == {"estimated_tokens", "replayed_turns", "total_turns"}
        assert len(captured) == 2, [q["sql"] for q in captured.captured_queries]

    def test_the_select_related_is_wide_enough_to_keep_the_budget_honest(self, client):
        """THE PIN THAT PROVES `visible_turn`'s widened `select_related`
        (spec review R2). Narrowing it back to `("conversation",)` -- or
        narrowing it PARTIALLY, dropping only one of the two JOINs --
        adds a lazy FK read to every tick of every open tab, and this
        pin is what turns red rather than quietly costing them.

        DEVIATION FROM THE BRIEF (task-4 review, ruling 2 + finding 5):
        the brief's own assertion is bare equality
        (`len(stream_queries) == len(loose_queries)`), which this
        endpoint has never satisfied -- `_attachments_by_turn`'s own
        `scope_for_conversation` call (round 13, present since before
        this task) resolves an in-stream conversation's wall through
        `workstream_scope`, at a real, pre-existing, five-query cost
        (one `agents_workstream` `.first()` authorization read, three
        `identity_identitysettings` ownership reads, one
        `rag_workstreampin` read) that is not reachable from
        `agents/visibility.py` or `agents/chat/views/turns.py`, the only
        two production files this task touches -- SEE THE WHOLE-BRANCH
        BACKLOG ITEM BELOW. A first re-pin fixed that gap at the magic
        constant 5, which worked but coupled this task's OWN regression
        pin to a cost this task does not own: any future change to
        `_attachments_by_turn`/`attachments_for` would turn this red for
        the wrong reason. Re-expressed instead in the SAME shape terms
        `TestTheContextMeter::test_a_reader_who_may_not_upload_pays_no_
        extra_query_for_the_stream` (task 3) already established for
        this exact ambiguity: a lazy FK fetch is a `.get()`-shaped
        query, `... LIMIT 21`, no `ORDER BY`; the pre-existing
        authorization read is a `.first()`-shaped query, `... ORDER BY
        "agents_workstream"."updated_at" DESC LIMIT 1`. Counting
        STANDALONE (non-JOIN) reads of EACH shape, on EACH table
        (`agents_agent` for the `conversation__agent` JOIN,
        `agents_workstream` for `conversation__workstream`), asserts
        zero lazy reads on both sides without needing to know or state
        the authorization surcharge's size at all -- so a future change
        to that unrelated code path can move the total query count
        however it likes without touching this pin.

        Verified by temporarily narrowing `visible_turn`'s own
        `select_related` twice and restoring it after each: dropping
        `"conversation__agent"` alone puts a `... FROM "agents_agent"
        WHERE "agents_agent"."id" = <id> LIMIT 21` read on BOTH the
        loose and the stream side (the FK is non-null, so both turns
        pay it) -- caught by the `agents_agent` half below. Dropping
        `"conversation__workstream"` alone puts a `... FROM
        "agents_workstream" WHERE "agents_workstream"."id" = <id> LIMIT
        21` read on the STREAM side only (a loose conversation's
        `workstream_id` is `None`, so Django resolves `None` with no
        query) -- caught by the `agents_workstream` half below, which
        the `_EXCLUDED_SHAPE` guard keeps distinct from the pre-existing
        `.first()` authorization read so narrowing this JOIN turns the
        test red instead of hiding behind that read's own presence.
        """
        stream = _workstream(name="Poll stream", instructions="be brief")
        loose = make_conversation(agent=make_agent(slug="poll-loose"))
        in_stream = make_conversation(agent=make_agent(slug="poll-stream"),
                                      workstream=stream)
        loose_turn = make_turn(conversation=loose, role=Turn.Role.ASSISTANT, text="a",
                               state=Turn.State.DONE, queue_job_id=8)
        stream_turn = make_turn(conversation=in_stream, role=Turn.Role.ASSISTANT, text="a",
                                state=Turn.State.DONE, queue_job_id=9)
        client.get(reverse("chat-turn-status", args=[loose_turn.pk]))   # warm-up
        with CaptureQueriesContext(connection) as loose_queries:
            client.get(reverse("chat-turn-status", args=[loose_turn.pk]))
        with CaptureQueriesContext(connection) as stream_queries:
            client.get(reverse("chat-turn-status", args=[stream_turn.pk]))

        # `.first()`-shaped: the pre-existing, task-unrelated
        # `scope_for_conversation` authorization read (round 13) --
        # named here, not fixed at a count, because THIS test does not
        # own its size. WHOLE-BRANCH BACKLOG (task-4 review, finding 4):
        # spec review R2's intent is that the poll path pays NOTHING
        # extra for an in-stream conversation, and today it pays this
        # authorization read plus its own downstream ownership/pin
        # reads on every tick of every open in-stream tab -- a real gap
        # against R2, not this task's file list to close.
        _EXCLUDED_SHAPE = 'order by "agents_workstream"."updated_at" desc limit 1'

        def _standalone_lazy_reads(captured, table: str) -> int:
            # `.get()`-shaped: `... LIMIT 21`, no `ORDER BY` -- the lazy
            # FK fetch a narrowed `select_related` would reintroduce.
            # Never a JOIN (part of the SAME query the view already
            # runs) and never the excluded `.first()` authorization
            # shape above.
            return sum(
                1 for q in captured.captured_queries
                if table in q["sql"].lower()
                and " join " not in q["sql"].lower()
                and _EXCLUDED_SHAPE not in q["sql"].lower()
            )

        # THE COUNTED-EXCLUSION GUARD (same discipline as task 3's own
        # `test_a_reader_who_may_not_upload_pays_no_extra_query_for_the_
        # stream`, review fix finding 3 there): a bare negative string
        # match would silently swallow a SECOND, different `.first()`-
        # shaped authorization read too. Pin the assumption
        # `_standalone_lazy_reads` actually rests on -- exactly one
        # pre-existing `scope_for_conversation` read on the in-stream
        # tick, none on the loose one (a loose conversation's
        # `workstream_id` is `None`, and `scope_for_conversation`
        # returns `None` without querying) -- so a future second read is
        # caught here rather than excluded away.
        excluded_in_stream = [q for q in stream_queries.captured_queries
                              if _EXCLUDED_SHAPE in q["sql"].lower()]
        excluded_in_loose = [q for q in loose_queries.captured_queries
                             if _EXCLUDED_SHAPE in q["sql"].lower()]
        assert len(excluded_in_stream) == 1, (
            "exactly one pre-existing scope_for_conversation read expected")
        assert len(excluded_in_loose) == 0, (
            "a loose conversation should never run scope_for_conversation's query")

        for table in ("agents_agent", "agents_workstream"):
            assert _standalone_lazy_reads(loose_queries, table) == 0, (
                f"a loose conversation's poll tick should read no standalone {table} row")
            assert _standalone_lazy_reads(stream_queries, table) == 0, (
                f"an in-stream conversation's poll tick should read no standalone "
                f"lazy {table} row (the excluded .first() authorization read aside)")

    def test_the_poll_cost_does_not_scale_with_the_conversations_length(self, client):
        from agents.limits import HISTORY_TURNS

        agent = make_agent(slug="poll-scale")
        short = make_conversation(agent=agent)
        long_one = make_conversation(agent=agent)
        for _ in range(HISTORY_TURNS * 3):
            make_turn(conversation=long_one, text="hi", state=Turn.State.DONE)
        short_turn = make_turn(conversation=short, role=Turn.Role.ASSISTANT, text="a",
                               state=Turn.State.DONE, queue_job_id=10)
        long_turn = make_turn(conversation=long_one, role=Turn.Role.ASSISTANT, text="a",
                              state=Turn.State.DONE, queue_job_id=11)
        client.get(reverse("chat-turn-status", args=[short_turn.pk]))   # warm-up
        with CaptureQueriesContext(connection) as one:
            client.get(reverse("chat-turn-status", args=[short_turn.pk]))
        with CaptureQueriesContext(connection) as many:
            client.get(reverse("chat-turn-status", args=[long_turn.pk]))
        assert len(many) == len(one)

    def test_the_page_and_a_poll_tick_cannot_disagree_about_the_ceiling(
        self, client, bound_chat_role
    ):
        """The regression spec review M3 names: a poll body that computed
        its own denominator would answer with the AGENT's role binding
        where the page answered with the PICKED connection. The window
        never travels, so the page's `data-window` is the only one there
        is, and a tick cannot contradict it."""
        from models.registry.models import ModelConnection

        picked = ModelConnection.objects.create(
            name="picked", engine="ollama", endpoint="http://localhost:11434",
            model_id="another-chat-model", capabilities=["chat"], context_window=2048)
        conversation = make_conversation(agent=make_agent(slug="poll-picked"))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                              state=Turn.State.DONE, queue_job_id=12)
        page = client.get(
            f"{reverse('chat-conversation', args=[conversation.id])}?connection={picked.pk}"
        ).content.decode()
        body = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        assert 'data-window="2048"' in page
        assert "window" not in str(body)

    def test_the_script_count_is_still_unchanged(self, client, bound_chat_role):
        conversation = make_conversation(agent=make_agent(slug="poll-scripts"))
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count("<script") == 4
