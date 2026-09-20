"""The settings assistant panel's context, its two guards, and its
budget (spec §6.2, §6.3, §11).

THE BUDGET IS PINNED BY EQUALITY, NEVER BY `<=` -- "a bound that only
forbids growth is a place for a regression to hide". The invariant the
pins protect is that the number is FLAT: in the number of turns, of
conversations and of agents.

TWO OF THE NUMBERS ARE THE SAME IN BOTH POSTURES AND ARE THE PROMISE
THAT LETS THIS PROCESSOR EXIST AT ALL -- zero queries off the settings
area, and zero on a settings page for a principal the panel will not
render for. Those two are the objection `foundation/settings_area.py:
12-20` raised against a context processor for the sidebar, answered.
"""
from __future__ import annotations

import importlib
import re

import pytest
from django.test import override_settings
from django.urls import clear_url_caches, reverse

from agents.chat.context_processors import ASSISTANT_PANEL_TURNS, settings_assistant
from foundation.settings_area import visible_entries
# `_patch_queue` IS IN THIS LIST because Tasks 10 and 11 call it in ten
# tests: it patches `enqueue`/`get_job` at the `agents.chat.service` seam
# (`agents/chat/tests/_helpers.py:77-99`) so the ask flow's own code path
# runs for real against no queue.
from agents.chat.tests._helpers import (
    _patch_queue, bound_chat_role, make_admin, make_turn, make_user, posture,
    seed_sweep_posture, sign_in, user_principal,
)
from agents.defaults import install_default
from agents.models import Agent, Turn
from agents.visibility import create_conversation
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN

pytestmark = pytest.mark.django_db

SLUG = "settings-helper"
# The settings page these tests drive. `chat-settings` is class S,
# renders for an administrator in both postures, and belongs to this
# column -- so a change here never depends on another column's page.
A_SETTINGS_PAGE = "chat-settings"


@pytest.fixture(autouse=True)
def _a_box(db):
    seed_sweep_posture()


def _install(principal):
    install_default("agent", SLUG, principal)
    return Agent.objects.get(slug=SLUG)


def _thread(principal, turns=0):
    agent = _install(principal)
    conversation = create_conversation(principal, agent)
    for index in range(turns):
        make_turn(conversation=conversation, role=Turn.Role.USER, text=f"q{index}")
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text=f"a{index}",
                  state=Turn.State.DONE)
    return conversation


class TestTheFirstGuardCostsNothingOffTheSettingsArea:
    """THE PANEL'S OWN SHARE IS MEASURED BY CALLING THE PROCESSOR
    DIRECTLY, not by diffing two whole-page counts.

    That is the only non-vacuous way to state a `+N` claim here: a
    whole-page baseline taken with the processor already registered
    ALREADY CONTAINS the panel's cost, so `assert count == baseline + 1`
    would be asserting `n == n + 1` or, worse, passing by accident. The
    absolute whole-request numbers are pinned separately, by equality, in
    the `MEASURED_*` tests below.
    """

    @pytest.mark.parametrize("box", [POSTURE_OPEN, POSTURE_ENTERPRISE])
    def test_a_page_that_is_not_a_settings_page_costs_zero_and_yields_no_key(
            self, rf, django_assert_num_queries, box):
        """+0, IN BOTH POSTURES. On every page in the box that is not a
        settings page the processor costs one attribute read and one set
        membership test -- which is the precise objection
        `foundation/settings_area.py:12-20` raised against a context
        processor for the sidebar, answered."""
        with posture(box):
            request = _request(rf, "chat-index")
            with django_assert_num_queries(0):
                assert settings_assistant(request) == {}

    @pytest.mark.parametrize("box", [POSTURE_OPEN, POSTURE_ENTERPRISE])
    def test_it_costs_nothing_even_with_the_open_parameter_typed_by_hand(
            self, rf, django_assert_num_queries, box):
        with posture(box):
            request = _request(rf, "chat-index", query="?assistant=1")
            with django_assert_num_queries(0):
                assert settings_assistant(request) == {}

    def test_a_request_that_resolved_to_nothing_at_all_is_survived(self, rf):
        """`resolver_match` is `None` on a request the URL resolver never
        matched -- a 404 render, and any test rendering a template
        directly. The first clause reads it with `getattr`, so this is a
        return rather than an `AttributeError` on a page nobody asked the
        panel about."""
        request = rf.get("/no-such-path/")
        assert settings_assistant(request) == {}


class TestTheSecondGuardIsAtTheDataSeam:
    def test_an_anonymous_visitor_to_the_public_settings_page_gets_no_key(self, client):
        """SPEC §6.2, DECISION 21 -- AND IT IS ASSERTED ON THE CONTEXT,
        NOT ON THE HTML, because the defect being guarded is work done
        BEFORE a template discards it.

        `/setup/` is the proving case rather than a convenient one: it is
        a settings-area entry gated EVERYONE with route class P, it
        extends `_settings.html`, and it HAS a card (assertion 1 requires
        one) -- so it passes the first clause. Without the admin clause,
        an anonymous visitor on an accounts-on box would have an agent
        row, a conversation lookup and six turns resolved for them.
        """
        with posture(POSTURE_ENTERPRISE):
            response = client.get(reverse("setup-index"))
            assert response.status_code == 200
            assert "assistant" not in response.context

    def test_the_same_with_the_open_parameter_typed_by_hand(self, client):
        with posture(POSTURE_ENTERPRISE):
            response = client.get(reverse("setup-index") + "?assistant=1")
        assert response.status_code == 200
        assert "assistant" not in response.context

    def test_an_anonymous_visitor_costs_zero_panel_queries(
            self, rf, django_assert_num_queries):
        """+0 IN BOTH POSTURES for a principal the panel will not render
        for -- the second of the two zeroes that are the promise letting
        this processor exist at all.

        MEASURED ON THE PROCESSOR ITSELF, with the settings row PRIMED
        first: the admin clause reads that singleton, and on a fresh test
        database its very first read also CREATES it. Priming separates
        the one-time cost from the steady-state one, which is the number
        this pins.
        """
        with posture(POSTURE_ENTERPRISE):
            request = _request(rf, "setup-index", query="?assistant=1")
            settings_assistant(request)                       # prime the singleton
            request = _request(rf, "setup-index", query="?assistant=1")
            with django_assert_num_queries(0):
                assert settings_assistant(request) == {}

    def test_a_member_on_a_settings_page_gets_no_key_either(self, client):
        """A member cannot reach `chat-settings` (class S), so the page
        this asserts on is the public one again -- signed in this time,
        which is a different principal down a different branch of
        `is_admin`."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("setup-index") + "?assistant=1")
        assert "assistant" not in response.context

    def test_a_member_costs_no_conversation_and_no_turn_read(self, client):
        """THE INVARIANT, STATED AS THE TABLES RATHER THAN AS A COUNT.

        A signed-in member's `is_admin` reads `auth_user` once -- a read
        the gate middleware has ALREADY made and memoised on the same
        settings-row instance -- so the honest claim for this principal
        is not a bare zero but "no panel row is read". That is exactly
        what the leak SHAPE was: an agent row, a conversation lookup and
        six turns resolved for somebody a `{% if %}` was about to throw
        them away from.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            url = reverse("setup-index") + "?assistant=1"
            client.get(url)
            with CaptureQueriesContext(connection) as captured:
                client.get(url)
        sql = " ".join(entry["sql"].lower() for entry in captured.captured_queries)
        for table in ("agents_agent", "agents_conversation", "agents_turn"):
            assert table not in sql, table


class TestTheCollapsedPanel:
    def test_an_administrator_on_a_settings_page_gets_the_key_collapsed(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.get(reverse(A_SETTINGS_PAGE))
        assistant = response.context["assistant"]
        assert assistant["open"] is False
        assert assistant["installed"] is False
        assert assistant["cards"] == []
        assert assistant["links"] == []

    def test_a_collapsed_panel_reads_no_conversation_and_no_turn(self, client):
        """A `<details>` renders its children into the DOM even when
        closed, so NOT RENDERING THEM is the only honest way to not pay
        for them."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=3)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE))
        assert response.context["assistant"]["cards"] == []

    def test_the_open_posture_collapsed_panel_costs_exactly_one_query(
            self, rf, django_assert_num_queries):
        """+1: THE AGENT ROW, AND NOTHING ELSE. The one number spec §11
        states outright rather than leaving to measurement.

        Measured on the processor itself, with the settings row stashed
        the way `IdentityGateMiddleware` stashes it -- so what this counts
        is the panel's own share and not the page's."""
        with posture(POSTURE_OPEN):
            _install(_open_principal())
            request = _request(rf, A_SETTINGS_PAGE)
            with django_assert_num_queries(1):
                assistant = settings_assistant(request)["assistant"]
        assert assistant["open"] is False
        assert assistant["installed"] is True

    def test_the_not_installed_state_costs_the_collapsed_number_not_the_open_one(
            self, rf, django_assert_num_queries):
        """SPEC §11's explicitly separate case: with NO agent row, an
        `?assistant=1` request must still cost the COLLAPSED number --
        there is no conversation to look up, and looking anyway would be
        a query spent proving nothing exists."""
        with posture(POSTURE_OPEN):
            request = _request(rf, A_SETTINGS_PAGE, query="?assistant=1")
            with django_assert_num_queries(1):
                assistant = settings_assistant(request)["assistant"]
        assert assistant["installed"] is False
        assert assistant["cards"] == []

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_accounts_on_collapsed_number_is_flat_and_pinned_by_equality(
            self, client, django_assert_num_queries, turns):
        """MEASURE IT AT IMPLEMENTATION AND WRITE IT IN AS AN EQUALITY.
        Replace `MEASURED_COLLAPSED_ENTERPRISE` below with the number this
        test prints on its first run -- and never with a `<=`.

        WHY IT IS LARGER THAN THE OPEN ONE, AND WHY THAT IS IRREDUCIBLE
        HERE: an administrator on an accounts-on box is not
        `sees_all_content` (`admin_sees_content` defaults to False, and
        `is_admin` is emphatically not `sees_all_content`), so
        `visible_agents` does not take its cheap first branch -- it adds
        the agent-target `shared_keys` plus `label_permitted_q` ->
        `held_entitlement_ids` -> `_grant_ids`. Threading
        `settings_row_for(request)` removes the repeated singleton reads
        and is still required; it cannot remove the share/grant reads, and
        this phase does not widen a cross-column function to chase them.

        THE INVARIANT THE PIN PROTECTS IS FLATNESS: the same number at 0
        turns and at 12.
        """
        # 12 -- Task 8 review fix round, M1: was 15 before `visible_
        # agents` threaded `settings_row` into its OWN two remaining
        # legs (`owned_rows_q` -> `is_admin`, `label_permitted_q` ->
        # `held_entitlement_ids`), each of which re-read the
        # `IdentitySettings` singleton on its own. Still made of the
        # whole `chat-settings` GET (session + auth middleware,
        # `IdentityGateMiddleware`'s own `accounts_on` read, the view's
        # own settings reads, and the panel's `visible_agents` share/
        # grant TABLE reads on its non-cheap branch, which this phase
        # does not chase) -- FLAT at both 0 and 12 turns, because the
        # collapsed panel reads no conversation and no turn.
        MEASURED_COLLAPSED_ENTERPRISE = 12
        assert MEASURED_COLLAPSED_ENTERPRISE is not None, (
            "measure this number, then write it in as an equality")
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=turns)
            sign_in(client, admin)
            url = reverse(A_SETTINGS_PAGE)
            client.get(url)                                   # prime session + singleton
            with django_assert_num_queries(MEASURED_COLLAPSED_ENTERPRISE):
                client.get(url)


class TestTheOpenPanel:
    def test_the_open_parameter_opens_it_and_shows_the_recent_turns(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=2)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assistant = response.context["assistant"]
        assert assistant["open"] is True
        assert assistant["installed"] is True
        assert [card["text"] for card in assistant["cards"]] == ["q0", "a0", "q1", "a1"]

    def test_it_shows_the_last_n_turns_not_the_whole_history(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=20)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        cards = response.context["assistant"]["cards"]
        assert len(cards) == ASSISTANT_PANEL_TURNS
        # NEWEST N, IN INDEX ORDER -- a panel that showed the OLDEST six
        # would be a panel that never shows the answer just given.
        assert cards[-1]["text"] == "a19"

    def test_an_assistant_turn_is_rendered_through_the_one_renderer(self, client):
        """SPEC §6.4: the panel reuses the thing that matters --
        `agents.chat.rendering.render_answer` -- so a model's `**bold**`,
        lists and code spans render identically here and in `/chat/`,
        ESCAPED FIRST. It does NOT reuse `chat/_turn_block.html`."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                      text="**bold** and <script>alert(1)</script>", state=Turn.State.DONE)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        html = str(response.context["assistant"]["cards"][-1]["text_html"])
        assert "<strong>bold</strong>" in html
        assert "<script>" not in html

    def test_a_queued_turn_marks_the_panel_pending(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.QUEUED)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assistant = response.context["assistant"]
        assert assistant["pending"] is True
        assert assistant["cards"][-1]["pending"] is True

    def test_a_failed_turn_carries_its_own_honest_error_line(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.FAILED, error="the queue is down")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        card = response.context["assistant"]["cards"][-1]
        assert card["pending"] is False
        assert card["error"] == "the queue is down"

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_open_panel_is_flat_in_the_number_of_turns(
            self, rf, django_assert_num_queries, turns):
        """+3 IN THE OPEN POSTURE: the agent, the conversation, and its
        last N turns -- ONE query for N turns, INCLUDING the tool turns,
        which is where the Jump-to links live, so the strip is built from
        rows already fetched and never costs a read of its own.

        THE SAME 3 AT 0 TURNS AND AT 12. That flatness is the invariant;
        the number itself is only the shape it takes today."""
        with posture(POSTURE_OPEN):
            _thread(_open_principal(), turns=turns)
            request = _request(rf, A_SETTINGS_PAGE, query="?assistant=1")
            with django_assert_num_queries(3):
                assistant = settings_assistant(request)["assistant"]
        assert assistant["open"] is True
        assert len(assistant["cards"]) == min(2 * turns, ASSISTANT_PANEL_TURNS)

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_accounts_on_open_number_is_flat_and_pinned_by_equality(
            self, client, django_assert_num_queries, turns):
        # 16 -- Task 8 review, R2: was 18 before `visible_conversations`
        # threaded `settings_row` into its OWN `owned_rows_q` call
        # (`agents/visibility.py:107`), on top of the M1 threading
        # `visible_agents` already did. That closed BOTH the second
        # `identity_identitysettings` read AND, because `is_admin` ->
        # `_user_row` memoises on the settings-row INSTANCE
        # (`identity/access.py:80-93`), the `identity_user` re-read a
        # fresh row would otherwise have cost -- two queries, not one,
        # which is why this dropped by 2 rather than 1. Larger than the
        # isolated open-posture `+3`
        # (`test_the_open_panel_is_flat_in_the_number_of_turns`) because
        # this branch also reaches `visible_conversations`'s share/
        # workstream reads, which this fix does not chase. FLAT at both
        # 0 and 12 turns, which is the invariant this pin protects.
        MEASURED_OPEN_ENTERPRISE = 16
        assert MEASURED_OPEN_ENTERPRISE is not None, (
            "measure this number, then write it in as an equality")
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=turns)
            sign_in(client, admin)
            url = reverse(A_SETTINGS_PAGE) + "?assistant=1"
            client.get(url)
            with django_assert_num_queries(MEASURED_OPEN_ENTERPRISE):
                client.get(url)


class TestTheOpenBoxSharesOneThread:
    def test_two_viewers_on_an_open_box_continue_the_same_conversation(self, client):
        """OWNER FLAG 3, ASSERTED RATHER THAN LEFT IN A DOCSTRING.
        `visible_conversations` short-circuits on `sees_all_content` and
        returns `.all()`, and every viewer on an open box is the same
        `OPEN_PRINCIPAL` -- so a household box has ONE assistant thread
        that everyone at the keyboard continues and reads the history of.
        That is correct for a box with no accounts and it matches the
        whole settings area; it is also the half a person would be
        surprised by."""
        from django.test import Client

        with posture(POSTURE_OPEN):
            _thread(_open_principal(), turns=1)
            first = Client().get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
            second = Client().get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert [c["text"] for c in first.context["assistant"]["cards"]] == \
               [c["text"] for c in second.context["assistant"]["cards"]]


class TestTwoAdministratorsDoNotShare:
    def test_each_administrator_sees_only_their_own_thread(self, client):
        from django.test import Client

        with posture(POSTURE_ENTERPRISE):
            one, two = make_admin(), make_admin()
            _thread(user_principal(one), turns=1)
            client_one, client_two = Client(), Client()
            sign_in(client_one, one)
            sign_in(client_two, two)
            url = reverse(A_SETTINGS_PAGE) + "?assistant=1"
            assert client_one.get(url).context["assistant"]["cards"]
            assert client_two.get(url).context["assistant"]["cards"] == []

    def test_each_administrator_sees_only_their_own_thread_even_with_content_access_on(
            self, client):
        """TASK 8 REVIEW, m2 -- THE REAL LEAK. With `admin_sees_content`
        on, `visible_conversations` returns EVERY administrator's
        conversation, ordered newest-first (`Conversation.Meta.
        ordering`), so an unfiltered `.first()` binds a viewer's panel to
        whichever administrator's thread was updated most recently --
        not necessarily their own. `panel_context` filters the
        conversation lookup to the viewing principal's own
        `owner_kind`/`owner_key` (`identity.access.owner_fields`), always
        -- reading is authorised by the flag, but AUTO-SELECTING somebody
        else's thread is not, and a later ask would have written into
        it.

        THE TWO THREADS CARRY DIFFERENT TEXT, deliberately: a test whose
        two seeded threads read identically cannot tell "each viewer sees
        their own" apart from "both viewers were bound to the same one".
        Administrator one's thread is created AFTER (and so is ordered
        ahead of) administrator two's -- the exact ordering an unfiltered
        `.first()` would use to hand BOTH viewers administrator one's
        transcript.
        """
        from django.test import Client

        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            one, two = make_admin(), make_admin()
            agent = _install(user_principal(one))
            two_conversation = create_conversation(user_principal(two), agent)
            make_turn(conversation=two_conversation, role=Turn.Role.USER, text="twos-question")
            make_turn(conversation=two_conversation, role=Turn.Role.ASSISTANT,
                      text="twos-answer", state=Turn.State.DONE)
            one_conversation = create_conversation(user_principal(one), agent)
            make_turn(conversation=one_conversation, role=Turn.Role.USER, text="ones-question")
            make_turn(conversation=one_conversation, role=Turn.Role.ASSISTANT,
                      text="ones-answer", state=Turn.State.DONE)
            client_one, client_two = Client(), Client()
            sign_in(client_one, one)
            sign_in(client_two, two)
            url = reverse(A_SETTINGS_PAGE) + "?assistant=1"
            one_cards = client_one.get(url).context["assistant"]["cards"]
            two_cards = client_two.get(url).context["assistant"]["cards"]
        assert [c["text"] for c in one_cards] == ["ones-question", "ones-answer"]
        assert [c["text"] for c in two_cards] == ["twos-question", "twos-answer"]


def _open_principal():
    from identity.contracts.principals import OPEN_PRINCIPAL

    return OPEN_PRINCIPAL


def _request(rf, name, *, query: str = "", user=None):
    """A request the processor can be called on directly, shaped exactly
    the way a real one reaches it.

    THREE THINGS THE MIDDLEWARE CHAIN NORMALLY PUTS THERE, and every one
    of them changes what is being measured if it is missing:

      * `resolver_match` -- the first guard clause reads its `url_name`.
        A `RequestFactory` request has none until something resolves the
        path, so this resolves it.
      * `identity_settings_row` -- the row `IdentityGateMiddleware`
        stashes. WITHOUT IT `settings_row_for` fetches the singleton
        itself, and the "+1" and "+3" pins above would each be measuring
        one extra read that no real request pays.
      * `user` -- what `principal_for_request` reads on an accounts-on
        box. `AnonymousUser` by default, which is the drill-7 principal.

    DIRECT CALLS RATHER THAN `client.get`, for the pins only: a
    whole-page count already contains the panel's cost, so a `+N` claim
    stated against one would be asserting a number against itself. The
    absolute whole-request numbers ARE pinned -- by equality, in the
    `MEASURED_*` tests, through the real client.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.urls import resolve

    from identity.models import IdentitySettings

    path = reverse(name)
    request = rf.get(path + query)
    request.resolver_match = resolve(path)
    request.identity_settings_row = IdentitySettings.get_solo()
    request.user = user or AnonymousUser()
    return request


class TestTheSharedComposerParameter:
    """`composer_next` (spec §6.5) -- the ONE change this phase makes to
    `chat/_composer.html`, in the shape that fragment already uses for
    `composer_workstream`.

    `"attach_workstream": None` IN BOTH CONTEXT DICTS BELOW, though
    neither test is about attachments: the fragment's own docstring
    rules `attach_workstream` is READ DIRECTLY FROM CONTEXT, never
    `with`-passed, because every REAL caller already builds it
    (`composer_attach_context`/`thread_context`) -- but it is also the
    right-hand argument of `composer_workstream|default:attach_
    workstream` a few lines above where `composer_next` is added, and a
    filter's variable ARGUMENT (unlike the value it filters) is not
    given Django's normal missing-variable leniency: an omitted one
    raises `VariableDoesNotExist` out of `render_to_string`, rather than
    resolving empty, whether or not `composer_workstream` itself is
    set. A direct `render_to_string` call is what every real caller
    never does -- each already supplies it -- so this is the one key
    the fragment needs that a real page context always has and this
    bare dict does not.
    """

    def test_it_emits_a_hidden_next_when_passed(self):
        from django.template.loader import render_to_string

        html = render_to_string("chat/_composer.html", {
            "composer_mode": "turn", "composer_action": "/settings/assistant/ask/",
            "composer_next": "/chat/settings/?assistant=1",
            "composer_placeholder": "Ask about these settings", "composer_button": "Ask",
            "attach_workstream": None,
        })
        assert '<input type="hidden" name="next" value="/chat/settings/?assistant=1">' in html

    def test_it_emits_nothing_when_omitted(self):
        """EVERY EXISTING INCLUDE SITE OMITS IT AND MUST BE
        BYTE-IDENTICAL TO TODAY."""
        from django.template.loader import render_to_string

        html = render_to_string("chat/_composer.html", {
            "composer_mode": "start", "composer_action": "/chat/start/",
            "composer_placeholder": "Message", "composer_button": "Send",
            "attach_workstream": None,
        })
        assert 'name="next"' not in html

    def test_the_three_existing_chat_surfaces_still_emit_no_next(self, client):
        """ALL THREE SURFACES THAT INCLUDE `chat/_composer.html`, through
        the real pages rather than the fragment, so a regression in an
        include site is caught as well as one in the fragment.

        THE CONVERSATION PAGE IS THE ONE THAT MATTERS MOST and is the one
        an earlier draft of this test left out: it posts to
        `turn_create`, which reads `next` through the same
        `validated_next_url` this phase is extending, so a stray hidden
        field there would not be inert -- it would redirect the operator
        somewhere `turn_create` never intended.

        `chat-workstream` (THE SINGULAR, PER-STREAM WORKING PAGE), NOT
        `chat-workstreams` (the plural list): `chat/_composer.html`'s
        own docstring names the three surfaces that include it as
        `chat/index.html`, `chat/workstream.html`'s New-chat card, and
        `chat/conversation.html` -- and `chat/workstream.html` is what
        `chat-workstream` (`/w/<pk>/`) renders. `chat-workstreams`
        (`/w/`, the list) is `chat/workstreams.html`, which includes no
        composer at all -- a name collision in the route list, not a
        fourth surface, so this test creates a real stream and reverses
        the row-addressed route instead."""
        from agents.chat.tests._helpers import make_thread
        from agents.tests._helpers import _workstream

        with posture(POSTURE_OPEN):
            conversation = make_thread()
            stream = _workstream()
            pages = (
                reverse("chat-index"),
                reverse("chat-workstream", args=[stream.pk]),
                reverse("chat-conversation", args=[conversation.pk]),
            )
            for url in pages:
                body = client.get(url).content.decode()
                # SCOPED TO THE COMPOSER, NOT THE WHOLE PAGE. The sidebar's
                # row menus have emitted their own `name="next"` since
                # rename/pin/archive landed (`chat/_sidebar_row.html:63,
                # 81,88,97,104`), and `chat/base.html` draws that rail on
                # all three of these pages -- so a whole-body assertion is
                # false against `main` itself, the moment there is one
                # conversation for the rail to list. What this phase must
                # not change is the COMPOSER's fields, and that is what
                # this reads: `_composer.html:66` opens `<div class=
                # "composer-card">` and `:126` closes its `</form>`.
                start = body.index('<div class="composer-card">')
                composer = body[start:body.index("</form>", start)]
                assert 'name="next"' not in composer, url


class TestTheInstallOfferComesBack:
    def test_installing_from_a_settings_page_returns_to_that_page(self, client):
        """SPEC §6.5, DECISION 12: three lines on an existing route
        against a fourth new route. `validated_next_url` already exists
        and is already used by two other views."""
        with posture(POSTURE_OPEN):
            response = client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "settings-helper",
                "next": reverse(A_SETTINGS_PAGE) + "?assistant=1",
            })
        assert response.status_code == 302
        assert response.headers["Location"] == reverse(A_SETTINGS_PAGE) + "?assistant=1"

    def test_a_next_off_this_host_is_refused_and_falls_back(self, client):
        """`validated_next_url` is the guard, unchanged: a `next` value is
        caller-supplied, and an unchecked one is an open redirect off this
        box."""
        with posture(POSTURE_OPEN):
            response = client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "settings-helper",
                "next": "https://example.invalid/steal",
            })
        assert response.headers["Location"] == reverse("chat-index")

    def test_with_no_next_it_behaves_exactly_as_before(self, client):
        with posture(POSTURE_OPEN):
            response = client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "general"})
        assert response.headers["Location"] == reverse("chat-index")

    def test_the_race_path_honours_next_too(self, client):
        """`default_install` has TWO return paths -- the ordinary one and
        the `IntegrityError` one that catches two concurrent
        double-clicks. Both get the same treatment, because an operator
        who lost the race got the state they asked for and should land
        where the winner landed."""
        with posture(POSTURE_OPEN):
            target = reverse(A_SETTINGS_PAGE) + "?assistant=1"
            client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "settings-helper", "next": target})
            again = client.post(reverse("chat-default-install"), {
                "kind": "agent", "slug": "settings-helper", "next": target})
        assert again.headers["Location"] == target


class TestTheInstallDoorRefusesTheAssistantSlugForNonAdmins:
    """BINDING TASK-10 AMENDMENT 5 (orchestrator ruling, Task 6
    advisory-5): `chat-default-install` is class A (`identity/routes.py`)
    -- any signed-in caller, no row rule -- and the Task 6 exclusion only
    kept `settings-helper` off the `/chat/` OFFERS list; it never stopped
    a crafted POST straight at this door. The assistant's OWN offer form
    (`chat/_assistant_panel.html`, Task 10 Step 7) posts to this SAME
    route (spec §6.5 decision 12: three lines on an existing route beat a
    fourth one) and is only ever rendered for an administrator, so the
    door refuses a SETTINGS-SURFACE slug from anybody else rather than
    refusing the route outright -- which is what keeps the panel's own
    button working while closing the gap the ruling names."""

    def test_a_member_may_not_install_the_settings_assistant_through_the_chat_door(
            self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("chat-default-install"),
                                   {"kind": "agent", "slug": SLUG})
        assert response.status_code == 403
        assert Agent.objects.filter(slug=SLUG).count() == 0

    def test_an_anonymous_caller_may_not_either(self, client):
        with posture(POSTURE_ENTERPRISE):
            response = client.post(reverse("chat-default-install"),
                                   {"kind": "agent", "slug": SLUG})
        # Class A: an anonymous caller is refused BEFORE this view runs
        # on an accounts-on box, so this never reaches the view's own
        # check -- asserted here so the two refusals are not confused.
        assert response.status_code != 200
        assert Agent.objects.filter(slug=SLUG).count() == 0

    def test_an_administrator_still_installs_it_through_the_same_door(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("chat-default-install"),
                                   {"kind": "agent", "slug": SLUG,
                                    "next": reverse(A_SETTINGS_PAGE) + "?assistant=1"})
        assert response.status_code == 302
        assert Agent.objects.filter(slug=SLUG).count() == 1

    def test_an_open_box_installs_it_too(self, client):
        """`is_admin` is True for everybody on an open box -- the same
        "household box" reasoning the panel's own routes lean on."""
        with posture(POSTURE_OPEN):
            response = client.post(reverse("chat-default-install"),
                                   {"kind": "agent", "slug": SLUG})
        assert response.status_code == 302
        assert Agent.objects.filter(slug=SLUG).count() == 1

    def test_an_ordinary_agent_is_unaffected(self, client):
        """The check is scoped to `SETTINGS_SURFACE_SLUGS`, not to the
        route as a whole: a non-admin member still installs an ordinary
        shipped default exactly as before."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("chat-default-install"),
                                   {"kind": "agent", "slug": "general"})
        assert response.status_code == 302
        assert Agent.objects.filter(slug="general").count() == 1

    def test_the_refusal_is_scoped_by_slug_not_by_kind(self, client):
        """Task 10 review, a3 (hardening): the guard used to read
        `kind == "agent" and slug in _SETTINGS_SURFACE_SLUGS_CI`, so a
        future settings-surface FLOW slug in that same set would slip
        the `kind` term and reach `install_default` unrefused for a
        non-admin. The guard now reads the slug alone, so a `kind=
        "flow"` smuggle of the settings-surface slug is refused BEFORE
        `install_default` ever runs -- a 403, not whatever a nonexistent-
        flow `ValueError` would have produced."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("chat-default-install"),
                                   {"kind": "flow", "slug": SLUG})
        assert response.status_code == 403
        assert Agent.objects.filter(slug=SLUG).count() == 0


class TestTheRoutesAreGated:
    ROUTES = (
        ("settings-assistant-ask", "post", {"text": "hello"}),
        ("settings-assistant-reset", "post", {}),
        ("settings-assistant-panel", "get", {}),
    )

    @pytest.mark.parametrize("name,method,body", ROUTES)
    def test_a_member_gets_403_from_all_three(self, client, name, method, body):
        """CLASS S: the middleware refuses before the view runs."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = getattr(client, method)(reverse(name), body)
        assert response.status_code == 403, name

    @pytest.mark.parametrize("name,method,body", ROUTES)
    def test_an_anonymous_caller_never_gets_a_200(self, client, name, method, body):
        with posture(POSTURE_ENTERPRISE):
            response = getattr(client, method)(reverse(name), body)
        assert response.status_code in (302, 401, 403), name

    @pytest.mark.parametrize("name,method,body", ROUTES)
    def test_every_viewer_on_an_open_box_reaches_them(self, client, name, method, body):
        """OWNER FLAG 3 / GLOBAL CONSTRAINT 3: `is_admin` is True for
        everybody on an open box, so a household box's routes answer the
        person at the keyboard. That is consistent with the whole
        settings area."""
        with posture(POSTURE_OPEN):
            _install(_open_principal())
            response = getattr(client, method)(reverse(name), body)
        assert response.status_code != 403, name

    def test_all_three_are_classified_s_and_driven(self):
        """`identity/routes.py:43` and
        `identity/tests/test_route_matrix.py:92`. A name absent from
        ROUTE_RULES is treated as admin AND LOGGED, so this is not
        cosmetic."""
        from identity.routes import ROUTE_RULES
        from identity.tests.test_route_matrix import _DRIVERS

        for name, _method, _body in self.ROUTES:
            assert ROUTE_RULES[name] == "S", name
            assert name in _DRIVERS, name


class TestTheAskFlow:
    def test_a_non_xhr_ask_redirects_back_carrying_the_open_parameter(
            self, client, monkeypatch, bound_chat_role):
        """SPEC §6.6 STEP 5, AND DRILL 5. With no script at all: a plain
        POST, a redirect back to the page you asked from with
        `?assistant=1`, and the queued turn visible on that page.

        `bound_chat_role` -- a real `ModelConnection` bound to `chat.
        converse`, the SAME fixture `agents/chat/tests/test_turn_create.py`
        requests -- is what lets `start_turn`'s own `preflight_turn` check
        pass so this is a genuine success rather than a redirect shape
        that would look identical on a refusal too."""
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "how do I lock the library?", "next": here})
        assert response.status_code == 302
        assert response.headers["Location"] == f"{here}?assistant=1"

    def test_that_page_then_renders_the_panel_open_with_the_queued_turn(
            self, client, monkeypatch, bound_chat_role):
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            client.post(reverse("settings-assistant-ask"), {"text": "hello", "next": here})
            response = client.get(f"{here}?assistant=1")
        assistant = response.context["assistant"]
        assert assistant["open"] is True
        assert assistant["pending"] is True
        assert [card["text"] for card in assistant["cards"]][0] == "hello"
        body = response.content.decode()
        assert "Thinking" in body

    def test_a_next_that_already_carries_the_open_parameter_is_not_doubled(
            self, client, monkeypatch, bound_chat_role):
        """Task 10 review, n1. The inline panel's own `next` is
        `request.get_full_path()`, so a second script-free ask, made
        from the page the FIRST redirect already landed on, re-poses a
        `next` that already carries `?assistant=1`. Three such asks in a
        row used to measure `[1, 2, 3]` copies of the parameter in the
        address bar; the redirect target must carry the parameter
        EXACTLY ONCE regardless of how many round trips came before it."""
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            already_open = f"{here}?assistant=1"
            responses = []
            for _ in range(3):
                response = client.post(
                    reverse("settings-assistant-ask"),
                    {"text": "how do I lock the library?", "next": already_open})
                responses.append(response.headers["Location"])
        for location in responses:
            assert location == already_open
            assert location.count("assistant=1") == 1

    def test_the_redirect_carries_no_pending_turn_id(
            self, client, monkeypatch, bound_chat_role):
        """DECISION 9 / §18 ITEM 7: the panel re-renders its whole recent
        transcript on every render, so the queued turn is a row it already
        read and the pending marker is derived server-side. Carrying a
        turn id nothing reads would be cargo."""
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "hello", "next": reverse(A_SETTINGS_PAGE)})
        assert "pending=" not in response.headers["Location"]

    def test_a_next_off_this_host_falls_back_to_the_settings_index(
            self, client, monkeypatch, bound_chat_role):
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "hello", "next": "https://example.invalid/x"})
        assert response.headers["Location"] == f"{reverse('settings-index')}?assistant=1"

    def test_an_xhr_ask_gets_the_panel_fragment_not_json(
            self, client, monkeypatch, bound_chat_role):
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            response = client.post(
                reverse("settings-assistant-ask"),
                {"text": "hello", "next": reverse(A_SETTINGS_PAGE)},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("text/html")
        assert "hello" in response.content.decode()

    def test_asking_with_no_agent_row_redirects_for_a_plain_post(self, client):
        """UPDATED, ITEM 6 (Coherence Wave D): this refusal used to be a
        raw 400 regardless of caller, the one branch on this view that
        never checked `_is_xhr` even though this module's own docstring
        promises "EVERYTHING WORKS WITH NO SCRIPT AT ALL: the form is a
        plain POST, the answer is a redirect". It now mirrors the
        `start.ok` refusal just below it on the SAME view: a redirect
        for a plain POST (this test), the panel fragment naming the
        offer for an XHR caller (the test just below)."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "hello", "next": reverse(A_SETTINGS_PAGE)})
        assert response.status_code == 302

    def test_asking_with_no_agent_row_over_xhr_gets_the_panel_naming_the_offer(self, client):
        """The XHR half of the pin just above. N3 (review fix round):
        assert `id="assistant-error"` (`chat/_assistant_panel.html:
        135-137`), not only "install" in the body -- the old plain-text
        400 also contained the word "install", so that assertion alone
        would not have distinguished the dual-mode fragment this wave
        introduced from the bare sentence it replaced."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(
                reverse("settings-assistant-ask"),
                {"text": "hello", "next": reverse(A_SETTINGS_PAGE)},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert response.status_code == 200
        body = response.content.decode()
        assert 'id="assistant-error"' in body
        assert "install" in body.lower()

    def test_a_refusal_lands_in_the_panels_own_error_slot_never_a_bare_fragment(
            self, client, monkeypatch, bound_chat_role):
        """The rule `agents/chat/views/turns.py:64-67` states: a non-XHR
        refusal RE-RENDERS, it does not answer a bare fragment.

        `raises=` takes an EXCEPTION INSTANCE, not a flag: the helper
        does `raise raises` (`agents/chat/tests/_helpers.py:88-90`), so a
        bare `True` is `TypeError: exceptions must derive from
        BaseException`. `QueueUnavailable("down")` is the house value
        (`agents/chat/tests/_helpers.py:117-121`).

        `bound_chat_role` MAKES THIS REFUSAL GENUINE: without it,
        `start_turn`'s own `preflight_turn` check refuses FIRST, for "no
        model is assigned to the chat.converse role", and `enqueue` --
        the thing this test's `raises=` is patching -- would never even
        be called, so the test would pass for the wrong reason."""
        from models.contracts.queue import QueueUnavailable

        _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            response = client.post(reverse("settings-assistant-ask"),
                                   {"text": "hello", "next": here}, follow=True)
        assert response.status_code == 200
        assert "settings-nav" in response.content.decode()   # the whole page, not a fragment

    def test_blank_text_is_refused_without_writing_a_turn(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            before = Turn.objects.count()
            client.post(reverse("settings-assistant-ask"),
                        {"text": "   ", "next": reverse(A_SETTINGS_PAGE)})
        assert Turn.objects.count() == before


class TestOneConversationPerAdministrator:
    def test_two_asks_produce_one_conversation_and_two_user_turns(
            self, client, monkeypatch, bound_chat_role):
        _patch_queue(monkeypatch)
        from agents.models import Conversation

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            client.post(reverse("settings-assistant-ask"), {"text": "one", "next": here})
            _finish_pending()
            client.post(reverse("settings-assistant-ask"), {"text": "two", "next": here})
        assert Conversation.objects.filter(agent__slug=SLUG).count() == 1
        assert Turn.objects.filter(role=Turn.Role.USER).count() == 2

    def test_two_administrators_get_two_conversations(self, client, monkeypatch):
        from django.test import Client
        from agents.models import Conversation

        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            one, two = make_admin(), make_admin()
            _install(user_principal(one))
            here = reverse(A_SETTINGS_PAGE)
            for account in (one, two):
                per = Client()
                sign_in(per, account)
                per.post(reverse("settings-assistant-ask"), {"text": "hi", "next": here})
        assert Conversation.objects.filter(agent__slug=SLUG).count() == 2

    def test_the_created_conversation_carries_no_workstream(self, client, monkeypatch):
        """SPEC §7.1.2 depends on this NULL, and Task 6 pins it from the
        other side. `create_conversation(principal, agent)` -- no
        `workstream` -- and that column is stamped once and never written
        again."""
        from agents.models import Conversation

        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            client.post(reverse("settings-assistant-ask"),
                        {"text": "hi", "next": reverse(A_SETTINGS_PAGE)})
        assert Conversation.objects.get(agent__slug=SLUG).workstream_id is None

    def test_start_over_archives_and_the_next_ask_creates_a_fresh_one(
            self, client, monkeypatch):
        """NOTHING IS DELETED: the old thread is archived, and the
        chat-surface exclusion keeps it out of the archive listing too."""
        from agents.models import Conversation

        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            client.post(reverse("settings-assistant-ask"), {"text": "one", "next": here})
            reset = client.post(reverse("settings-assistant-reset"), {"next": here})
            assert reset.headers["Location"] == f"{here}?assistant=1"
            assert Conversation.objects.filter(
                agent__slug=SLUG, archived_at__isnull=False).count() == 1
            client.post(reverse("settings-assistant-ask"), {"text": "two", "next": here})
        assert Conversation.objects.filter(agent__slug=SLUG).count() == 2
        assert Conversation.objects.filter(
            agent__slug=SLUG, archived_at__isnull=True).count() == 1

    def test_on_an_open_box_two_viewers_share_one_conversation(self, client, monkeypatch):
        """DELIBERATELY (spec §6.6 step 3, owner flag 3)."""
        from django.test import Client
        from agents.models import Conversation

        _patch_queue(monkeypatch)
        with posture(POSTURE_OPEN):
            _install(_open_principal())
            here = reverse(A_SETTINGS_PAGE)
            Client().post(reverse("settings-assistant-ask"), {"text": "one", "next": here})
            _finish_pending()
            Client().post(reverse("settings-assistant-ask"), {"text": "two", "next": here})
        assert Conversation.objects.filter(agent__slug=SLUG).count() == 1


class TestOwnerFilteredWritePaths:
    """BINDING TASK-10 AMENDMENT 1 (Task 8 review, R1): `assistant_ask`
    and `assistant_reset` filter their conversation lookup by
    `**owner_fields(principal)`, unconditionally -- the identical
    mechanism `panel_context`'s own read-side lookup uses (Task 8
    review, m2) -- so `admin_sees_content=True` never lets one
    administrator's ASK append a turn to, or RESET archive, another
    administrator's thread. `sees_all_content` authorises READING;
    `may_post_to`/`may_manage_conversation` are the separate WRITE
    predicates this codebase already keeps apart from it, and this is
    the exact auto-select the panel's own read side was fixed against,
    now proven on the write side too.

    THE TWO SEEDED THREADS CARRY DIFFERENT TEXT, deliberately, the same
    reason `TestTwoAdministratorsDoNotShare`'s own content-access test
    does: a test whose threads read identically cannot tell "each
    administrator reaches only their own row" apart from "both were
    bound to the same one"."""

    def test_ask_never_writes_into_another_administrators_conversation(
            self, client, monkeypatch, bound_chat_role):
        from django.test import Client
        from agents.models import Conversation

        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            one, two = make_admin(), make_admin()
            agent = _install(user_principal(one))
            one_conversation = create_conversation(user_principal(one), agent)
            make_turn(conversation=one_conversation, role=Turn.Role.USER, text="ones-question")
            make_turn(conversation=one_conversation, role=Turn.Role.ASSISTANT,
                      text="ones-answer", state=Turn.State.DONE)
            client_two = Client()
            sign_in(client_two, two)
            client_two.post(reverse("settings-assistant-ask"),
                            {"text": "twos-question", "next": reverse(A_SETTINGS_PAGE)})
        assert Conversation.objects.filter(agent__slug=SLUG).count() == 2
        assert Turn.objects.filter(
            conversation=one_conversation, text="twos-question").count() == 0
        twos_conversation = Conversation.objects.filter(
            agent__slug=SLUG).exclude(pk=one_conversation.pk).get()
        assert Turn.objects.filter(
            conversation=twos_conversation, text="twos-question").count() == 1

    def test_reset_never_archives_another_administrators_conversation(
            self, client, monkeypatch):
        from django.test import Client

        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            one, two = make_admin(), make_admin()
            agent = _install(user_principal(one))
            one_conversation = create_conversation(user_principal(one), agent)
            make_turn(conversation=one_conversation, role=Turn.Role.USER, text="ones-question")
            make_turn(conversation=one_conversation, role=Turn.Role.ASSISTANT,
                      text="ones-answer", state=Turn.State.DONE)
            client_two = Client()
            sign_in(client_two, two)
            # ADMINISTRATOR TWO OWNS NO ASSISTANT THREAD OF THEIR OWN --
            # the owner-filtered lookup finds nothing for them, so this
            # is a no-op rather than falling through to the one
            # conversation `sees_all_content` lets them READ.
            client_two.post(reverse("settings-assistant-reset"),
                            {"next": reverse(A_SETTINGS_PAGE)})
        one_conversation.refresh_from_db()
        assert one_conversation.archived_at is None


class TestTheJumpToStrip:
    def _tool_turn(self, conversation, data):
        return make_turn(conversation=conversation, role=Turn.Role.TOOL, text="",
                         state=Turn.State.DONE, data=data,
                         tool_call={"tool": "settings.card", "args": {}, "agent": SLUG,
                                    "id": "", "discarded": []})

    def test_a_real_link_is_built_from_the_platforms_own_table(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"page": "identity-settings", "links": [
                {"route": "identity-settings", "anchor": "library-posture",
                 "label": "Library posture"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        links = response.context["assistant"]["links"]
        assert links == [{
            "url": f"{reverse('identity-settings')}?assistant=1#library-posture",
            "label": "Library posture",
        }]
        assert links[0]["url"] in response.content.decode()

    def test_an_unknown_route_renders_no_anchor(self, client):
        """SPEC §10.3 "links are whitelisted". A whitelist BY
        CONSTRUCTION: nothing that is not already in the code-side table
        can become a link on this page."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "admin:index", "anchor": "x", "label": "Django admin"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []
        assert "Django admin" not in response.content.decode()

    def test_a_foreign_anchor_on_a_real_route_renders_no_anchor(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "identity-settings", "anchor": "retention",
                 "label": "Retention"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []

    def test_the_label_comes_from_the_card_not_from_the_tool_result(self, client):
        """A label is DISPLAY TEXT. Taking it from the row would let a
        hostile tool result put arbitrary words under a real link."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "identity-settings", "anchor": "library-posture",
                 "label": "CLICK HERE TO WIN"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"][0]["label"] == "Library posture"
        assert "CLICK HERE TO WIN" not in response.content.decode()

    def test_a_tool_turn_with_malformed_data_renders_no_anchor_and_no_error(self, client):
        """Task 10 review, m1: an UNHASHABLE `route`/`anchor` (a `list`
        or a `dict`) must render no link and no 500 -- `_links` tests
        both against plain sets, and `x not in a_set` raises `TypeError`
        for an unhashable `x` with no guard. Pinned alongside the
        already-covered non-dict-entry and `None`-route cases so this
        property is one test, not an assumption."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                "not-a-dict",
                {"route": None},
                {"route": ["identity-settings"]},
                {"route": {"identity-settings": True}},
                {"route": "identity-settings", "anchor": ["library-posture"]},
                {"route": "identity-settings", "anchor": {"library-posture": True}},
            ]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.status_code == 200
        assert response.context["assistant"]["links"] == []

    def test_the_most_recent_tool_turn_wins(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "rag-settings", "anchor": "retention", "label": "Retention"}]})
            self._tool_turn(conversation, {"links": [
                {"route": "chat-settings", "anchor": "time-aware", "label": "x"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"][0]["url"].startswith(
            reverse("chat-settings"))

    def test_a_newer_all_invalid_turn_does_not_fall_back_to_an_older_valid_one(self, client):
        """Task 10 review, n3: `_links` used to keep walking BACKWARD
        past a tool turn that built no links, and would return an
        OLDER exchange's jump strip -- a stale strip under a newer
        answer. The most recent TOOL turn's own result is now final,
        empty or not."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "rag-settings", "anchor": "retention", "label": "Retention"}]})
            self._tool_turn(conversation, {"links": [
                {"route": "admin:index", "anchor": "x", "label": "Django admin"}]})
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []
        assert "Retention" not in response.content.decode()

    def test_a_card_whose_route_this_box_does_not_mount_renders_no_link_and_no_500(
            self, client):
        """C1 (final review): `settings_help.py`'s `CARDS` is a PURE LEAF
        (`foundation/settings_area.py:69-77`) -- it ships `vision-engine-
        files` unconditionally, on every box, and CANNOT consult
        `settings.FARABUNKER_FEATURES` to know that `config/urls.py:
        45-46` mounts `/vision/` only while the flag is on. Every one of
        the five green suite permutations carries `"vision"`
        (`docs/DEV.md:251-257`), so an unguarded `reverse("vision-
        engine-files")` in `_links` is the one class of defect those
        suites were constitutionally unable to see -- on a configuration
        `docs/DEV.md:1245-1247` documents as a supported PRODUCT state.
        Before the fix this raised `NoReverseMatch` out of a CONTEXT
        PROCESSOR: a 500 on whichever settings page the tool turn's
        conversation was open on.

        THE FLAG-OFF CASE RELOADS `config.urls` ITSELF, the same escape
        hatch `foundation/tests/test_shell.py::TestTheEngineFilesEntry
        IsFlagGuarded` uses (`tools/rag/tests/test_flag_hygiene.py`'s
        repo-wide sweep REQUIRES it): a bare `FARABUNKER_FEATURES =
        frozenset()` in a file that also touches the test `Client` risks
        permanently poisoning the process-wide URLconf's `vision/` mount
        if this happens to be the first URL resolution in the run.
        """
        from config import urls as config_urls

        try:
            with override_settings(FARABUNKER_FEATURES=frozenset()):
                importlib.reload(config_urls)
                clear_url_caches()
                with posture(POSTURE_ENTERPRISE):
                    admin = make_admin()
                    conversation = _thread(user_principal(admin))
                    self._tool_turn(conversation, {"links": [
                        {"route": "vision-engine-files", "anchor": "output-folder",
                         "label": "Output folder"}]})
                    sign_in(client, admin)
                    response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        finally:
            # Restore AFTER `override_settings` has already put the real
            # feature set back -- reloading while still inside the `with`
            # would rebuild the URLconf from the OVERRIDDEN (empty)
            # settings and leave every later test without the vision/
            # mount.
            importlib.reload(config_urls)
            clear_url_caches()

        assert response.status_code == 200
        assert response.context["assistant"]["links"] == []
        assert "Output folder" not in response.content.decode()


class TestTheNamedPageLinks:
    """PLACEMENT-ROUND FOLLOW-UP (owner-found defect, screenshot-
    confirmed, 2026-09-12): a turn whose ANSWER named "Identity &
    security" showed a jump strip of `rag-settings` anchors only -- the
    model made just its FIRST `settings.card` call (the documented
    variance `placement-round-report.md`'s own flagship re-drill already
    recorded), and `_links` only ever links a page the model actually
    fetched THIS turn, so the strip pointed at the wrong page entirely
    while the answer text pointed at the right one.

    THE FIX IS PLATFORM-SIDE AND MODEL-INDEPENDENT: `_links` now also
    matches the final answer's own text against `foundation.settings_
    help.CARDS`' canonical `title`/`route_name` spellings and adds a
    PAGE-LEVEL link (no anchor -- the model never fetched that card, so
    there is no field to point at) for anything it names that the fetched
    leg did not already cover. The whitelist is unchanged in kind: still
    matched only against the platform's own table, never against
    anything the model wrote that is NOT in that table.
    """

    def _tool_turn(self, conversation, data):
        return make_turn(conversation=conversation, role=Turn.Role.TOOL, text="",
                         state=Turn.State.DONE, data=data,
                         tool_call={"tool": "settings.card", "args": {}, "agent": SLUG,
                                    "id": "", "discarded": []})

    def _answer(self, conversation, text):
        return make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         text=text, state=Turn.State.DONE)

    def test_the_owners_own_scenario_a_named_but_uncarded_page_gets_a_link(self, client):
        """THE EXACT DEFECT. One `settings.card` call (`rag-settings`);
        the answer's own text names a SECOND page, never fetched this
        turn, by its registry title. The strip must carry BOTH: the
        fetched anchor first, then the named page -- page-level, since
        no card for it was ever read."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "rag-settings", "anchor": "retention", "label": "Retention"}]})
            self._answer(
                conversation,
                "Library access is not set here. It is controlled on "
                "Identity & security instead.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        links = response.context["assistant"]["links"]
        assert links == [
            {"url": f"{reverse('rag-settings')}?assistant=1#retention",
             "label": "Retention"},
            {"url": f"{reverse('identity-settings')}?assistant=1",
             "label": "Identity & security"},
        ]
        body = response.content.decode()
        assert links[1]["url"] in body
        assert "Identity &amp; security</a>" in body

    def test_the_route_name_is_an_accepted_alias_for_the_same_page(self, client):
        """"reasonable aliases like the route name" -- `identity-settings`
        names the same card `Identity & security` does, with no anchor
        fetched at all this turn."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(conversation, "See the identity-settings page for this.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == [
            {"url": f"{reverse('identity-settings')}?assistant=1",
             "label": "Identity & security"},
        ]

    def test_a_page_named_that_is_not_in_the_registry_gets_no_link(self, client):
        """Nothing that is not already in `CARDS` can become a link --
        restated for the new leg exactly as `TestTheJumpToStrip::test_an_
        unknown_route_renders_no_anchor` already states it for the old
        one. A real-sounding but uncarded name produces nothing."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(conversation, "Try the Admin Backdoor page for that.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []
        # THE PROSE ITSELF STILL RENDERS (it is the model's own honest
        # answer text, rendered exactly as every other answer is) --
        # this fix adds no link, it never removes the model's own words.
        assert "Admin Backdoor" in response.content.decode()

    def test_hostile_answer_text_naming_fake_pages_produces_nothing(self, client):
        """THE WHITELIST HOLDS UNDER ADVERSARIAL TEXT. Nothing becomes a
        link unless it is the registry's own exact spelling, no matter
        how plausible, official-sounding or HTML-shaped the surrounding
        text is -- matched only against `foundation.settings_help.CARDS`,
        never against the model's own invented vocabulary or markup."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(
                conversation,
                "Go to the Super Secret Admin page, or django-admin, or "
                "<a href=\"https://evil.example/\">click here</a> for System "
                "Configuration.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []

    def test_exact_not_fuzzy_a_near_miss_spelling_does_not_match(self, client):
        """Conservative, per the orchestrator's own instruction: "Identity
        and security" (no `&`) and "Identitysecurity" (no space) are
        near-misses, not the registry's own exact spelling, and must not
        resolve to `identity-settings` -- a fuzzy match here is exactly
        the "could link the wrong page" risk this round was told to
        avoid."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(
                conversation,
                "This is about Identity and security, also known as Identitysecurity.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []

    def test_a_page_already_covered_by_a_fetched_anchor_is_not_duplicated(self, client):
        """"then named-page links not already covered": the model DID
        fetch `identity-settings` this turn and the answer also names it
        by title -- the strip carries the one sharper anchor link, never
        a second, anchor-less entry for the same page."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._tool_turn(conversation, {"links": [
                {"route": "identity-settings", "anchor": "library-posture",
                 "label": "Library posture"}]})
            self._answer(conversation, "Set this on Identity & security.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == [
            {"url": f"{reverse('identity-settings')}?assistant=1#library-posture",
             "label": "Library posture"},
        ]

    def test_q7_a_lowercase_generic_one_word_title_in_unrelated_prose_gets_no_link(
            self, client):
        """Q7 TIGHTENING (owner ruling, final-code resilience battery,
        2026-09-13). The drilled scenario: "libary admin onyl??" produced
        an answer entirely about library ACCESS POSTURE, grounded on
        `settings.overview` alone -- and the auto-linker wrapped the
        stray word "library" in a link to `rag-settings`, whose own
        `HelpCard.title` happens to be the single word "Library",
        because a lowercase, in-passing mention of a short generic title
        is not the same thing as the answer naming that page. No
        `settings.card` call this turn at all, so there is nothing to
        override the exact-case requirement with."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(
                conversation,
                "The library posture is open, so it is not restricted to "
                "administrators only.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []

    def test_q7_exact_case_one_word_title_still_links(self, client):
        """The other half of the same ruling: the registry's OWN
        capitalisation still links on its own, with no card fetched at
        all this turn -- the tightening narrows CASE, never whether a
        one-word title can ever match."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(conversation, "Check the Library page for this.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == [
            {"url": f"{reverse('rag-settings')}?assistant=1", "label": "Library"},
        ]

    def test_q7_multi_word_titles_still_match_case_insensitively(self, client):
        """The tightening is scoped to short, generic ONE-WORD titles
        only -- a multi-word title like "Identity & security" keeps
        matching regardless of case, exactly as before this ruling."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(conversation, "This lives under identity & security.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == [
            {"url": f"{reverse('identity-settings')}?assistant=1",
             "label": "Identity & security"},
        ]

    def test_the_strip_is_capped(self, client):
        """`MAX_JUMP_LINKS`. An answer that happens to name every
        registry page must not turn the jump strip into an index of the
        box."""
        from agents.chat.context_processors import MAX_JUMP_LINKS
        from foundation.settings_help import CARDS

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(conversation, " and ".join(card.title for card in CARDS))
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        links = response.context["assistant"]["links"]
        assert len(links) == MAX_JUMP_LINKS
        assert len(CARDS) > MAX_JUMP_LINKS, "this pin needs a registry bigger than the cap"

    def test_a_pending_turns_empty_text_names_nothing(self, client):
        """A turn still `Thinking…` has no answer text yet -- the
        collapsed-by-construction absence of a false match, not a special
        case this leg has to code around."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                     text="", state=Turn.State.RUNNING)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == []


class TestTheInlineLinkification:
    """OWNER FEEDBACK, LIVE, SCREENSHOT-VERIFIED, 2026-09-13 -- the second
    finding on the exact scenario `249523e` already closed for the jump
    strip. The finale answer "reads glitched": it speaks in internal
    route names ("chat-tool-entitlements", "identity-settings
    (identity-settings)") and its only links sit in a separate Jump-to
    strip below, never IN the sentence the operator is reading. The
    owner's own expectation: the page names already in the answer should
    be the clickable part.

    `_linkify_named_pages` (`agents/chat/context_processors.py`) is the
    platform-side half of the fix -- it wraps a matched page name IN
    PLACE, in `text_html`, reusing the exact same whitelist matcher
    `249523e` built (`_page_alias_regex`/`CARDS`), never touching the
    Jump-to strip's own separate mechanism at all. The prompt half (a
    fold-in sentence in `agents/defaults.py`) is what stops the model
    reaching for the ugly route-name form in the first place; these
    tests are about the platform half only.
    """

    def _answer(self, conversation, text):
        return make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         text=text, state=Turn.State.DONE)

    def _html(self, client, text):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(conversation, text)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        return str(response.context["assistant"]["cards"][-1]["text_html"])

    def test_the_owners_scenario_a_title_and_a_route_name_both_link(self, client):
        """THE EXACT DEFECT. Both spellings the owner's own screenshot
        showed unlinked -- a canonical title and a bare route name --
        become clickable, each pointing at its own real page, and the
        VISIBLE TEXT stays exactly what the model wrote (never rewritten
        to the canonical title)."""
        html = self._html(
            client,
            "To change this, go to Identity & security and also see "
            "chat-tool-entitlements for tool access.")
        assert (f'<a href="{reverse("identity-settings")}?assistant=1">'
                'Identity &amp; security</a>') in html
        assert (f'<a href="{reverse("chat-tool-entitlements")}?assistant=1">'
                'chat-tool-entitlements</a>') in html

    def test_a_page_not_in_the_registry_gets_no_inline_link(self, client):
        html = self._html(client, "Try the Admin Backdoor page for that.")
        assert "<a href=" not in html
        assert "Admin Backdoor" in html

    def test_hostile_text_with_fake_pages_and_markup_shaped_titles_never_linkifies(
            self, client):
        """THE WHITELIST HOLDS UNDER ADVERSARIAL TEXT, restated for the
        inline leg exactly as `TestTheNamedPageLinks` already states it
        for the strip. An attempted injected anchor is dead, escaped
        text -- `render_answer`'s own contract, untouched by this fix --
        and no fake page name becomes a real link either."""
        html = self._html(
            client,
            "Go to the Super Secret Admin page, or django-admin, or "
            "<a href=\"https://evil.example/\">click here</a> for System "
            "Configuration.")
        assert "<a href=" not in html
        assert "evil.example" in html  # inert escaped text, never a live href
        assert '&lt;a href=&quot;https://evil.example/&quot;&gt;' in html

    def test_no_double_escaping_of_an_ampersand_in_a_linkified_title(self, client):
        """DOUBLE-ESCAPING REGRESSION PIN. `_linkify_named_pages` runs on
        text `render_answer` has ALREADY escaped -- "Identity & security"
        is already spelled "Identity &amp; security" by the time this
        function's own regex ever sees it, matched by searching for
        `escape(card.title)`, never the raw title, for exactly that
        reason. A naive implementation that escaped the matched span a
        SECOND time before re-inserting it would turn `&amp;` into
        `&amp;amp;`; this asserts that never happens."""
        html = self._html(client, "See Identity & security for this.")
        assert "Identity &amp; security" in html
        assert "&amp;amp;" not in html

    def test_only_the_first_occurrence_of_a_page_is_linkified(self, client):
        """Link spam guard: the same page named three times, by both
        spellings, earns exactly one link."""
        html = self._html(
            client,
            "Identity & security. Again, Identity & security. And "
            "identity-settings once more.")
        assert html.count("<a href=") == 1

    def test_a_shorter_title_never_wins_over_a_longer_overlapping_route_name(
            self, client):
        """NON-OVERLAPPING, LONGEST-MATCH-ON-A-TIE. `"Chat"` (the
        `chat-settings` card's own title) is a word-bounded token INSIDE
        `"chat-tool-entitlements"` (hyphens are not `\\w`), so both
        candidates start at the same position -- the longer, more
        specific route-name match must be the one that survives, never
        a link to the wrong (`chat-settings`) page nested or crossed
        with the right one."""
        html = self._html(client, "See chat-tool-entitlements for this.")
        assert html.count("<a href=") == 1
        assert (f'<a href="{reverse("chat-tool-entitlements")}?assistant=1">'
                'chat-tool-entitlements</a>') in html
        assert reverse("chat-settings") not in html

    def test_q7_a_lowercase_generic_one_word_title_in_unrelated_prose_gets_no_link(
            self, client):
        """Q7 TIGHTENING (owner ruling, final-code resilience battery,
        2026-09-13), restated for the inline leg exactly as
        `TestTheNamedPageLinks` states it for the strip: the drilled
        scenario's own prose, mentioning "library" only in passing about
        library ACCESS POSTURE, must not become a clickable link to
        `rag-settings` (whose own title happens to be the identical
        ordinary word) with no `settings.card` call behind it."""
        html = self._html(
            client,
            "The library posture is open, so it is not restricted to "
            "administrators only.")
        assert "<a href=" not in html
        assert "library posture" in html

    def test_q7_exact_case_one_word_title_still_links(self, client):
        """The registry's own capitalisation still links on its own, no
        card fetched this turn -- the tightening narrows CASE, never
        whether a one-word title can ever match inline."""
        html = self._html(client, "Check the Library page for this.")
        assert (f'<a href="{reverse("rag-settings")}?assistant=1">'
                'Library</a>') in html

    def test_q7_a_fetched_cards_own_lowercase_title_still_links(self, client):
        """THE OVERRIDE HALF OF THE RULING: `settings.card(page=
        "rag-settings")` was actually called this turn -- the tool turn
        immediately preceding the answer carries its own anchor link --
        so a lowercase, in-passing "library" is no longer a coincidental
        word collision; it is the page the model just, visibly, read."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            make_turn(conversation=conversation, role=Turn.Role.TOOL, text="",
                     state=Turn.State.DONE,
                     data={"links": [
                         {"route": "rag-settings", "anchor": "retention",
                          "label": "Retention"}]},
                     tool_call={"tool": "settings.card", "args": {}, "agent": SLUG,
                                "id": "", "discarded": []})
            self._answer(
                conversation,
                "Check the library for the maximum upload size.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        html = str(response.context["assistant"]["cards"][-1]["text_html"])
        assert (f'<a href="{reverse("rag-settings")}?assistant=1">'
                'library</a>') in html

    def test_q7_multi_word_titles_still_match_case_insensitively(self, client):
        """Scope check: the tightening is ONE-WORD titles only -- a
        multi-word title keeps matching regardless of case, exactly as
        before this ruling."""
        html = self._html(client, "This lives under identity & security.")
        assert (f'<a href="{reverse("identity-settings")}?assistant=1">'
                'identity &amp; security</a>') in html

    def test_the_jump_strip_is_unaffected_and_still_carries_its_own_links(
            self, client):
        """EXISTING STRIP PINS UNTOUCHED: the inline leg and the Jump-to
        strip are two separate mechanisms over the same answer, and this
        proves they coexist rather than one crowding out the other --
        `assistant["links"]` (the strip, `249523e`) and the card's own
        `text_html` (this round's inline links) both carry their own
        entry for the same named page, independently."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            conversation = _thread(user_principal(admin))
            self._answer(conversation, "See Identity & security for this.")
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        assert response.context["assistant"]["links"] == [
            {"url": f"{reverse('identity-settings')}?assistant=1",
             "label": "Identity & security"},
        ]
        html = str(response.context["assistant"]["cards"][-1]["text_html"])
        assert (f'<a href="{reverse("identity-settings")}?assistant=1">'
                'Identity &amp; security</a>') in html

    def test_render_answers_own_formatting_is_unaffected_when_nothing_matches(
            self, client):
        """The existing pin this round must not disturb:
        `TestTheOpenPanel::test_an_assistant_turn_is_rendered_through_the_
        one_renderer`'s own claim, re-checked from this fix's own angle --
        an answer naming no registry page renders exactly as
        `render_answer` alone would have produced it."""
        html = self._html(client, "**bold** and <script>alert(1)</script>")
        assert "<strong>bold</strong>" in html
        assert "<script>" not in html
        assert "<a href=" not in html


class TestThePanelRoute:
    def test_it_returns_the_same_fragment_the_inline_panel_renders(self, client):
        """THE `_turn_block.html` PRECEDENT: this page's inline render and
        the poll body come out of the SAME builder over the SAME rows."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            response = client.get(
                reverse("settings-assistant-panel")
                + f"?assistant=1&next={reverse(A_SETTINGS_PAGE)}")
        assert response.status_code == 200
        assert "a0" in response.content.decode()

    def test_it_writes_nothing(self, client):
        from agents.models import Conversation

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            sign_in(client, admin)
            before = Conversation.objects.count()
            client.get(reverse("settings-assistant-panel"))
        assert Conversation.objects.count() == before

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_open_posture_fragment_costs_exactly_five_queries(
            self, client, django_assert_num_queries, turns):
        # 5, NOT 3 -- MEASURED, Task 10 Step 9. The isolated "+3" pin
        # above (`TestTheOpenPanel::test_the_open_panel_is_flat_in_the_
        # number_of_turns`, agent + conversation + turns) is measured on
        # a bare `panel_context(request)` call through `rf`, with no
        # middleware chain in front of it. THIS test goes through a real
        # `client.get()`, which pays two costs that call never sees:
        # `IdentityGateMiddleware`'s own `IdentitySettings.get_solo()`
        # read (deliberately UNCACHED -- `identity/models.py`'s own
        # docstring: "a cache would be a second truth with a staleness
        # window"), and `models.registry.context_processors`'s own
        # `RoleBinding` read, which runs on every template `render()` in
        # the box and is not this view's to avoid. FLAT at both 0 and 12
        # turns, the invariant this pin protects.
        with posture(POSTURE_OPEN):
            _thread(_open_principal(), turns=turns)
            url = reverse("settings-assistant-panel") + "?assistant=1"
            client.get(url)
            with django_assert_num_queries(5):
                client.get(url)

    @pytest.mark.parametrize("turns", [0, 12])
    def test_the_accounts_on_fragment_number_is_flat_and_pinned_by_equality(
            self, client, django_assert_num_queries, turns):
        # MEASURED — Task 10, Step 9: the view's own class-S belt-and-
        # suspenders `is_admin` check (Task 8 review, m1) is threaded
        # onto the SAME `settings_row` instance `panel_context` reuses,
        # so it costs the request's first `auth_user` read rather than a
        # second one -- see `agents/chat/views/assistant.py`'s own
        # docstring. FLAT at both 0 and 12 turns, the invariant this pin
        # protects.
        MEASURED_FRAGMENT_ENTERPRISE = 15
        assert MEASURED_FRAGMENT_ENTERPRISE is not None, (
            "measure this number, then write it in as an equality")
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=turns)
            sign_in(client, admin)
            url = reverse("settings-assistant-panel") + "?assistant=1"
            client.get(url)
            with django_assert_num_queries(MEASURED_FRAGMENT_ENTERPRISE):
                client.get(url)


class TestThePanelOnThePage:
    def test_it_renders_on_every_settings_page_for_an_administrator(self, client, settings):
        """EVERY REGISTERED PAGE, driven from `SETTINGS_GROUPS` itself
        rather than a hand-typed list, so a page added later is swept the
        day it is added -- which is what happened at F1, Coherence Wave
        C, when "Job execution" became the twelfth. PINS ITS OWN FEATURE STATE (Global Constraint 13): one
        entry is gated on a feature token, and a test that inherited
        `FARABUNKER_FEATURES` from the environment would silently skip it
        in one of the two supported suite states."""
        from foundation.settings_area import SETTINGS_GROUPS

        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            swept = 0
            for _group, entries in SETTINGS_GROUPS:
                for entry in entries:
                    body = client.get(reverse(entry.url_name)).content.decode()
                    assert 'id="assistant-panel"' in body, entry.url_name
                    swept += 1
        # I3 (Wave C review): compared against the GATED view of the
        # table, never against the loop's own `sum(len(entries) ...)` --
        # that earlier spelling re-derived the count from the very
        # structure the unconditional loop above walks, so it could not
        # fail under any change and the comment beside it claimed a
        # guarantee it structurally did not provide. `visible_entries`
        # applies `_may_see` (standing, posture and `Entry.feature`), so
        # this DOES disagree the moment the loop walks an entry this
        # viewer would not be offered -- which is exactly the case where
        # sweeping it proves nothing.
        assert swept == len(visible_entries(
            admin=True, posture=POSTURE_ENTERPRISE, features=frozenset({"vision"}))), swept

    def test_the_panel_css_reaches_every_settings_leaf(self, client, settings):
        """I1 (final review): `id="assistant-panel"` markup rendering is
        not the same claim as the CSS reaching the page -- the test
        above pins the former on every leaf and would not have
        caught this. `foundation/setup/templates/setup/index.html` and
        `models/registry/templates/inference/console.html` both
        overrode `{% block extra_style %}` with no `{{ block.super }}`
        UNTIL COHERENCE WAVE A (`foundation/tests/test_shell.py::
        TestTheTargetHighlight`'s own docstring names both, corrected by
        this same finding), so a rule placed in `_settings.html`'s
        `extra_style` -- where these seven `.assistant-*` rules used to
        live -- never reached either page's rendered `<style>` at all.
        They now live in `_shell.html`'s unconditional style region
        instead. Coherence Wave B (N5 re-review): walk EVERY leaf
        `SETTINGS_GROUPS` names, same as the markup test above, instead of
        naming only the two that once proved the placement wrong -- the
        method's name claimed "every settings leaf" before this and only
        checked two."""
        from foundation.settings_area import SETTINGS_GROUPS

        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            swept = 0
            for _group, entries in SETTINGS_GROUPS:
                for entry in entries:
                    body = client.get(reverse(entry.url_name)).content.decode()
                    assert ".assistant-panel {" in body, entry.url_name
                    assert ".assistant-transcript {" in body, entry.url_name
                    assert ".assistant-jump {" in body, entry.url_name
                    swept += 1
        # I3 (Wave C review): compared against the GATED view of the
        # table, never against the loop's own `sum(len(entries) ...)` --
        # that earlier spelling re-derived the count from the very
        # structure the unconditional loop above walks, so it could not
        # fail under any change and the comment beside it claimed a
        # guarantee it structurally did not provide. `visible_entries`
        # applies `_may_see` (standing, posture and `Entry.feature`), so
        # this DOES disagree the moment the loop walks an entry this
        # viewer would not be offered -- which is exactly the case where
        # sweeping it proves nothing.
        assert swept == len(visible_entries(
            admin=True, posture=POSTURE_ENTERPRISE, features=frozenset({"vision"}))), swept

    def test_it_does_not_render_for_a_member_on_the_public_settings_page(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("setup-index")).content.decode()
        assert 'id="assistant-panel"' not in body

    def test_it_introduces_no_second_page_name(self, client):
        """`foundation/tests/test_page_names.py`'s one-name-per-page rule
        sweeps every settings page; the panel must add no `<h1>`."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert body.count("<h1") == 1

    def test_exactly_one_composer_renders_per_settings_page(self, client):
        """`id="composer-text"`'s accessible-name invariant
        (`chat/_composer.html:87-97`) holds unchanged only if this does."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert body.count('id="composer-text"') == 1

    def test_the_panel_offers_no_attach_door_and_no_model_picker(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert "attach-block" not in body
        assert 'name="connection"' not in body
        assert 'name="agent"' not in body

    def test_an_uninstalled_box_renders_the_offer_instead_of_the_composer(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert 'id="composer-text"' not in body
        assert f'value="{SLUG}"' in body
        assert reverse("chat-default-install") in body

    def test_installing_from_the_opened_offer_lands_back_on_the_open_panel(self, client):
        """Task 10 review, n2 -- UPDATED BY THE PLACEMENT-ROUND FOLLOW-UP
        (owner review, 2026-09-11). This test USED TO fetch the panel
        COLLAPSED (no `?assistant=1`) and still find the install offer's
        form in the body, on the reasoning that "an operator can expand
        the `<details>` with its own native `<summary>` toggle, client-
        side, with no navigation". That path is exactly what this round
        removed (see `chat/_assistant_panel.html`'s own docstring): the
        offer now renders ONLY inside the open branch, so it is only ever
        reachable by way of a real `?assistant=1` request. What the
        original test protected still matters and is still true --
        `install_next` must carry the open marker so the post-install
        redirect lands the operator back on a page showing the panel
        open, one click closer to the composer they just installed
        rather than one click further -- it is just proven on the page
        this offer actually renders on now."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        here = reverse(A_SETTINGS_PAGE)
        assert (f'<input type="hidden" name="next" value="{here}?assistant=1">'
                in body)

    def test_no_client_side_storage_appears_anywhere_in_the_panel(self, client):
        """SPEC §6.7. PERSISTENCE IS SERVER-SIDE ONLY -- `Conversation`
        and `Turn` rows. The idiom is
        `tools/vision/tests/test_views_create.py:1000-1004`'s."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        for banned in ("localStorage", "sessionStorage", "indexedDB"):
            assert banned not in body, banned


class TestTheFloatingCornerWidget:
    """PLACEMENT ROUND, RULING 1 (owner walk, 2026-09-11): the panel used
    to render in flow, at the bottom of `.settings-main` -- a 24px
    collapsed bar the owner could not find on the models console without
    scrolling roughly four screens. Fixed bottom-right presence instead.

    UPDATED FOR THE PLACEMENT-ROUND FOLLOW-UP (owner review, 2026-09-11):
    ruling 1's first draft kept `<details class="assistant-panel">` in
    both states and restyled them with CSS alone. The review's own
    finding was that this made the bubble's native `<summary>` toggle far
    more reachable than the old in-flow bar ever was, and that toggle
    opened the SAME element locally with no navigation -- revealing
    whatever the server had already rendered, which for the collapsed
    (zero-read) render was an EMPTY transcript for an administrator with
    real history. `chat/_assistant_panel.html` now renders a plain `<a>`
    when closed (no `<details>`, no local toggle to open with) and a
    `<details open>` -- always open, never rendered any other way -- when
    open, with an explicit `Close` link back. See that template's own
    docstring for the full account."""

    def test_the_bubble_is_fixed_and_corner_pinned_on_every_settings_page(
            self, client, settings):
        """Swept the same way `TestThePanelOnThePage::test_it_renders_on_
        every_settings_page_for_an_administrator` sweeps them all --
        this CSS lives in the shell's own unconditional `<style>`, so it
        reaches every one of them regardless of which leaf's own
        `extra_style` block does or does not call `{{ block.super }}`.
        Fetched COLLAPSED (no `?assistant=1`) on every page, which is now
        also a markup claim, not only a CSS one: the closed bubble is a
        plain `<a href>`, never a `<details>`."""
        from foundation.settings_area import SETTINGS_GROUPS

        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            swept = 0
            for _group, entries in SETTINGS_GROUPS:
                for entry in entries:
                    body = client.get(reverse(entry.url_name)).content.decode()
                    rule_start = body.index(".assistant-panel {")
                    rule = body[rule_start:body.index("}", rule_start)]
                    assert "position: fixed" in rule, entry.url_name
                    assert "right: 1.25rem" in rule, entry.url_name
                    assert "bottom: 1.25rem" in rule, entry.url_name
                    assert '<a class="assistant-panel" id="assistant-panel"' in body, \
                        entry.url_name
                    assert '<details class="assistant-panel"' not in body, entry.url_name
                    swept += 1
        # I3 (Wave C review): compared against the GATED view of the
        # table, never against the loop's own `sum(len(entries) ...)` --
        # that earlier spelling re-derived the count from the very
        # structure the unconditional loop above walks, so it could not
        # fail under any change and the comment beside it claimed a
        # guarantee it structurally did not provide. `visible_entries`
        # applies `_may_see` (standing, posture and `Entry.feature`), so
        # this DOES disagree the moment the loop walks an entry this
        # viewer would not be offered -- which is exactly the case where
        # sweeping it proves nothing.
        assert swept == len(visible_entries(
            admin=True, posture=POSTURE_ENTERPRISE, features=frozenset({"vision"}))), swept

    def test_the_open_state_restyles_into_a_bounded_overlay_card(self, client):
        """OPEN-STATE OVERLAY CLASSES. `details.assistant-panel` restyles
        the whole `<details>` into a card -- unconditionally, since this
        template only ever emits an OPEN one (see the class docstring) --
        `max-height` plus `overflow: hidden` is the "must not obscure a
        narrow page's own Save button" requirement: the box can grow no
        taller than its own ceiling regardless of transcript length."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        rule_start = body.index("details.assistant-panel {")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "display: flex" in rule
        assert "flex-direction: column" in rule
        assert "max-height" in rule
        assert "overflow: hidden" in rule
        # THE TRANSCRIPT SCROLLS; THE COMPOSER STAYS VISIBLE -- only
        # `.assistant-transcript` is the flex child allowed to shrink and
        # scroll internally; the close link, the composer and the jump/
        # reset row around it are ordinary non-growing children, never
        # the part pushed off-screen by a long history.
        transcript_start = body.index(".assistant-transcript {")
        transcript_rule = body[transcript_start:body.index("}", transcript_start)]
        assert "overflow-y: auto" in transcript_rule
        assert "flex: 1 1 auto" in transcript_rule

    def test_the_details_wrapper_carries_no_in_flow_box(self, client):
        """NO LAYOUT SHIFT IN EITHER STATE. `position: fixed`, on the
        shared `.assistant-panel` base rule both the closed `<a>` and the
        open `<details>` carry, removes the element from `.settings-main`'s
        own flow entirely in both states -- there is no box left behind to
        push a page's own content, which is what makes "must not shift
        page layout in either state" true regardless of how tall the panel
        grows."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE)).content.decode()
        rule_start = body.index(".assistant-panel {")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "position: fixed" in rule
        assert "margin-top: 1.5rem" not in rule

    def test_opening_via_the_native_local_toggle_is_no_longer_possible(self, client):
        """THE REGRESSION THIS ROUND CLOSES. Collapsed, there is no
        `<details>` in the DOM at all -- nothing a script-free click can
        toggle open locally, so there is no way left to reveal the
        collapsed render's own empty transcript. The ONLY way to open the
        panel is `assistant.open_url`, a real link that forces a fresh
        request through `panel_context` with `is_open=True` -- the leg
        that actually reads the conversation."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE)).content.decode()
        assert "<details" not in body
        assert f'<a class="assistant-panel" id="assistant-panel" href="{reverse(A_SETTINGS_PAGE)}?assistant=1">' in body

    def test_the_open_panel_carries_its_one_script_and_a_link_back(self, client):
        """JS-OFF TOGGLE, RE-STATED FOR THE NEW SHAPE. Opening and closing
        are both plain navigations (zero-JS by construction, not merely
        by a script's own defensiveness); the sanctioned poller script
        still renders exactly once, only now, only on the open page where
        it has something to act on (`TestTheOneScript::test_the_panel_
        carries_exactly_one_script_tag`'s own pin, re-affirmed here from
        the placement round's own angle).

        RE-PINNED 1 -> 2 (the unsaved-input round, 2026-09-16): the
        SECOND is not the panel's, it is the settings chrome's own
        navigation guard -- one document-delegated `beforeunload` scan
        declared in `foundation/templates/_settings.html`, so it is on
        every settings page whether this panel is open, collapsed, or not
        rendered for this viewer at all (`foundation/tests/test_shell.py
        ::TestTheUnsavedInputGuard`). The panel's own budget is unchanged
        and is still what this assertion is about: the number below minus
        the chrome's one. The composer's Enter-to-send does not render
        here -- the assistant is not installed in this test, so there is
        no composer."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert '<details class="assistant-panel" id="assistant-panel" open>' in body
        # `class="assistant-close"`, layout economy round: Close moved
        # onto the header row itself, carrying the compact-header style
        # class it did not need as its own full-width `<p>` before.
        assert f'<a href="{reverse(A_SETTINGS_PAGE)}" class="assistant-close">Close</a>' in body
        assert body.count("<script>") == 2   # this panel's poller, plus the chrome's guard
        assert "onclick" not in body

    def test_an_admin_with_history_who_clicks_the_bubble_gets_a_rendered_transcript(
            self, client):
        """THE PRODUCT PIN (owner review, 2026-09-11): the whole point of
        the fix. An administrator with real turns on their own thread
        fetches a settings page COLLAPSED, follows the bubble's own
        `href` exactly as a browser click would, and must land on a page
        that actually shows that history -- not the empty-transcript
        fallback string, and not zero turns."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            collapsed = client.get(reverse(A_SETTINGS_PAGE)).content.decode()
            href_start = collapsed.index('<a class="assistant-panel" id="assistant-panel" href="') + len(
                '<a class="assistant-panel" id="assistant-panel" href="')
            href = collapsed[href_start:collapsed.index('"', href_start)]
            opened = client.get(href).content.decode()
        assert 'class="assistant-transcript"' in opened
        assert "q0" in opened
        assert "a0" in opened
        assert "Ask about any setting on this box" not in opened


class TestTheTranscriptIsGenuinelyScrollable:
    """OWNER FEEDBACK, LIVE, SCREENSHOT-VERIFIED, 2026-09-13 (the third
    finding in the same session): the open panel's transcript could not
    be scrolled at all -- the answer's own top was clipped mid-line with
    no way to read it. Diagnosed live against the real running page
    (`scrollHeight`/`clientHeight` measured on `details.assistant-panel`,
    `#assistant-body` and `.assistant-transcript`, in that order) before
    this fix, and re-measured the same way after: `<details>` as a flex
    CONTAINER does not propagate its own resolved size into its
    children's flex-shrink distribution the way an ordinary flex-`<div>`
    does, so neither `#assistant-body` nor `.assistant-transcript` was
    ever actually being constrained, no matter what `<details>` itself
    declared. The fix gives `#assistant-body` -- a plain `<div>`, one
    level in, unaffected by the `<details>` quirk -- its OWN explicit
    `max-height`, which is what finally lets `.assistant-transcript`'s
    existing `flex: 1 1 auto; min-height: 0; overflow-y: auto` actually
    reach a bounded, overflowing box. `foundation/templates/_shell.html`'s
    own comment beside both rules carries the full live-verified account.
    """

    def test_the_transcript_declares_a_scrollable_overflow_y_auto(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        rule_start = body.index(".assistant-transcript {")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "overflow-y: auto" in rule
        # `column-reverse`, NOT `column` -- the CSS-only way the panel
        # opens already scrolled to the newest turn (below), pinned here
        # as the rule that makes it possible.
        assert "flex-direction: column-reverse" in rule

    def test_the_scroll_region_is_bounded_by_the_bodys_own_max_height(self, client):
        """THE BOUNDED HEIGHT the transcript's own `overflow-y: auto`
        needs to ever actually trigger -- declared on `#assistant-body`
        (the level the `<details>` quirk does not reach), not on
        `.assistant-transcript` itself or on `<details>`, which is
        exactly the live-verified finding this round's own diagnosis
        turned up."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        rule_start = body.index("details.assistant-panel #assistant-body {")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "max-height" in rule
        assert "overflow: hidden" in rule

    def test_the_no_layout_shift_and_z_index_pins_still_hold(self, client):
        """RE-AFFIRMED, NOT RE-DERIVED: this round's fix touches only
        `#assistant-body`'s own bounding and `.assistant-transcript`'s
        own flex direction -- the shared `.assistant-panel { position:
        fixed; ...; z-index: 30; }` base rule both the bubble and the
        card still carry is untouched. `TestTheFloatingCornerWidget`'s
        own tests already prove this in full; this is the one-line
        confirmation that the CLAIM those tests make is still the claim
        this round leaves standing."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        rule_start = body.index(".assistant-panel {")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "position: fixed" in rule
        assert "z-index: 30" in rule

    def test_turns_render_newest_first_in_the_dom(self, client):
        """THE OTHER HALF OF THE FIX, IN THE MARKUP: `chat/_assistant_
        panel.html` now iterates `assistant.cards reversed`, so the
        NEWEST turn is the FIRST element inside `.assistant-transcript`
        -- `.assistant-transcript`'s own `column-reverse` is what turns
        this back into ordinary reading order on screen (oldest at the
        top, newest at the bottom) while giving the browser's own
        default (unscrolled) position on the newest turn. Proven on the
        panel's own CONTEXT list first (still chronological, oldest to
        newest -- `TestTheOpenPanel::test_it_shows_the_last_n_turns_not_
        the_whole_history`'s own claim, untouched by this fix, which
        only changed how the TEMPLATE walks that same list) and then on
        the rendered DOM order, which must be the reverse of it."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=3)
            sign_in(client, admin)
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        cards = response.context["assistant"]["cards"]
        assert [c["text"] for c in cards if c["text"]] == ["q0", "a0", "q1", "a1", "q2", "a2"]
        body = response.content.decode()
        # THE NEWEST TURN'S OWN TEXT COMES FIRST IN THE MARKUP, in the
        # EXACT reverse of the context list's own chronological order.
        # MARKERS INCLUDE EACH TURN'S OWN SURROUNDING MARKUP, not bare
        # `"q2"`/`"a2"`: a bare two-character needle is exactly the shape
        # of substring a per-request CSRF token (random base64-ish
        # characters, several of them on this page -- the composer, the
        # reset form) can coincidentally contain, which showed up as a
        # real, order-dependent flake while writing this test, not a
        # false failure in the fix itself.
        markers = [
            'assistant-answer"><p>a2</p>', "You:</strong> q2",
            'assistant-answer"><p>a1</p>', "You:</strong> q1",
            'assistant-answer"><p>a0</p>', "You:</strong> q0",
        ]
        positions = [body.index(marker) for marker in markers]
        assert positions == sorted(positions), positions


class TestTheLayoutIsEconomical:
    """OWNER FEEDBACK, LIVE, 2026-09-13 (on top of the scroll fix above,
    same day): "the scroll is too tight to be readable, the chat window
    needs to be expanded and the entire chat menu optimized for space --
    so much wasted space." The panel's own budget grows (wider, and tall
    enough to use most of the viewport); the chrome around the
    transcript shrinks (Close moves onto the header row, Start over is
    demoted from a primary-scale button to a quiet link merged onto the
    composer's own Ask row, the composer is content-sized) so the
    TRANSCRIPT -- the reason this panel exists -- gets the space those
    savings free. Verified live against the real running preview page
    with a genuinely long conversation before landing (the same method
    the scroll fix above used): the transcript took the majority of the
    panel's own rendered height and stayed a bounded, scrolling region,
    never the whole-panel clip the scroll fix closed.
    """

    def test_the_panel_bounds_grew(self, client):
        """WIDER (`26rem`, up from `22rem`) and TALLER
        (`min(80vh, calc(100vh - 2.5rem))`, up from a flat `32rem`/
        `6.5rem` budget) -- still shrinking to fit shorter content, still
        never touching either viewport edge."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        rule_start = body.index("details.assistant-panel {")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "width: min(26rem, calc(100vw - 2.5rem))" in rule
        assert "max-height: min(80vh, calc(100vh - 2.5rem))" in rule

    def test_the_transcript_still_declares_the_grow_and_scroll_role(self, client):
        """RE-AFFIRMED, NOT RE-DERIVED: the layout-economy round changes
        the BUDGET numbers (this class's own tests) but not the
        mechanism the scroll fix already proved -- `.assistant-
        transcript` is still the flex child that grows and scrolls, on
        `#assistant-body`'s own explicit `max-height` (the level the
        `<details>` quirk does not reach, `TestTheTranscriptIsGenuinely
        Scrollable`'s own docstring names why)."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        transcript_rule = body[body.index(".assistant-transcript {"):]
        transcript_rule = transcript_rule[:transcript_rule.index("}")]
        assert "flex: 1 1 auto" in transcript_rule
        assert "overflow-y: auto" in transcript_rule
        body_rule_start = body.index("details.assistant-panel #assistant-body {")
        body_rule = body[body_rule_start:body.index("}", body_rule_start)]
        assert "max-height" in body_rule

    def test_close_moved_onto_the_compact_header_row(self, client):
        """THE HEADER ROW IS A FLEX ROW NOW, title left, `Close` right --
        replacing the full-width line `Close` used to get to itself
        below the header (one of the "so much wasted space" pieces named
        live). Both the CSS shape and the rendered markup are asserted:
        the rule change alone would pass even if the template still
        wrapped `Close` in its own `<p>`."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        rule_start = body.index("details.assistant-panel summary {")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "display: flex" in rule
        assert "justify-content: space-between" in rule
        assert '<summary><span>Settings assistant</span><a href=' in body
        assert 'class="assistant-close">Close</a></summary>' in body
        assert '<p class="assistant-close">' not in body

    def test_start_over_is_no_longer_a_primary_scale_button(self, client):
        """DEMOTED (owner's own words: "the largest thing in the
        panel"). `nav-link-button` is `_shell.html`'s own EXISTING quiet-
        control idiom (the account area's Sign-out control already uses
        it) reused here, not a new class -- the rendered markup is the
        pin, since the class carrying the visual claim already has its
        own CSS asserted nowhere near this fragment."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        assert '<button type="submit" class="nav-link-button">Start over</button>' in body
        assert '<button type="submit">Start over</button>' not in body

    def test_the_composer_is_scoped_compact_not_the_shared_recipe(self, client):
        """SCOPED TO THIS PANEL ALONE: `details.assistant-panel .composer-
        card`/`.composer-textarea`, never a rewrite of the SHARED
        `.composer-card`/`.composer-textarea` rules `/chat/`'s own three
        composer surfaces still use unchanged -- `foundation/ops/tests/
        test_css_ownership.py::test_the_shared_composers_selectors_live_
        in_the_shell_not_in_chat_base`'s own claim about those shared
        selectors is untouched by this round, which is what this test's
        second half confirms."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        panel_scoped_start = body.index("details.assistant-panel .composer-card {")
        panel_scoped = body[panel_scoped_start:body.index("}", panel_scoped_start)]
        assert "padding: .5rem .6rem" in panel_scoped
        textarea_scoped_start = body.index("details.assistant-panel .composer-textarea {")
        textarea_scoped = body[textarea_scoped_start:body.index("}", textarea_scoped_start)]
        assert "min-height: 2.8rem" in textarea_scoped
        # THE SHARED RULE'S OWN VALUE IS STILL THERE TOO, UNCHANGED --
        # a MORE SPECIFIC panel-scoped rule was added AFTER it in the
        # file (deliberately: the bare `.composer-textarea {` this
        # assertion searches for is a SUBSTRING of the panel-scoped
        # selector above, so placing the panel-scoped rule BEFORE the
        # shared one would make a first-match search silently re-find
        # the wrong rule -- two PRE-EXISTING, unrelated `/chat/` page
        # tests already do exactly this naive `.index(".composer-
        # textarea {")` search (`test_index.py::TestTheComposerCardFont::
        # test_the_shared_composer_textarea_inherits_the_page_font`,
        # `test_workstream_page.py::TestTheDesignPassReviewFixRound::
        # test_the_new_chat_textarea_inherits_the_page_font`), and this
        # ordering is what keeps them finding the shared rule rather
        # than a settings-panel rule they know nothing about -- caught
        # live, by running them, not assumed).
        shared_start = body.index(".composer-textarea {")
        shared_rule = body[shared_start:body.index("}", shared_start)]
        assert "min-height: 4.5rem" in shared_rule

    def test_no_layout_shift_and_z_index_pins_still_hold(self, client):
        """RE-AFFIRMED AGAIN: this round touches the panel's own size
        budget, the header row, the composer and the reset control --
        never the shared `.assistant-panel { position: fixed; ...;
        z-index: 30; }` base rule both the bubble and the card still
        carry."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()
        rule_start = body.index(".assistant-panel {")
        rule = body[rule_start:body.index("}", rule_start)]
        assert "position: fixed" in rule
        assert "z-index: 30" in rule


def _finish_pending():
    """Settle every queued or running turn, so the NEXT ask in a test is
    not refused for a turn already in flight.

    `start_turn` refuses with a 409 while any ASSISTANT turn on the
    conversation is `QUEUED` or `RUNNING` (`agents/chat/service.py:249-256`)
    -- "one turn at a time per conversation", because `Turn.next_index`
    is a read-then-write whose safety rests on exactly that. The queue is
    patched at the `agents.chat.service` seam in these tests, so no
    worker exists to move these rows and nothing else will. This is not a
    shortcut around the rule; it is what the worker would have done.
    """
    Turn.objects.filter(
        role=Turn.Role.ASSISTANT,
        state__in=(Turn.State.QUEUED, Turn.State.RUNNING),
    ).update(state=Turn.State.DONE, text="an answer")


class TestTheOneScript:
    def _panel(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            return client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1").content.decode()

    def test_the_three_constants_come_from_python_and_are_never_retyped(self, client):
        """SPEC §6.7: declared ONCE in `agents/chat/service.py:93-95` and
        handed to the template -- the rule
        `chat/conversation.html:396-407` states, followed here.

        ASSERTED AS THE VALUES, so a template that hard-coded 2000 would
        pass a name check and fail this."""
        from agents.chat.service import (
            MAX_POLL_DURATION_MS, MAX_TRANSPORT_RETRIES, POLL_INTERVAL_MS,
        )

        body = self._panel(client)
        script = body[body.index("<script"):]
        assert f"= {POLL_INTERVAL_MS};" in script
        assert f"= {MAX_TRANSPORT_RETRIES};" in script
        assert f"= {MAX_POLL_DURATION_MS};" in script

    def test_the_panel_carries_exactly_one_script_tag(self, client):
        """The budget is ONE -- THIS FEATURE's one. The other two on the
        page are EXISTING sanctioned scripts neither owned nor added by
        the panel, and are counted here so the assertion is honest about
        what is actually on the page rather than about what this feature
        would like to be measured on:

        * `chat/_enter_to_send.html`, included by the shared composer --
          the same script every composer surface in the app renders.
        * the settings chrome's unsaved-input navigation guard
          (`foundation/templates/_settings.html`, the round of
          2026-09-16), which is on EVERY settings page in both panel
          states and for every viewer the settings area renders for --
          this pin moved 2 -> 3 the day it shipped, and
          `foundation/tests/test_shell.py::TestTheUnsavedInputGuard` is
          where its own claims are pinned."""
        body = self._panel(client)
        assert body.count("<script") == 3   # this panel's one, Enter-to-send's, the chrome's guard

    def test_enter_to_send_is_document_delegated_so_it_survives_the_panels_own_swap(
            self, client):
        """m1 (final review): the panel replaces its own composer's
        container WHOLESALE on every poll (`current.replaceWith(fresh)`
        above), and an `innerHTML`-built fragment never re-executes the
        `<script>` tags inside it. `chat/_enter_to_send.html`'s listener
        used to bind once, at parse time, to `form.querySelector(
        "textarea")` -- a specific node the FIRST swap throws away, so
        Enter would insert a newline instead of sending from the first
        XHR ask onward, until a full page load; nothing rebound it the
        way `bind()` rebinds the submit handler. It now binds to
        `document` and resolves its form from `event.target` on every
        keystroke, so no swap can leave it stale -- pinned here, on the
        one page that actually swaps."""
        body = self._panel(client)
        assert 'document.addEventListener("keydown"' in body
        assert 'closest("form[data-enter-submits]")' in body
        assert 'form.querySelector("textarea").addEventListener' not in body

    def test_it_stores_nothing_on_the_client(self, client):
        body = self._panel(client)
        script = body[body.index("<script"):]
        for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            assert banned not in script, banned

    def test_it_polls_the_panel_route_and_never_a_url_it_builds_itself(self, client):
        """Reversed in Python and read off a data attribute -- the same
        reason `chat/conversation.html:29-33` gives for
        `data-pending-status-url`: a script that built a URL client-side
        would be a second URLconf."""
        body = self._panel(client)
        script = body[body.index("<script"):]
        assert "data-assistant-panel-url" in body
        assert reverse("settings-assistant-panel") not in script

    def test_it_sends_the_xhr_header_the_view_reads(self, client):
        body = self._panel(client)
        assert "X-Requested-With" in body[body.index("<script"):]

    def test_the_pending_marker_is_server_side_and_absent_when_nothing_runs(self, client):
        body = self._panel(client)
        assert "data-assistant-pending" not in body

    def test_the_pending_marker_appears_when_a_turn_is_queued(
            self, client, monkeypatch, bound_chat_role):
        """`bound_chat_role` -- a real `ModelConnection` bound to `chat.
        converse` -- is what lets `start_turn`'s own `preflight_turn`
        check pass so a turn is actually QUEUED here, the same forward-
        fix Task 10's report already names for this identical ask-then-
        assert-on-the-write shape (its own item 3): with no bound role
        the POST below still 302s, but `start_turn` refuses before
        anything is written, so the marker this test exists to pin would
        never appear regardless of what the script does with it."""
        _patch_queue(monkeypatch)
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            here = reverse(A_SETTINGS_PAGE)
            client.post(reverse("settings-assistant-ask"), {"text": "hi", "next": here})
            body = client.get(f"{here}?assistant=1").content.decode()
        assert 'data-assistant-pending="1"' in body


class TestThePanelSurvivesClickingThroughTheSettings:
    """THE PERSISTENCE ROUND, owner report, verbatim: "If I have the chat
    up and then I click on a new settings link, the menu goes back into
    hiding. Preserve the scroll location and the status of the chatbox so
    I can click through the settings while keeping the chat box active."

    THE WHOLE MECHANISM IS THE ONE QUERY PARAMETER, and these are the
    end-to-end pins on it: a REAL href taken out of a REAL rendered
    sidebar, followed exactly as a browser would, and the panel is still
    open on the page it lands on. Not a unit test of the string helper
    (`foundation/tests/test_settings_area.py` owns that) -- the claim
    here is about the shipped chrome.

    SCROLL IS NOT PERSISTED AND DOES NOT NEED TO BE: the transcript is
    `flex-direction: column-reverse` (`foundation/templates/_shell.html`),
    so every render of the open panel already lands on the newest
    exchange. That is the whole of the owner's "preserve the scroll
    location", with no script and nothing stored on the client -- which
    `TestTheOneScript` above pins in both directions.
    """

    _OPEN = '<details class="assistant-panel" id="assistant-panel" open>'

    def _sidebar_hrefs(self, body: str) -> list[str]:
        nav = body[body.index('<nav class="settings-nav"'):]
        nav = nav[:nav.index("</nav>")]
        return re.findall(r'<a href="([^"]+)"', nav)

    def test_every_sidebar_link_carries_the_panel_forward(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            opened = client.get(f"{reverse(A_SETTINGS_PAGE)}?assistant=1").content.decode()
            hrefs = self._sidebar_hrefs(opened)
            landed = [client.get(href).content.decode() for href in hrefs]
        assert hrefs, "the settings sidebar rendered no links at all"
        assert all(href.endswith("?assistant=1") for href in hrefs), hrefs
        for href, body in zip(hrefs, landed):
            assert self._OPEN in body, href
            assert "q0" in body, href

    def test_the_install_offer_survives_the_click_too(self, client):
        """REVIEW ROUND 1, N4, PINNED RATHER THAN ARGUED. The reviewer
        asked whether `open_query` should be gated on the assistant
        actually being INSTALLED, since an open panel on a box with no
        assistant row looked like a dead query parameter. It is not:
        `chat/_assistant_panel.html` gates its whole render on
        `assistant.open`, and the not-installed branch is a real,
        actionable panel -- the install OFFER. Gating the suffix on
        `installed` would strip the flag from every sidebar link on
        exactly the box where an administrator is being ASKED to act, so
        their next click would shut the offer.

        This is that path, end to end, with no assistant installed.
        """
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            sign_in(client, admin)
            opened = client.get(f"{reverse(A_SETTINGS_PAGE)}?assistant=1").content.decode()
            hrefs = self._sidebar_hrefs(opened)
            landed = client.get(hrefs[0]).content.decode()
        assert Agent.objects.filter(slug=SLUG).count() == 0
        assert self._OPEN in opened
        assert "Add the settings assistant" in opened
        assert all(href.endswith("?assistant=1") for href in hrefs), hrefs
        assert self._OPEN in landed
        assert "Add the settings assistant" in landed

    def test_a_collapsed_sidebar_still_navigates_collapsed(self, client):
        """THE OTHER HALF, and the one that keeps this from being a panel
        nobody can get away from: a plain navigation into the settings
        area starts collapsed and STAYS collapsed on every click."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            collapsed = client.get(reverse(A_SETTINGS_PAGE)).content.decode()
            hrefs = self._sidebar_hrefs(collapsed)
            landed = [client.get(href).content.decode() for href in hrefs]
        assert all("?" not in href for href in hrefs), hrefs
        for href, body in zip(hrefs, landed):
            # The PANEL's own `<details>`, not any `<details>`: several
            # settings pages use the element for their own disclosures.
            assert '<details class="assistant-panel"' not in body, href
            assert '<a class="assistant-panel" id="assistant-panel"' in body, href

    def test_the_bar_s_own_settings_entry_carries_it_too(self, client):
        """`/settings/` is a redirect, and the app bar's one `Settings`
        entry points at it -- following it with the panel open must land
        on a page that still has the panel open, the same as any sidebar
        entry.

        THE HREF COMES OUT OF THE RENDERED BAR (review round 1, I2).
        This test used to build `reverse("settings-index") +
        "?assistant=1"` by hand -- a URL the bar did not emit and nothing
        in the tree ever produced -- so it stayed green while the bar
        dropped the flag on every click and `settings_index`'s
        passthrough sat unreachable. Reading the real href is the whole
        difference between pinning the ROUTE and pinning the LINK. The
        bar's own two-state rendering is pinned in `foundation/tests/
        test_shell.py`, where the bar lives; this is the end-to-end
        follow-through.
        """
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            opened = client.get(f"{reverse(A_SETTINGS_PAGE)}?assistant=1").content.decode()
            bar = opened[opened.index('<header class="app-bar">'):]
            bar = bar[:bar.index("</header>")]
            href = re.search(r'<a href="([^"]*)"[^>]*>Settings</a>', bar).group(1)
            landed = client.get(href, follow=True).content.decode()
        assert href == f"{reverse('settings-index')}?assistant=1"
        assert self._OPEN in landed
        assert "q0" in landed

    def test_saving_a_setting_lands_back_on_an_open_panel(self, client):
        """THE SAVE HALF, end to end and through the real form: the
        action rendered on an open page carries the flag, the view
        redirects with it, and the page the browser follows to still has
        the panel -- and the transcript -- up. `chat-settings` is this
        column's own settings page (`A_SETTINGS_PAGE`), so this test
        depends on no other column's write path; the repo-wide claim,
        over every settings POST endpoint the box has, is
        `identity/tests/test_route_matrix.py::
        TestEverySettingsSaveKeepsTheAssistantPanelOpen`.
        """
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _thread(user_principal(admin), turns=1)
            sign_in(client, admin)
            opened = client.get(f"{reverse(A_SETTINGS_PAGE)}?assistant=1").content.decode()
            action = re.search(r'<form class="chat-settings-form" method="post" action="([^"]+)"',
                               opened).group(1)
            landed = client.post(action, {"time_aware": "on"}, follow=True)
        assert action == f"{reverse(A_SETTINGS_PAGE)}?assistant=1"
        body = landed.content.decode()
        assert self._OPEN in body
        assert "q0" in body

    def test_a_save_from_a_collapsed_page_redirects_exactly_as_it_did(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _install(user_principal(admin))
            sign_in(client, admin)
            collapsed = client.get(reverse(A_SETTINGS_PAGE)).content.decode()
            action = re.search(r'<form class="chat-settings-form" method="post" action="([^"]+)"',
                               collapsed).group(1)
            response = client.post(action, {"time_aware": "on"})
        assert action == reverse(A_SETTINGS_PAGE)
        assert response.headers["Location"] == reverse(A_SETTINGS_PAGE)


class TestThePanelGetsNoAttachDoorAndNoPasteListener:
    """PACKET §7, first informational note (2026-09-17). `chat/
    _composer.html` is shared with this panel, and the panel's own
    context omits `may_attach_files` -- so the attach fragment, and the
    paste listener inside it, never render here. That was true before
    the listener existed and nothing pinned it.

    WORTH A PIN BECAUSE THE PANEL IS DIFFERENT GROUND: it renders inside
    the settings shell, which is `foundation/tests/test_shell.py::
    TestTheGuardsOrderingInvariant`'s derived area and carries its own
    script-count assertion. A listener arriving here later is a decision
    somebody should make deliberately, with these tests going red first
    -- not a side effect of the composer being shared."""

    def _panel_body(self, client):
        """The OPEN panel's rendered HTML, in this module's own shape.

        There is no shared helper to reuse -- every test here inlines its
        own `with posture(...): sign_in(...); client.get(...)` (e.g.
        `TestTheCollapsedPanel`, :188-197). The module-level `_a_box`
        autouse fixture (:48-50) seeds the posture sweep for all of them;
        this adds the admin + `?assistant=1` an OPEN panel needs, since a
        COLLAPSED one renders no children at all and would make both
        assertions below pass for the wrong reason.
        """
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.get(reverse(A_SETTINGS_PAGE) + "?assistant=1")
        return response.content.decode()

    def test_the_panel_really_renders_its_composer(self, client):
        """THE POSITIVE CONTROL, and it earns its place: both assertions
        below are absences, and an empty body, a redirect, or a panel
        that rendered collapsed would satisfy every one of them while
        proving nothing. This is what fails first if the request shape
        above ever stops rendering a composer at all."""
        assert "composer-card" in self._panel_body(client)

    def test_the_panel_renders_no_paste_listener(self, client):
        assert 'addEventListener("paste"' not in self._panel_body(client)

    def test_the_panel_renders_no_attach_door_at_all(self, client):
        """The cause, pinned beside the effect: the listener is absent
        because the whole attach fragment is, not by a second gate of
        its own."""
        body = self._panel_body(client)
        assert 'for="attach-files"' not in body
        assert 'class="attach-block"' not in body
