"""`POST /chat/c/<uuid>/share/` -- add a share, and revoke one.

ONE ROUTE, NOT TWO. Unsharing keys on a `share_id` in the same body, so
the thread page has one form action rather than two URLs that have to
agree about who may reach them.

WHAT A SHARED CONVERSATION EXPOSES is stated on the form, because it is
the one place a person can leak library content without meaning to: a
thread's turns record citation TEXT, so sharing the thread shares the
quoted passages -- but not the documents. A citation renders its link and
the link 404s for a recipient who may not read that document.
"""
from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils.html import escape

from agents.chat.tests._helpers import (
    make_admin, make_agent, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in, user_principal,
)
from agents.models import Share
from agents.visibility import create_conversation
from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.models import AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


def _conversation(owner):
    return create_conversation(user_principal(owner), make_agent())


class TestSharing:
    def test_the_owner_shares_at_a_level_and_it_is_audited(self, client):
        owner, guest = make_user(), make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "share", "subject": f"user:{guest.pk}", "level": "use"})
        assert response.status_code == 302
        assert Share.objects.get(user=guest).level == Share.Level.USE
        assert AuditEvent.objects.filter(action=actions.SHARE_ADDED).count() == 1

    def test_a_group_share_works_the_same_way(self, client):
        owner = make_user()
        group = make_group()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            client.post(reverse("chat-conversation-share", args=[conversation.pk]),
                        {"action": "share", "subject": f"group:{group.pk}", "level": "view"})
        assert Share.objects.filter(group=group).exists()

    def test_revoking_removes_the_row_and_is_audited(self, client):
        owner, guest = make_user(), make_user()
        conversation = _conversation(owner)
        row = Share.objects.create(target_type=Share.Target.CONVERSATION,
                                   target_key=str(conversation.pk), user=guest,
                                   level=Share.Level.USE)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            client.post(reverse("chat-conversation-share", args=[conversation.pk]),
                        {"action": "revoke", "share": row.pk})
        assert Share.objects.count() == 0
        event = AuditEvent.objects.get(action=actions.SHARE_REVOKED)
        # THE ROW IS GONE by the time this write happens (review
        # finding, 2026-08-31) -- without carrying its subject/level
        # forward, this audit row could never answer "whose access was
        # removed".
        assert event.detail["subject"] == "user"
        assert event.detail["subject_key"] == guest.pk
        assert event.detail["level"] == Share.Level.USE

    def test_a_recipient_may_not_re_share_and_gets_404(self, client):
        """404, not 403: this is a row-addressed URL, and a 403 would
        confirm which conversations exist."""
        owner, guest, third = make_user(), make_user(), make_user()
        conversation = _conversation(owner)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, guest)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "share", "subject": f"user:{third.pk}", "level": "view"})
        assert response.status_code == 404
        assert Share.objects.count() == 1

    def test_a_stranger_gets_404_on_a_conversation_they_cannot_see(self, client):
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "share", "subject": f"user:{owner.pk}", "level": "view"})
        assert response.status_code == 404

    def test_an_unparseable_share_id_answers_404_not_500(self, client):
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "revoke", "share": "abc"})
        assert response.status_code == 404
        assert b"Traceback" not in response.content

    def test_a_non_decimal_digit_share_id_answers_404_not_500(self, client):
        """"²".isdigit() is True but int("²") raises `ValueError` (review
        finding, 2026-08-31): `revoke_share`'s old `str.isdigit()` guard
        let exactly this class of string reach `int(...)` and 500 --
        `isdecimal()` is the set `int()` can actually parse."""
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "revoke", "share": "²"})
        assert response.status_code == 404
        assert b"Traceback" not in response.content

    def test_a_non_decimal_digit_subject_answers_404_not_500(self, client):
        """The same bug, the other parse: `conversation_share`'s subject
        split used `str.isdigit()` too, reachable by ANY principal who
        can merely SEE the thread -- before the owner check even runs."""
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "share", "subject": "user:²", "level": "view"})
        assert response.status_code == 404
        assert b"Traceback" not in response.content

    def test_a_share_id_from_another_conversation_answers_404(self, client):
        owner = make_user()
        # A SINGLE shared agent for both conversations -- see
        # `test_revoke_answers_none_for_a_share_on_another_conversation`
        # in `agents/chat/tests/test_visibility.py` for why: `make_agent()`
        # defaults to one fixed slug, and its own docstring says to pass
        # one explicitly for a second agent. This test needs two
        # CONVERSATIONS, not two agents.
        agent = make_agent()
        mine = create_conversation(user_principal(owner), agent)
        theirs = create_conversation(user_principal(make_user()), agent)
        elsewhere = Share.objects.create(target_type=Share.Target.CONVERSATION,
                                         target_key=str(theirs.pk), user=make_user())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("chat-conversation-share", args=[mine.pk]),
                                   {"action": "revoke", "share": elsewhere.pk})
        assert response.status_code == 404
        assert Share.objects.filter(pk=elsewhere.pk).exists()

    def test_an_unknown_action_flashes_and_redirects_to_the_thread(self, client):
        """M4 (Wave C review): a raw 400 until this pass. The share panel
        is a plain form on a conversation page inside the shell, and the
        conversation is already resolved (404 for a caller who may not
        see it) by the time the action is checked -- so a stale action
        redirects to the thread the caller was on, with the sentence the
        400 body carried."""
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "transfer"}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse(
            "chat-conversation", args=[conversation.pk])
        assert escape("'transfer' is not a recognised action.") in response.content.decode()
        assert Share.objects.count() == 0

    def test_a_share_post_shows_a_confirmation_on_the_page_it_redirects_to(self, client):
        """`django.contrib.messages` carries "Shared." across the
        redirect (review finding, 2026-08-31 -- same
        `chat-conversation-delete` shape `test_delete.py::
        test_the_index_shows_a_deleted_notice` already pins): without a
        messages block on `conversation.html`, this flash had nowhere to
        render on the page the caller actually lands on."""
        owner, guest = make_user(), make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "share", "subject": f"user:{guest.pk}", "level": "view"},
                follow=True)
        assert response.status_code == 200
        assert "Shared." in response.content.decode()


class TestThePanel:
    def test_the_owner_sees_the_share_list_and_a_recipient_does_not(self, client):
        """A share list names who else is reading somebody's thread --
        content about content -- so it renders to the owner and to a
        principal that `sees_all_content`, and to nobody else.

        ROUND 9: the panel is a POPUP now (`<details class="chat-menu">`
        behind a `Share…` trigger, `chat/conversation.html`'s own
        `.thread-actions`) rather than an in-flow section -- the owner's
        body carries that wrapper too, not merely the panel's own
        content, and a recipient without `sees_all_content` gets neither
        the trigger nor the panel (render-vs-gate: no dead button)."""
        owner, guest = make_user(), make_user()
        conversation = _conversation(owner)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        url = reverse("chat-conversation", args=[conversation.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            owner_body = client.get(url).content.decode()
            assert "Shared with" in owner_body
            assert "<summary>Share…</summary>" in owner_body
            assert '<details class="chat-menu">' in owner_body
            other = client.__class__()
            sign_in(other, guest)
            guest_body = other.get(url).content.decode()
            assert "Shared with" not in guest_body
            assert "Share…" not in guest_body

    def test_an_admin_reaches_it_only_with_the_content_setting_on(self, client):
        owner = make_user()
        conversation = _conversation(owner)
        url = reverse("chat-conversation", args=[conversation.pk])
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            sign_in(client, make_admin())
            assert b"Shared with" in client.get(url).content

    def test_the_form_states_what_a_shared_thread_exposes(self, client):
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(reverse("chat-conversation", args=[conversation.pk])).content
        assert b"quoted" in body

    def test_an_open_box_renders_no_share_panel(self, client, monkeypatch):
        """`share_subjects` answers empty in open posture and the panel
        is gated on the posture, so the household box's thread page is
        byte-identical to today's.

        PINNED to `POSTURE_OPEN` explicitly (review finding, 2026-08-31
        -- same repair shape as eda5464): this module's own `_settings`
        fixture calls `seed_sweep_posture`, so under
        `FARABUNKER_TEST_POSTURE=enterprise` this test used to run
        against an anonymous caller's 302 on an ENTERPRISE box with a
        0-byte redirect body -- the assertion passed without an open box
        ever being exercised.

        ALSO PINS THE ZERO-QUERY RULE (`may_see_shares` gates on
        `accounts_on()` before `shares_for` ever runs): `shares_for` is
        monkeypatched to raise if reached at all, so this proves an open
        box's thread GET never issues a `Share` query, rather than
        merely asserting the panel's absence from the rendered body.
        """
        def _boom(*args, **kwargs):
            raise AssertionError("shares_for must not run on an open box")

        monkeypatch.setattr("agents.chat.views.thread.shares_for", _boom)
        conversation = create_conversation(OPEN_PRINCIPAL, make_agent())
        with posture(POSTURE_OPEN):
            response = client.get(reverse("chat-conversation", args=[conversation.pk]))
        assert response.status_code == 200
        assert b"Shared with" not in response.content


class TestTheComposeFormFollowsTheShareLevel:
    def test_a_view_recipient_is_not_shown_the_form_a_use_recipient_is(self, client):
        """The SAME predicate `chat-turn` enforces (`may_post_to`), never
        a second truth -- a page that renders a control whose POST answers
        403 is a page that lies to the person reading it."""
        owner, viewer, poster = make_user(), make_user(), make_user()
        conversation = _conversation(owner)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=viewer,
                             level=Share.Level.VIEW)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=poster,
                             level=Share.Level.USE)
        url = reverse("chat-conversation", args=[conversation.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            assert b"shared with you to read" in client.get(url).content
            other = client.__class__()
            sign_in(other, poster)
            assert b"shared with you to read" not in other.get(url).content
