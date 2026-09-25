"""The settings assistant is off the chat surface (owner ruling 2, spec
§7) -- not merely unlisted, but unreachable.

TWO HALVES, AND BOTH ARE ASSERTED HERE. The five list-shaped call sites
make it INVISIBLE on `/chat/`; narrowing `visible_conversation_or_404`
makes every row-addressed chat view answer 404 on it, INCLUDING SHARE,
which is what turns the spec's "a shared assistant conversation is
deferred" into a shut door rather than one nobody has walked through.

WHAT IS DELIBERATELY *NOT* NARROWED IS RECORDED HERE TOO, in
`TestWhatIsDeliberatelyLeftAlone`, so a later reader does not mistake its
200 for a hole.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (
    make_admin, make_turn, make_user, posture, seed_sweep_posture, sign_in, user_principal,
)
from agents.defaults import SETTINGS_SURFACE_SLUGS, install_default
from agents.models import Agent, Conversation, Turn
from agents.visibility import (
    chat_surface_agents, chat_surface_conversations, create_conversation, visible_agents,
    visible_conversations,
)
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN

pytestmark = pytest.mark.django_db

SLUG = "settings-helper"


@pytest.fixture(autouse=True)
def _a_box(db):
    seed_sweep_posture()


def _installed(principal):
    """The assistant's ROW plus this principal's own conversation on it --
    the world every test below needs. Installed through the ONE way a row
    appears (`install_default`, create-if-absent), never
    `Agent.objects.create`, so this world is the world an operator gets."""
    install_default("agent", SLUG, principal)
    agent = Agent.objects.get(slug=SLUG)
    conversation = create_conversation(principal, agent)
    make_turn(conversation=conversation, role=Turn.Role.USER, text="what is the posture?")
    make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="Enterprise.",
              state=Turn.State.DONE)
    return agent, conversation


class TestTheWrappers:
    def test_the_gate_itself_still_answers_yes(self):
        """SPEC §7.2: `visible_agents`/`visible_conversations` answer "may
        this principal READ this row", and the answer here is YES -- the
        panel reads both. Narrowing the platform's one gate to express a
        SURFACE preference would make `visible_agent_slugs` claim an
        administrator may not run an agent they are, at that moment,
        running."""
        with posture(POSTURE_ENTERPRISE):
            admin = user_principal(make_admin())
            agent, conversation = _installed(admin)
            assert agent in visible_agents(admin)
            assert conversation in visible_conversations(admin)

    def test_the_wrappers_exclude_it(self):
        with posture(POSTURE_ENTERPRISE):
            admin = user_principal(make_admin())
            agent, conversation = _installed(admin)
            assert agent not in chat_surface_agents(admin)
            assert conversation not in chat_surface_conversations(admin)

    def test_the_wrappers_exclude_nothing_else(self):
        """A wrapper that narrowed more than the one slug set would be a
        surface preference quietly becoming a permission."""
        from agents.chat.tests._helpers import make_agent, make_conversation

        with posture(POSTURE_ENTERPRISE):
            admin = user_principal(make_admin())
            _installed(admin)
            # `box_wide=True` (task 5, chat cluster feature B): `resident`
            # alone no longer reaches a non-owning admin under
            # `admin_sees_content=False` -- see
            # `agents/visibility.py::visible_agents`.
            other = make_agent(slug="ordinary", resident=True, box_wide=True)
            thread = make_conversation(agent=other, **_owner(admin))
            assert other in chat_surface_agents(admin)
            assert thread in chat_surface_conversations(admin)

    def test_the_wrappers_exclude_a_case_variant_slug_too(self):
        """Task 6 review, Finding 4: slug identity is case-insensitive
        everywhere else this platform tests it (`Agent`'s own `uniq_
        agent_slug_ci` constraint, `install_default`'s `slug__iexact`,
        `missing_defaults`'s own `.lower()`), while the wrappers used to
        compare `SETTINGS_SURFACE_SLUGS` exactly. A row with the
        case-variant slug `Settings-Helper` used to slip back onto the
        chat surface -- named, listed, its conversation reachable --
        even though `install_default` already reports it installed.
        No production path writes a case-variant row today
        (`agents/defaults.py`'s only row-creating path always writes the
        catalogue's lower-case slug), so this is the latent shape the
        review flagged, closed rather than left as a comment."""
        from agents.chat.tests._helpers import make_agent, make_conversation

        with posture(POSTURE_ENTERPRISE):
            admin = user_principal(make_admin())
            variant = make_agent(slug="Settings-Helper", resident=True)
            thread = make_conversation(agent=variant, **_owner(admin))
            assert variant not in chat_surface_agents(admin)
            assert thread not in chat_surface_conversations(admin)


def _owner(principal):
    from identity.access import owner_fields

    return owner_fields(principal)


class TestTheListSurfaces:
    def test_it_is_absent_from_the_chat_picker_and_from_the_offers(self, client):
        """Two consumers, fixed by two separate subtractions in
        `conversations.py`: the "Add the default X" offers list drops the
        assistant's slug from `missing_defaults`'s own result (never from
        `installed_slugs`, which would make an installed assistant look
        uninstalled and therefore offered -- the opposite of the intent),
        and the `nothing_installed` banner is computed from its own
        `chat_surface_installed_slugs`, so a box whose only agent row is
        the settings assistant reads "nothing installed", not "all
        installed agents are disabled"."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _installed(user_principal(admin))
            sign_in(client, admin)
            body = client.get(reverse("chat-index")).content.decode()
        assert SLUG not in body
        assert "Settings assistant" not in body

    def test_it_is_absent_from_the_sidebar_active_pinned_and_archived(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            principal = user_principal(admin)
            _agent, conversation = _installed(principal)
            sign_in(client, admin)
            body = client.get(reverse("chat-index")).content.decode()
            assert str(conversation.pk) not in body

            Conversation.objects.filter(pk=conversation.pk).update(pinned_at="2026-01-01T00:00Z")
            assert str(conversation.pk) not in client.get(
                reverse("chat-index")).content.decode()

            Conversation.objects.filter(pk=conversation.pk).update(
                pinned_at=None, archived_at="2026-01-01T00:00Z")
            assert str(conversation.pk) not in client.get(
                reverse("chat-index")).content.decode()

    def test_it_is_absent_from_chat_all_rows_and_from_its_preview(self, client):
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _agent, conversation = _installed(user_principal(admin))
            sign_in(client, admin)
            body = client.get(reverse("chat-all")).content.decode()
            assert str(conversation.pk) not in body
            selected = client.get(f"{reverse('chat-all')}?selected={conversation.pk}")
            assert str(conversation.pk) not in selected.content.decode()

    def test_starting_one_by_hand_is_refused(self, client):
        """`_startable_agent` matters for a reason worth naming: without
        it, a hand-crafted POST to `/chat/start/` naming this slug would
        create a conversation that then appears on no list at all.
        Closing the picker without closing the start path leaves
        orphans."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _installed(user_principal(admin))
            before = Conversation.objects.count()
            sign_in(client, admin)
            response = client.post(reverse("chat-start"), {"agent": SLUG, "text": "hello"})
        assert response.status_code == 400
        assert Conversation.objects.count() == before

    def test_no_workstream_card_can_ever_list_it(self):
        """SPEC §7.1.2 -- THE CALL SITES THAT NEED NO CHANGE, AND WHY,
        pinned directly rather than left to luck.

        `agents/chat/views/workstreams.py:373` (a stream card's
        conversation list) and `:792` (`workstream_consolidate`'s own
        row lookup) both filter on `workstream_id`, and an assistant
        conversation always carries NULL there: the ask flow calls
        `create_conversation(principal, agent)` with no `workstream`, and
        that column "IS STAMPED ONCE AND NEVER WRITTEN AGAIN"
        (`agents/visibility.py:777-784`) -- no route writes it after
        creation and the module exposes no setter. A future author who
        threaded a workstream into the ask flow would put one there with
        nothing complaining, which is what this pins.
        """
        with posture(POSTURE_ENTERPRISE):
            admin = user_principal(make_admin())
            _agent, conversation = _installed(admin)
        conversation.refresh_from_db()
        assert conversation.workstream_id is None


class TestTheRowAddressedDoor:
    """SPEC §7.1.1 / DECISION 22. Nine call sites, one function, one word
    changed -- so every row-addressed chat view answers 404 on a
    settings-surface conversation. 404 rather than 403 by
    `visible_conversation_or_404`'s own docstring rule: "a 403 on a
    row-addressed URL confirms the row exists"."""

    @pytest.mark.parametrize("name,method,body", [
        ("chat-conversation", "get", {}),
        ("chat-turn", "post", {"text": "hello"}),
        ("chat-conversation-rename", "post", {"title": "renamed"}),
        ("chat-conversation-duplicate", "post", {}),
        ("chat-conversation-pin", "post", {}),
        ("chat-conversation-unpin", "post", {}),
        ("chat-conversation-archive", "post", {}),
        ("chat-conversation-unarchive", "post", {}),
        ("chat-conversation-delete", "post", {}),
        ("chat-conversation-share", "post", {}),
    ])
    def test_every_row_addressed_chat_view_answers_404_for_its_own_owner(
            self, client, name, method, body):
        """FOR ITS OWN OWNER, which is what makes this a surface rule
        rather than a permission one: this principal may read the row --
        the panel does, on the settings page, in the same request cycle
        -- and still cannot reach it here."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _agent, conversation = _installed(user_principal(admin))
            sign_in(client, admin)
            url = reverse(name, args=[conversation.pk])
            response = getattr(client, method)(url, body)
        assert response.status_code == 404, (name, response.status_code)

    def test_sharing_it_is_the_one_that_matters_most(self, client):
        """§15.5's deferral is a SHUT DOOR because of this, not an
        unwalked one: `Share.Target.CONVERSATION` exists and the share
        action lives on the page that now 404s."""
        from agents.models import Share

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            other = make_user()
            _agent, conversation = _installed(user_principal(admin))
            sign_in(client, admin)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"user": other.pk, "level": "view"})
        assert response.status_code == 404
        assert not Share.objects.filter(target_key=str(conversation.pk)).exists()

    def test_an_ordinary_conversation_is_untouched_by_the_narrowing(self, client):
        """The negative case, without which the parametrized sweep above
        could pass on a helper that 404s everything."""
        from agents.chat.tests._helpers import make_agent, make_conversation

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            principal = user_principal(admin)
            thread = make_conversation(agent=make_agent(slug="ordinary", resident=True),
                                       **_owner(principal))
            sign_in(client, admin)
            response = client.get(reverse("chat-conversation", args=[thread.pk]))
        assert response.status_code == 200


class TestWhatIsDeliberatelyLeftAlone:
    def test_the_turn_status_fragment_is_not_narrowed_and_that_is_deliberate(self, client):
        """SPEC §7.1.1, RECORDED SO A LATER READER DOES NOT MISTAKE THIS
        200 FOR A HOLE. `visible_turn` (`agents/visibility.py:222-234`)
        filters `conversation__in=visible_conversations(principal)` and
        backs `chat-turn-status`. It asks the identical READ gate, so it
        is owner-only; and it exposes no affordance -- a status fragment
        is not a page, and the panel does not use it (it polls
        `settings-assistant-panel`, its own route). Narrowing it would
        mean a third wrapper for no reachable action.

        THE CLAIM THIS PHASE MAKES IS PRECISE: every row-addressed chat
        VIEW is closed, not that no code path anywhere can resolve the
        row by id.
        """
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _agent, conversation = _installed(user_principal(admin))
            turn = conversation.turns.order_by("index").last()
            sign_in(client, admin)
            response = client.get(reverse("chat-turn-status", args=[turn.pk]))
        assert response.status_code == 200

    def test_a_stranger_still_cannot_read_that_fragment(self, client):
        """Which is the half that makes leaving it alone safe."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            _agent, conversation = _installed(user_principal(admin))
            turn = conversation.turns.order_by("index").last()
            sign_in(client, make_user())
            response = client.get(reverse("chat-turn-status", args=[turn.pk]))
        assert response.status_code == 404


class TestTheOpenPosture:
    def test_the_exclusion_holds_on_a_box_with_no_accounts(self, client):
        """`visible_conversations` short-circuits on `sees_all_content`
        and returns `.all()` on an open box, so the exclusion has to work
        on a queryset that was never filtered by ownership at all."""
        from identity.contracts.principals import OPEN_PRINCIPAL

        with posture(POSTURE_OPEN):
            _agent, conversation = _installed(OPEN_PRINCIPAL)
            assert conversation not in chat_surface_conversations(OPEN_PRINCIPAL)
            body = client.get(reverse("chat-all")).content.decode()
            assert str(conversation.pk) not in body
            assert client.get(
                reverse("chat-conversation", args=[conversation.pk])).status_code == 404
