"""Edit a past prompt: the disclosure, the gate on the POST, and what
the branch a POST makes actually holds.

THE TWO HALVES OF FEATURE C's LAST VISIBLE STEP. `agents.visibility`
(Task 12) answers "may this principal branch here" and writes the
branch; nothing there renders, redirects, or starts a turn. Everything
below is the chat column's own half: the `<details>` disclosure on an
own, finished user turn, the POST that validates BEFORE it writes, and
the poller's two carry-overs -- the swapped block's edit form and the
picker's own selection.

EVERY REFUSAL RUNS UNDER `posture(POSTURE_ENTERPRISE)`, deliberately:
on an open box `sees_all_content` answers True for everyone, so a
refusal asserted there would prove nothing at all.
"""
from __future__ import annotations

import itertools

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    bind_chat_role, fake_queue_down, fake_queued_job, fake_running_job, fake_turn_queue,
    make_admin, make_agent, make_conversation, make_turn, make_user, posture, sign_in,
    user_principal,
)
from agents.models import Conversation, Share, Turn
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE
from models.contracts.roles import CHAT_CONVERSE_ROLE

pytestmark = pytest.mark.django_db

_slugs = itertools.count()


def _own_thread(owner, *, texts=("first", "second")):
    """A conversation owned by `owner` whose every turn is a FINISHED
    USER turn -- so `may_edit_turn`'s conversation-wide in-flight clause
    passes and each of them is individually editable.

    THE SLUG IS FRESH PER CALL because `Agent.slug` is unique
    (`uniq_agent_slug_ci`) and several tests below build two threads.
    """
    conversation = make_conversation(
        agent=make_agent(slug=f"edit-thread-{next(_slugs)}"),
        **owner_fields(user_principal(owner)))
    turns = [make_turn(conversation=conversation, role=Turn.Role.USER, text=text,
                       state=Turn.State.DONE) for text in texts]
    return conversation, turns


class TestTheRowPredicateIsSharedNotSpelledTwice:
    """FEATURE C REVIEW, I1. The per-turn half of editability used to be
    written twice -- negatively in `agents.visibility.may_edit_turn`
    (`role != USER or depth != 0 or state != DONE`) and positively in
    `agents.chat.rendering.turn_card` -- with nothing pinning that the
    two agreed.

    BOTH DRIFTS ARE BAD, AND IN OPPOSITE DIRECTIONS. A card copy that
    grew WIDER renders a disclosure whose own POST answers 404, which is
    the very thing the in-flight clause is HIDDEN rather than merely
    refused to prevent. One that grew NARROWER silently removes an
    available control. Per-condition tests elsewhere in this file catch
    neither, because each asserts about one spelling only: a fourth
    condition added to the predicate would be invisible to all of them.

    So this class walks the whole `(role, depth, state)` truth table
    through BOTH call sites and asserts they agree row by row.
    """

    def test_the_card_and_the_predicate_agree_on_every_row_shape(self):
        from agents.chat.rendering import turn_card
        from agents.visibility import is_editable_turn_row, may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation = make_conversation(
                agent=make_agent(slug="truth-table"),
                **owner_fields(user_principal(owner)))
            principal = user_principal(owner)
            shapes = [
                (role, depth, state)
                for role in (Turn.Role.USER, Turn.Role.ASSISTANT, Turn.Role.TOOL)
                for depth in (0, 1)
                for state in (Turn.State.DONE, Turn.State.QUEUED,
                              Turn.State.FAILED, Turn.State.CANCELLED)
            ]
            for role, depth, state in shapes:
                turn = make_turn(conversation=conversation, role=role, depth=depth,
                                 state=state, text="t")
                # `may_edit_turn` also asks the CONVERSATION half, which
                # a non-terminal row would flip for the whole thread --
                # so each shape is measured on its own conversation-free
                # terms and the row predicate is the subject.
                card = turn_card(turn, may_edit=True)
                assert card["may_edit"] is is_editable_turn_row(turn), (
                    role, depth, state)
                turn.delete()

            # AND THE TWO REALLY ARE ONE FUNCTION, not two that happen to
            # agree: the POST's own gate is built from the same call.
            editable = make_turn(conversation=conversation, role=Turn.Role.USER,
                                 depth=0, state=Turn.State.DONE, text="t")
            assert is_editable_turn_row(editable) is True
            assert may_edit_turn(principal, conversation, editable) is True
            assert turn_card(editable, may_edit=True)["may_edit"] is True

    def test_widening_the_shared_row_predicate_moves_both_answers_at_once(
        self, client, monkeypatch, fake_turn_queue
    ):
        """THE AGREEMENT PIN PROPER. Monkeypatching the ONE definition to
        admit an ASSISTANT row must change the rendered disclosure AND
        the POST's answer TOGETHER -- which is only possible while both
        call the same function. Before I1 this test could not be written
        at all: there was no single object to patch, and patching either
        spelling would have moved one answer and not the other."""
        import agents.chat.rendering as rendering_module
        import agents.visibility as visibility_module

        owner = make_user()
        bind_chat_role(CHAT_CONVERSE_ROLE, name="widen-role")
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            assistant = make_turn(conversation=conversation,
                                  role=Turn.Role.ASSISTANT, text="answer",
                                  state=Turn.State.DONE)
            sign_in(client, owner)

            url = reverse("chat-turn-edit", args=[conversation.id, assistant.pk])
            refused = client.post(url, {"text": "edited"})
            body_before = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
            assert refused.status_code == 404
            assert body_before.count("Send from here") == 2   # the two USER turns

            # ONE definition widened, in the one module that holds it,
            # and rebound in `rendering`'s own namespace too because it
            # imported the name rather than the module.
            widened = (lambda turn: turn.depth == 0
                       and turn.state == Turn.State.DONE)
            monkeypatch.setattr(visibility_module, "is_editable_turn_row", widened)
            monkeypatch.setattr(rendering_module, "is_editable_turn_row", widened)

            allowed = client.post(url, {"text": "edited"})
            body_after = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert allowed.status_code == 302                     # the POST moved
        assert body_after.count("Send from here") == 3        # and so did the page


class TestTheDisclosure:
    def test_an_own_finished_user_turn_offers_it(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Edit and carry on from here" in body
        assert "Send from here" in body

    def test_it_says_what_will_happen_before_the_button(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "starts a new conversation with everything before this message" in body
        assert "The original stays as it is" in body
        assert "not carried over" in body

    def test_an_assistant_card_never_offers_it(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                      text="answer", state=Turn.State.DONE)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count("Send from here") == 2

    def test_a_view_share_recipient_is_never_offered_it(self, client):
        from agents.visibility import share_conversation

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            share_conversation(user_principal(owner), conversation, user=recipient,
                               level=Share.Level.VIEW)
            sign_in(client, recipient)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Send from here" not in body

    def test_a_use_share_recipient_is_never_offered_it_either(self, client):
        """Spec review M2, the half a VIEW recipient alone cannot prove:
        a `use` recipient may POST into this thread and is STILL refused
        the copy -- `may_manage_conversation`, never `may_post_to`."""
        from agents.visibility import share_conversation

        owner, recipient = make_user(), make_user(username="use-recipient")
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            share_conversation(user_principal(owner), conversation, user=recipient,
                               level=Share.Level.USE)
            sign_in(client, recipient)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Send from here" not in body

    def test_it_is_not_offered_while_a_turn_is_running(self, client):
        """The in-flight clause is CONVERSATION-WIDE, so the control is
        HIDDEN rather than merely refused -- a page that rendered a
        button its own POST would 404 is the defect this pins."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.QUEUED)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Send from here" not in body

    def test_it_carries_the_plain_attach_input_and_neither_script_fragment(
        self, client
    ):
        """Spec review m8. `chat/_composer.html` carries TWO script
        blocks (`chat/_attach_dragdrop.html` and `chat/_enter_to_send.
        html`) and this disclosure renders once per eligible user turn,
        so including the COMPOSER would multiply the page's pinned
        script count by the number of editable messages.

        COUNTED, NOT MERELY ABSENT: the composer renders each of those
        two scripts once on this very page, so a bare `not in body`
        would be false for a reason that has nothing to do with this
        feature. The pin is that there is still exactly ONE of each,
        beside a `+ Add files` door that now renders three times.
        """
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        # Two markers that occur in those fragments' RUNNING CODE and
        # nowhere else in the rendered page (a `{% templatetag openblock
        # %} comment {% templatetag closeblock %}` block is stripped
        # before render; a `//` line in `conversation.html`'s own script
        # is not, which is why the enter-to-send marker below is the
        # IME guard rather than the selector that comment mentions).
        assert body.count("function mergeInFiles(") == 1
        assert body.count("event.keyCode === 229") == 1
        assert body.count("+ Add files") == 3        # the composer's, plus two turns'

    def test_ten_editable_messages_still_render_four_script_tags(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(
                owner, texts=tuple(f"m{i}" for i in range(10)))
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count("Send from here") == 10
        assert body.count("<script") == 4

    def test_a_polled_done_swap_shows_exactly_what_a_reload_shows(self, client):
        """THE INVARIANT `agents/chat/views/turns.py` STATES IN FOUR
        DOCSTRINGS, and the reason a first draft of this plan got it
        wrong. `_done_body`: "a poller that swapped in less than that
        would show strictly less than the same thread shows after F5 --
        silently, and only for the operator who waited rather than
        reloading." `_group_html`: the queued, running and done bodies
        "can never render the group differently from one another or from
        a reload." `turn_group_cards`: "exactly what a full page reload
        already shows for this turn." `_attachments_by_turn`: "a poll
        swap's chips are NEVER stale relative to what a reload would
        show."
        """
        from agents.tests._helpers import _workstream

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            # IN A STREAM THE OWNER MAY UPLOAD TO, deliberately: the
            # BUTTON alone would pass with `_edit_context` narrowed back
            # to two keys, and the placement chooser is the whole reason
            # `attach_workstream` travels. `chat/_attach_files.html`
            # gates the middle radio AND the remember-this-choice block
            # on it, so a two-key poll body renders two radios where a
            # reload renders three -- which is this test's real subject.
            stream = _workstream(**owner_fields(user_principal(owner)))
            conversation = make_conversation(agent=make_agent(slug="parity-thread"),
                                             workstream=stream,
                                             **owner_fields(user_principal(owner)))
            make_turn(conversation=conversation, role=Turn.Role.USER, text="first",
                      state=Turn.State.DONE)
            assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                                  text="answer", state=Turn.State.DONE, queue_job_id=1)
            sign_in(client, owner)
            polled = client.get(
                reverse("chat-turn-status", args=[assistant.pk])).json()["html"]
            reloaded = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Send from here" in polled
        assert "Send from here" in reloaded
        assert "This workstream only" in polled
        assert "This workstream only" in reloaded
        # The declared sentence travels too, or a swapped disclosure
        # would open onto a form that never says what it is about to do.
        assert "The original stays as it is" in polled
        assert "The original stays as it is" in reloaded

    def test_the_poller_carries_the_pickers_selection_into_a_swapped_form(self, client):
        """The one value that cannot travel on the wire, carried across
        ON THE PAGE instead: the swapped block's `connection` field is
        rendered empty by the server, and the poller copies the
        COMPOSER's own server-rendered value into it. Asserted on the
        script rather than on a rendered DOM, because there is no
        JavaScript engine in this suite -- the same way every other
        poller behaviour on this surface is pinned."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "function carryConnection(" in body
        assert "carryConnection(refreshed)" in body
        assert 'input[name="connection"]' in body
        # No prose composed, nothing sent, no new tag.
        assert "innerHTML" not in body.split("function carryConnection(")[1][:600]
        assert body.count("<script") == 4

    def test_a_queued_and_a_running_tick_pay_nothing_for_the_predicate(
        self, client, fake_queued_job, fake_running_job
    ):
        """`may_edit_any_turn` is PROVABLY FALSE on those two paths --
        the turn being polled IS a non-terminal turn in that
        conversation -- so `_queued_body` and `_running_body` compute
        nothing at all and the every-two-seconds path is untouched. The
        cost lives on the once-per-finished-turn `done` tick and nowhere
        else."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            queued = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                               text="", state=Turn.State.QUEUED, queue_job_id=2)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-turn-status", args=[queued.pk])).json()["html"]
        assert "Send from here" not in body

    def test_the_done_tick_costs_at_most_the_budgeted_extra_reads(self, client):
        """The once-per-turn price of keeping the invariant, pinned so a
        later reader cannot quietly widen it: FLAT between two
        equivalent conversations. The absolute number is whatever the
        body already cost plus the predicate's own bounded reads; what
        must never appear is a cost that grows with the thread."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            done = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                             text="answer", state=Turn.State.DONE, queue_job_id=3)
            other, _t = _own_thread(owner, texts=tuple(f"n{i}" for i in range(12)))
            other_done = make_turn(conversation=other, role=Turn.Role.ASSISTANT,
                                   text="answer", state=Turn.State.DONE, queue_job_id=4)
            sign_in(client, owner)
            client.get(reverse("chat-turn-status", args=[done.pk]))   # warm-up
            with CaptureQueriesContext(connection) as one:
                client.get(reverse("chat-turn-status", args=[done.pk]))
            with CaptureQueriesContext(connection) as two:
                client.get(reverse("chat-turn-status", args=[other_done.pk]))
        assert len(two) == len(one)

    def test_two_editable_messages_render_no_duplicate_dom_id(self, client):
        """`chat/_attach_files.html` hardcodes `id="attach-files"` and a
        `<label for="attach-files">`, and the COMPOSER already renders
        one. Every `<label for=...>` resolves to the FIRST match in
        document order, so without a per-turn id every "+ Add files"
        inside an edit form would open the COMPOSER's picker and stage
        the chosen file onto a new turn instead of the branch. The input
        carries `class="attach-input"` (the label-button pattern), so
        the label is the only way to reach it -- this is a functional
        bug, not an HTML-validity nit."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner, texts=("a", "b", "c"))
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        # `== 1`, not `<= 1`: the bare count also passes at ZERO, which
        # would go green on a posture with no attach door at all and pin
        # nothing. This fixture has the composer's own door, so the
        # composer's is the one that must survive -- and each edit form
        # must carry its own suffixed id instead.
        assert body.count('id="attach-files"') == 1
        assert body.count('for="attach-files"') == 1
        assert body.count('id="attach-files-') == 3
        assert body.count('for="attach-files-') == 3

    def test_every_edit_textarea_has_its_own_real_label(self, client):
        """FEATURE C REVIEW, M1. The disclosure renders once per eligible
        user turn, so a fixed id would point every `<label for=>` at the
        first box in document order and no id at all would leave every
        box unnamed. A REAL `<label>`, not an `aria-label`: `chat/
        _composer.html` reaches for the attribute only because the shared
        fragment had dropped a visible label that used to exist, and
        nothing here forces that compromise.

        PAIRED COUNTS, not a membership test: three ids, three matching
        `for=`, and three textareas -- so a label that lost its target,
        or a textarea that lost its id, reds this rather than passing on
        the other half."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner, texts=("a", "b", "c"))
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count('id="edit-text-') == 3
        assert body.count('for="edit-text-') == 3
        assert body.count("Edit this message") == 3
        for turn in turns:
            assert f'for="edit-text-{turn.pk}"' in body
            assert f'id="edit-text-{turn.pk}"' in body

    def test_the_edit_form_carries_the_pickers_current_selection(self, client):
        """Spec section 5.4 step 3: the branch's first turn is started
        with the picker's current selection, not with the agent's role
        binding. `thread_context` already holds `selected`; without a
        hidden field the POST sends `""` and the branch silently answers
        from a different model than the thread the reader was in.

        A COUNT, NOT A MEMBERSHIP TEST: `chat/_composer.html` already
        renders its own hidden `connection` field on a page with no
        picker to render (no `ModelConnection` rows exist in this
        fixture), so `in body` was green before this feature existed and
        pinned nothing. THREE is the composer's one plus one per
        editable turn."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            url = reverse("chat-conversation", args=[conversation.id])
            body = client.get(f"{url}?connection=7").content.decode()
        assert body.count('name="connection" value="7"') == 3


class TestThePost:
    def test_it_branches_and_redirects_to_the_new_conversation(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        bind_chat_role(CHAT_CONVERSE_ROLE, name="edit-role-1")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "a better question"})
            branch = Conversation.objects.exclude(pk=conversation.pk).get()
        assert response.status_code == 302
        assert str(branch.id) in response["Location"]
        assert "pending=" in response["Location"]

    def test_the_branch_holds_everything_before_the_edit_plus_the_edit(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        bind_chat_role(CHAT_CONVERSE_ROLE, name="edit-role-2")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner, texts=("a", "b", "c"))
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, turns[2].pk]),
                        {"text": "edited"})
            branch = Conversation.objects.exclude(pk=conversation.pk).get()
            texts = list(branch.turns.filter(role=Turn.Role.USER)
                         .order_by("index").values_list("text", flat=True))
        assert texts == ["a", "b", "edited"]

    def test_the_original_is_untouched(self, client, fake_turn_queue):
        owner = make_user()
        bind_chat_role(CHAT_CONVERSE_ROLE, name="edit-role-3")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner, texts=("a", "b", "c"))
            before = list(conversation.turns.order_by("index")
                          .values_list("index", "text"))
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                        {"text": "edited"})
            after = list(conversation.turns.order_by("index")
                         .values_list("index", "text"))
        # A COUNT beside the equality: `after == before` alone would stay
        # green if the parent were truncated AND the snapshot taken
        # afterwards. BRANCH-not-rewind is the owner's ruling, so it gets
        # a number.
        assert after == before
        assert len(after) == 3

    def test_it_is_turn_edit_that_calls_start_turn_not_branch_conversation(
        self, client, fake_turn_queue
    ):
        """The import direction is one-way (`agents/chat` imports
        `agents/visibility`), so `branch_conversation` cannot start a
        turn and cannot redirect. This view does both, after it
        returns."""
        import inspect

        from agents.chat.views import turns as turns_module

        source = inspect.getsource(turns_module.turn_edit)
        assert "branch_conversation" in source
        assert "start_turn" in source

    def test_a_blank_edit_writes_nothing_at_all(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            before = Conversation.objects.count()
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "   "})
        assert response.status_code in (302, 400)
        assert Conversation.objects.count() == before

    def test_an_over_length_edit_writes_nothing_at_all(self, client):
        """VALIDATE BEFORE CREATING: the same `MAX_TURN_CHARS` constant
        `start_turn` uses, checked before `branch_conversation` is
        called at all, so a refused edit cannot orphan a branch."""
        from agents.limits import MAX_TURN_CHARS

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            before = Conversation.objects.count()
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                        {"text": "x" * (MAX_TURN_CHARS + 1)})
        assert Conversation.objects.count() == before

    def test_a_start_turn_refusal_leaves_the_branch_readable_and_never_500s(
        self, client, fake_queue_down
    ):
        """Accepted and stated rather than papered over: the branch
        exists with its copied history and no answer, a state the thread
        page already renders honestly with its existing banner."""
        owner = make_user()
        bind_chat_role(CHAT_CONVERSE_ROLE, name="edit-role-4")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "edited"})
            branch = Conversation.objects.exclude(pk=conversation.pk).first()
            assert branch is not None
            page = client.get(reverse("chat-conversation", args=[branch.id]))
        assert response.status_code in (302, 503)
        assert "Traceback" not in response.content.decode(errors="replace")
        assert page.status_code == 200
        # `start_turn` rolls its own transaction back, so the branch
        # holds only the copied history -- no half-written turn.
        assert branch.turns.count() == 1

    def test_a_forged_post_from_each_refused_principal_is_a_404_writing_nothing(
        self, client, fake_turn_queue
    ):
        """No share recipient can mint a durable owned copy that
        outlives revocation (spec review M2)."""
        from agents.visibility import share_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            before = Conversation.objects.count()
            for index, level in enumerate((Share.Level.VIEW, Share.Level.USE)):
                recipient = make_user(username=f"recipient-{index}")
                share_conversation(user_principal(owner), conversation, user=recipient,
                                   level=level)
                sign_in(client, recipient)
                response = client.post(
                    reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                    {"text": "mine now"})
                assert response.status_code == 404
                client.logout()
        assert Conversation.objects.count() == before

    def test_a_workstream_share_recipient_is_refused_through_the_view(
        self, client, fake_turn_queue
    ):
        """FEATURE C REVIEW, M3. A workstream share is `use`-by
        construction, so its holder may POST into this thread and is
        still refused the COPY. Pinned at the predicate by Task 12
        (`agents/tests/test_branch.py::TestMayEditTurn::
        test_a_workstream_share_recipient_may_not`); driven through the
        VIEW here, so the 404 is a pinned fact rather than an inference
        from the view delegating to that predicate."""
        from agents.tests._helpers import _workstream
        from agents.visibility import share_workstream

        owner = make_user()
        recipient = make_user(username="stream-recipient")
        with posture(POSTURE_ENTERPRISE):
            stream = _workstream(**owner_fields(user_principal(owner)))
            conversation = make_conversation(
                agent=make_agent(slug="stream-edit-thread"), workstream=stream,
                **owner_fields(user_principal(owner)))
            turn = make_turn(conversation=conversation, role=Turn.Role.USER,
                             text="theirs", state=Turn.State.DONE)
            share_workstream(user_principal(owner), stream, user=recipient,
                             level=Share.Level.USE)
            before = Conversation.objects.count()
            sign_in(client, recipient)
            # The stream share really does admit them to the thread --
            # otherwise this would prove only that they cannot read it.
            assert client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).status_code == 200
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turn.pk]),
                {"text": "mine now"})
        assert response.status_code == 404
        assert Conversation.objects.count() == before

    def test_an_admin_with_the_content_toggle_off_is_refused_through_the_view(
        self, client, fake_turn_queue
    ):
        """FEATURE C REVIEW, M3. Refused a gate EARLIER than the
        predicate -- `visible_conversation_or_404` is itself gated on
        `sees_all_content` -- which is real defence in depth and worth
        pinning as the view's own answer rather than leaving to
        inference. The same 404 either way, so no response distinguishes
        "no such row" from "not yours"."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            conversation, turns = _own_thread(owner)
            before = Conversation.objects.count()
            sign_in(client, make_admin())
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "mine now"})
        assert response.status_code == 404
        assert Conversation.objects.count() == before

    def test_an_admin_with_the_content_toggle_on_may(self, client, fake_turn_queue):
        """The other side of the toggle, so the test above pins the
        TOGGLE rather than something incidental about being an admin --
        and the administrator's-copy consequence `branch_conversation`
        records is real: the branch is theirs BY OWNERSHIP and survives
        the setting being switched back off."""
        owner = make_user()
        bind_chat_role(CHAT_CONVERSE_ROLE, name="admin-edit-role")
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            conversation, turns = _own_thread(owner)
            sign_in(client, make_admin())
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "an administrator's edit"})
            branch = Conversation.objects.exclude(pk=conversation.pk).get()
        assert response.status_code == 302
        assert str(branch.id) in response["Location"]

    def test_a_stranger_gets_the_same_404_and_writes_nothing(
        self, client, fake_turn_queue
    ):
        """CLASS O: a refused row-addressed POST is a 404, never a 403 --
        a 403 would confirm the conversation exists."""
        owner, stranger = make_user(), make_user(username="stranger")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            before = Conversation.objects.count()
            sign_in(client, stranger)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "mine now"})
        assert response.status_code == 404
        assert Conversation.objects.count() == before

    def test_an_assistant_turn_id_is_refused(self, client, fake_turn_queue):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                                  text="answer", state=Turn.State.DONE)
            before = Conversation.objects.count()
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, assistant.pk]),
                {"text": "edited"})
        assert response.status_code == 404
        assert Conversation.objects.count() == before

    def test_a_forged_post_while_a_turn_is_in_flight_is_refused(
        self, client, fake_turn_queue
    ):
        """The control is hidden while an answer runs; the POST refuses
        anyway. Hiding a control is not a gate."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.QUEUED)
            before = Conversation.objects.count()
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "edited"})
        assert response.status_code == 404
        assert Conversation.objects.count() == before

    def test_a_turn_id_from_another_conversation_is_a_404_not_a_500(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            _elsewhere, other_turns = _own_thread(owner)
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, other_turns[1].pk]),
                {"text": "edited"})
        assert response.status_code == 404

    def test_an_unknown_turn_id_is_a_404_not_a_500(self, client, fake_turn_queue):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, 999999]),
                {"text": "edited"})
        assert response.status_code == 404

    def test_a_get_is_a_405(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            sign_in(client, owner)
            response = client.get(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]))
        assert response.status_code == 405

    def test_the_branch_is_stamped_with_the_parents_own_index(
        self, client, fake_turn_queue
    ):
        """Task 14's banner reads `branched_at_index`, and it is the
        PARENT's index of the edited turn -- never the branch's own last
        index."""
        owner = make_user()
        bind_chat_role(CHAT_CONVERSE_ROLE, name="edit-role-5")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner, texts=("a", "b", "c"))
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, turns[2].pk]),
                        {"text": "edited"})
            branch = Conversation.objects.exclude(pk=conversation.pk).get()
        assert branch.branched_from_id == conversation.id
        assert branch.branched_at_index == turns[2].index

    def test_a_branch_of_a_branch_works_and_reports_the_right_parent(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        bind_chat_role(CHAT_CONVERSE_ROLE, name="edit-role-6")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner, texts=("a", "b", "c"))
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, turns[2].pk]),
                        {"text": "second thread"})
            first_branch = Conversation.objects.exclude(pk=conversation.pk).get()
            # THE QUEUED PLACEHOLDER THE FIRST EDIT LEFT BEHIND HAS TO
            # SETTLE FIRST, and that is the feature working rather than a
            # fixture nicety: the in-flight clause is conversation-wide,
            # so a branch whose own answer is still running is not
            # branchable either. The worker would write this row; here it
            # is written by hand, the same way every other test on this
            # surface stands in for a finished job.
            first_branch.turns.filter(role=Turn.Role.ASSISTANT).update(
                state=Turn.State.DONE)
            branch_turn = first_branch.turns.filter(
                role=Turn.Role.USER, state=Turn.State.DONE).order_by("index").first()
            client.post(
                reverse("chat-turn-edit", args=[first_branch.id, branch_turn.pk]),
                {"text": "third thread"})
            second_branch = Conversation.objects.exclude(
                pk__in=[conversation.pk, first_branch.pk]).get()
        assert second_branch.branched_from_id == first_branch.id
