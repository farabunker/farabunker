"""The sidebar menu's per-conversation actions: rename, duplicate,
archive, unarchive, pin (round 20), unpin (round 20) -- UI-3b.

ONE PERMISSION RULE FOR EVERY MUTATION, delete included. Every one of
them resolves its row through `visible_conversations` and then asks
`agents.visibility.may_manage_conversation` -- so a thread SHARED with
somebody is readable and postable by them and is NOT theirs to rename,
copy, archive, pin, or delete. That was already delete's rule; every
action below inherits it rather than inventing a looser one, and the
matrix below is what says so out loud in every column the route matrix
uses. Pin/unpin (`_ACTIONS`, below) share `_ACTIONS`'s own matrix and
`TestWhoMayAct`'s own tests rather than a separate class, the identical
"same gate, same rule" reasoning `agents.visibility.may_manage_
conversation`'s own docstring gives for why they were added to that
ONE predicate rather than a new one.

Every refusal is 404, never 403: these are row-addressed URLs, and a
403 confirms the row exists.

`TestWhoMayAct` PINS ITS POSTURE PER TEST (`posture(...)`): those
questions are only answerable on a box with accounts, so `open` would
make every column identical and prove nothing. Every class below it
asserts BEHAVIOUR rather than permission -- what duplicate copies, what
rename truncates to -- and does so on an open box, where there is one
principal and it may do everything, so the assertion is about the
action and not about who asked.

NO `seed_sweep_posture()` AUTOUSE FIXTURE IN THIS MODULE, deliberately,
and for the same reason `test_visibility.py` states: the behaviour
classes drive an unauthenticated `client` against the AMBIENT posture,
and opting the whole module into the sweep would leave them signed out
of a non-open box for a reason that has nothing to do with what they
assert. The one class where the posture IS the subject pins its own.
"""
from __future__ import annotations

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from agents.chat.tests._helpers import (
    make_admin, make_agent, make_turn, make_user, posture, reset_settings,
    sign_in, user_principal,
)
from agents.models import Conversation, Share, ToolInvocation, Turn
from agents.visibility import create_conversation
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    yield
    reset_settings()


def _conversation(owner, **overrides):
    conversation = create_conversation(user_principal(owner), make_agent())
    if overrides:
        for field, value in overrides.items():
            setattr(conversation, field, value)
        conversation.save()
    return conversation


# The four routes and a body each view really reads -- a body naming a
# field the view ignores would exercise its "you sent nothing" branch
# and this matrix would pass while testing the wrong thing (the route
# matrix's own driver rule).
_ACTIONS = [
    ("chat-conversation-rename", {"title": "Renamed"}),
    ("chat-conversation-duplicate", {}),
    ("chat-conversation-archive", {}),
    ("chat-conversation-unarchive", {}),
    # ROUND 20: the SAME matrix, not a second one -- `TestWhoMayAct`'s
    # own five tests below now cover pin/unpin for free.
    ("chat-conversation-pin", {}),
    ("chat-conversation-unpin", {}),
]
_ACTION_IDS = [name for name, _ in _ACTIONS]


class TestWhoMayAct:
    """The four columns `identity/tests/test_route_matrix.py` sweeps,
    asserted here against the real rows rather than only the status
    code."""

    @pytest.mark.parametrize("name,body", _ACTIONS, ids=_ACTION_IDS)
    def test_the_owner_is_admitted(self, client, name, body):
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse(name, args=[conversation.pk]), body)
        assert response.status_code == 302

    @pytest.mark.parametrize("name,body", _ACTIONS, ids=_ACTION_IDS)
    def test_a_foreign_member_gets_404(self, client, name, body):
        owner, stranger = make_user(), make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, stranger)
            response = client.post(reverse(name, args=[conversation.pk]), body)
        assert response.status_code == 404

    @pytest.mark.parametrize("name,body", _ACTIONS, ids=_ACTION_IDS)
    def test_an_admin_without_the_content_setting_gets_404(self, client, name, body):
        owner, admin = make_user(), make_admin()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            response = client.post(reverse(name, args=[conversation.pk]), body)
        assert response.status_code == 404

    @pytest.mark.parametrize("name,body", _ACTIONS, ids=_ACTION_IDS)
    def test_an_admin_with_the_content_setting_is_admitted(self, client, name, body):
        owner, admin = make_user(), make_admin()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            sign_in(client, admin)
            response = client.post(reverse(name, args=[conversation.pk]), body)
        assert response.status_code == 302

    @pytest.mark.parametrize("name,body", _ACTIONS, ids=_ACTION_IDS)
    def test_a_recipient_of_a_share_may_read_it_but_not_act_on_it(
        self, client, name, body,
    ):
        """THE FINDING THIS TASK HAD TO CONFIRM RATHER THAN ASSUME. A
        `use`-level share is the WIDEST one this platform grants, and it
        still does not make the thread the recipient's to change: it
        lets them read the thread and post into it. `chat-conversation-
        delete` already answered 404 here; all four new actions do too,
        because they ask the same predicate."""
        owner, guest = make_user(), make_user()
        conversation = _conversation(owner)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest,
                             level=Share.Level.USE)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, guest)
            # The thread itself IS readable -- without this the 404
            # below would prove nothing about the ACTION.
            assert client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).status_code == 200
            response = client.post(reverse(name, args=[conversation.pk]), body)
        assert response.status_code == 404


class TestMethodAndUnknownRows:
    @pytest.mark.parametrize("name,body", _ACTIONS, ids=_ACTION_IDS)
    def test_a_get_is_405(self, client, name, body):
        conversation = _conversation(make_user())
        assert client.get(
            reverse(name, args=[conversation.pk])).status_code == 405

    @pytest.mark.parametrize("name,body", _ACTIONS, ids=_ACTION_IDS)
    def test_an_unknown_id_is_404_not_a_500(self, client, name, body):
        assert client.post(
            reverse(name, args=[uuid.uuid4()]), body).status_code == 404


class TestRenaming:
    def test_it_writes_the_new_title(self, client):
        conversation = _conversation(make_user(), title="before")
        client.post(reverse("chat-conversation-rename", args=[conversation.pk]),
                    {"title": "after"})
        conversation.refresh_from_db()
        assert conversation.title == "after"

    def test_a_title_that_exactly_fills_the_column_is_written_not_rejected(self, client):
        """The off-by-one `fit_title` exists for: `truncate_title` cuts
        to `limit` and THEN appends "…", so a caller truncating TO the
        column width would hand the database 256 characters and get a
        `DataError` on save -- a 500 on a POST an operator can reach by
        pasting a long line into the rename box."""
        from agents.chat.service import TITLE_COLUMN_MAX

        conversation = _conversation(make_user())
        response = client.post(
            reverse("chat-conversation-rename", args=[conversation.pk]),
            {"title": "x" * (TITLE_COLUMN_MAX + 50)})
        assert response.status_code == 302
        conversation.refresh_from_db()
        assert len(conversation.title) == TITLE_COLUMN_MAX

    def test_a_too_long_title_is_re_truncated_by_the_same_rules(self, client):
        """`TITLE_COLUMN_MAX`, not `TITLE_MAX`: an operator who types a
        long title meant to, and the only ceiling that applies is the
        column's. Cut on a word boundary with a trailing "…", exactly
        as an auto-derived title is -- `truncate_title` is the one
        function, called twice."""
        from agents.chat.service import TITLE_COLUMN_MAX

        conversation = _conversation(make_user())
        client.post(reverse("chat-conversation-rename", args=[conversation.pk]),
                    {"title": ("word " * 200).strip()})
        conversation.refresh_from_db()
        assert len(conversation.title) <= TITLE_COLUMN_MAX
        assert conversation.title.endswith("…")
        assert not conversation.title.endswith("wor…")   # a word boundary

    def test_a_blank_title_flashes_and_redirects_and_changes_nothing(self, client):
        """UPDATED, ITEM 6 (Coherence Wave D): the refusal used to be a
        raw 400 (a bare plain-text page); it is flash-and-redirect now,
        the same house shape the success path on this same view already
        redirects with."""
        conversation = _conversation(make_user(), title="keep me")
        response = client.post(
            reverse("chat-conversation-rename", args=[conversation.pk]),
            {"title": "   "}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain
        assert "A conversation needs a title" in response.content.decode()
        conversation.refresh_from_db()
        assert conversation.title == "keep me"

    def test_it_returns_to_the_page_it_was_posted_from(self, client):
        conversation = _conversation(make_user())
        thread_url = reverse("chat-conversation", args=[conversation.pk])
        response = client.post(
            reverse("chat-conversation-rename", args=[conversation.pk]),
            {"title": "x", "next": thread_url})
        assert response["Location"] == thread_url

    def test_an_off_site_next_is_ignored(self, client):
        """`agents.chat.service.validated_next_url` is `url_has_allowed_
        host_and_scheme`, the same guard `tools/vision/views.py` puts on
        its own delete forms -- a `next` is caller-supplied, and an
        unchecked one is an open redirect off this box."""
        conversation = _conversation(make_user())
        response = client.post(
            reverse("chat-conversation-rename", args=[conversation.pk]),
            {"title": "x", "next": "https://example.invalid/steal"})
        assert response["Location"] == reverse(
            "chat-conversation", args=[conversation.pk])


class TestArchiving:
    def test_archiving_stamps_the_column_and_unarchiving_clears_it(self, client):
        conversation = _conversation(make_user())
        client.post(reverse("chat-conversation-archive", args=[conversation.pk]))
        conversation.refresh_from_db()
        assert conversation.archived_at is not None

        client.post(reverse("chat-conversation-unarchive", args=[conversation.pk]))
        conversation.refresh_from_db()
        assert conversation.archived_at is None

    def test_archiving_destroys_no_turns_and_leaves_the_thread_readable(self, client):
        """The whole difference between archive and delete, stated as a
        test rather than as a comment."""
        conversation = _conversation(make_user())
        make_turn(conversation=conversation, role=Turn.Role.USER, text="still here")
        client.post(reverse("chat-conversation-archive", args=[conversation.pk]))
        response = client.get(reverse("chat-conversation", args=[conversation.pk]))
        assert response.status_code == 200
        assert "still here" in response.content.decode()
        assert Turn.objects.filter(conversation=conversation).count() == 1

    def test_it_returns_to_the_page_it_was_posted_from(self, client):
        conversation = _conversation(make_user())
        response = client.post(
            reverse("chat-conversation-archive", args=[conversation.pk]),
            {"next": reverse("chat-index")})
        assert response["Location"] == reverse("chat-index")


class TestPinning:
    """ROUND 20 (owner: "can we add the ability t[o] pin chats"). The
    SAME shape `TestArchiving`, above, already pins for its own two
    routes -- toggle round-trip, the `next`-redirect landing behaviour
    -- because `_set_pinned` is a copy of `_set_archived`'s own body."""

    def test_pinning_stamps_the_column_and_unpinning_clears_it(self, client):
        conversation = _conversation(make_user())
        client.post(reverse("chat-conversation-pin", args=[conversation.pk]))
        conversation.refresh_from_db()
        assert conversation.pinned_at is not None

        client.post(reverse("chat-conversation-unpin", args=[conversation.pk]))
        conversation.refresh_from_db()
        assert conversation.pinned_at is None

    def test_it_returns_to_the_page_it_was_posted_from(self, client):
        conversation = _conversation(make_user())
        response = client.post(
            reverse("chat-conversation-pin", args=[conversation.pk]),
            {"next": reverse("chat-index")})
        assert response["Location"] == reverse("chat-index")

    def test_pinning_destroys_no_turns_and_leaves_the_thread_readable(self, client):
        """The identical claim `TestArchiving`'s own sibling test makes
        -- pinning is a bookmark, never a mutation of the thread's own
        content."""
        conversation = _conversation(make_user())
        make_turn(conversation=conversation, role=Turn.Role.USER, text="still here")
        client.post(reverse("chat-conversation-pin", args=[conversation.pk]))
        response = client.get(reverse("chat-conversation", args=[conversation.pk]))
        assert response.status_code == 200
        assert "still here" in response.content.decode()
        assert Turn.objects.filter(conversation=conversation).count() == 1

    def test_archiving_a_pinned_conversation_leaves_the_pin_column_standing(self, client):
        """ARCHIVE WINS ON THE SIDEBAR (`agents/chat/sidebar.py`'s own
        filter), NOT ON THE ROW: archiving never clears `pinned_at` --
        unarchiving restores the pin exactly where archiving found it,
        with no second click needed to re-pin."""
        conversation = _conversation(make_user())
        client.post(reverse("chat-conversation-pin", args=[conversation.pk]))
        client.post(reverse("chat-conversation-archive", args=[conversation.pk]))
        conversation.refresh_from_db()
        assert conversation.pinned_at is not None
        assert conversation.archived_at is not None


class TestDuplicating:
    def test_it_copies_the_finished_turns_and_lands_on_the_copy(self, client):
        """`artifacts` and `data` are asserted here alongside role and
        text because everything duplicate DROPS is pinned elsewhere and
        these two are what it KEEPS: `artifacts` is what makes a copied
        image card render its picture at all, and `data` carries the
        citations and the generation payload the card reads."""
        original = _conversation(make_user(), title="the original")
        make_turn(conversation=original, role=Turn.Role.USER, text="hello")
        make_turn(conversation=original, role=Turn.Role.ASSISTANT, text="hi",
                  state=Turn.State.DONE, artifacts=["output:12"],
                  data={"citations": [{"title": "a.pdf"}]})

        response = client.post(
            reverse("chat-conversation-duplicate", args=[original.pk]))

        copy = Conversation.objects.exclude(pk=original.pk).get()
        assert response.status_code == 302
        assert response["Location"] == reverse("chat-conversation", args=[copy.pk])
        assert [(t.role, t.text) for t in copy.turns.all()] == [
            (Turn.Role.USER, "hello"), (Turn.Role.ASSISTANT, "hi")]
        answer = copy.turns.get(role=Turn.Role.ASSISTANT)
        assert answer.artifacts == ["output:12"]
        assert answer.data == {"citations": [{"title": "a.pdf"}]}

    def test_a_mid_run_turn_is_left_behind_and_the_indexes_close_up(self, client):
        """A QUEUED or RUNNING turn belongs to the ORIGINAL's `agent.turn`
        job -- that job writes its answer back to the row it was handed,
        which is the original's -- so a copy of the placeholder could
        only ever sit in the new thread as a turn that never finishes.
        The surviving turns are renumbered from 0 rather than carrying a
        gap across."""
        original = _conversation(make_user())
        make_turn(conversation=original, role=Turn.Role.USER, text="asked",
                  state=Turn.State.DONE)
        make_turn(conversation=original, role=Turn.Role.ASSISTANT, text="",
                  state=Turn.State.RUNNING)
        make_turn(conversation=original, role=Turn.Role.USER, text="asked again",
                  state=Turn.State.DONE)

        client.post(reverse("chat-conversation-duplicate", args=[original.pk]))

        copy = Conversation.objects.exclude(pk=original.pk).get()
        assert [(t.index, t.text) for t in copy.turns.all()] == [
            (0, "asked"), (1, "asked again")]

    def test_a_failed_or_cancelled_turn_is_terminal_and_does_come_across(self, client):
        """"Terminal" is not "successful". A failed turn is part of what
        was said in this thread, and dropping it would make the copy a
        tidied-up version of a conversation that did not go that way."""
        original = _conversation(make_user())
        make_turn(conversation=original, role=Turn.Role.ASSISTANT, text="",
                  state=Turn.State.FAILED, error="the engine timed out")
        make_turn(conversation=original, role=Turn.Role.ASSISTANT, text="",
                  state=Turn.State.CANCELLED)

        client.post(reverse("chat-conversation-duplicate", args=[original.pk]))

        copy = Conversation.objects.exclude(pk=original.pk).get()
        assert [t.state for t in copy.turns.all()] == [
            Turn.State.FAILED, Turn.State.CANCELLED]
        assert copy.turns.first().error == "the engine timed out"

    def test_no_tool_invocation_row_is_copied_or_re_pointed(self, client):
        """A `ToolInvocation` is an AUDIT RECORD of a call that really
        happened, in the original thread, by a named principal. The copy
        keeps the tool card's visible content and carries NO audit link:
        pointing at the original's row would file one audit record under
        two conversations, and writing a second would invent a call that
        never ran."""
        original = _conversation(make_user())
        invocation = ToolInvocation.objects.create(
            principal_kind="user", principal_key="1", tool_key="rag.search",
            outcome=ToolInvocation.Outcome.OK, text="two results",
            finished_at=timezone.now(),
        )
        make_turn(conversation=original, role=Turn.Role.TOOL, text="two results",
                  state=Turn.State.DONE, invocation=invocation,
                  tool_call={"tool": "rag.search", "args": {"query": "x"},
                             "agent": "general", "id": "", "discarded": []})

        client.post(reverse("chat-conversation-duplicate", args=[original.pk]))

        copy = Conversation.objects.exclude(pk=original.pk).get()
        copied = copy.turns.get()
        assert ToolInvocation.objects.count() == 1
        assert copied.invocation_id is None
        assert copied.queue_job_id is None
        # The card's own content survives -- only the audit LINK does not.
        assert copied.tool_call["tool"] == "rag.search"
        assert copied.text == "two results"

    def test_the_copy_is_titled_copy_of_the_original(self, client):
        original = _conversation(make_user(), title="the original")
        client.post(reverse("chat-conversation-duplicate", args=[original.pk]))
        copy = Conversation.objects.exclude(pk=original.pk).get()
        assert copy.title == "Copy of the original"

    def test_a_full_width_title_still_produces_a_legal_and_useful_copy(self, client):
        """The prefix is never what gets cut. Truncating the whole
        `"Copy of <255 unbroken characters>"` as one string finds its
        only word boundary right after `"Copy of"` and yields
        `"Copy of…"` -- legal, and useless. `service.fit_title` spends
        the budget on the part that carries the meaning."""
        from agents.chat.service import TITLE_COLUMN_MAX

        original = _conversation(make_user(), title="x" * TITLE_COLUMN_MAX)
        client.post(reverse("chat-conversation-duplicate", args=[original.pk]))
        copy = Conversation.objects.exclude(pk=original.pk).get()
        assert copy.title.startswith("Copy of xxx")
        assert len(copy.title) <= TITLE_COLUMN_MAX

    def test_an_untitled_thread_copies_to_a_bare_copy_of(self, client):
        original = _conversation(make_user())
        client.post(reverse("chat-conversation-duplicate", args=[original.pk]))
        copy = Conversation.objects.exclude(pk=original.pk).get()
        assert copy.title == "Copy of"

    def test_the_copy_belongs_to_whoever_pressed_the_button(self, client):
        """`owner_fields(principal)`, exactly as `create_conversation`
        stamps: an administrator who duplicates somebody else's thread
        gets their OWN thread, not a second row filed under a person who
        did not ask for one."""
        owner, admin = make_user(), make_admin()
        original = _conversation(owner)
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            sign_in(client, admin)
            client.post(reverse("chat-conversation-duplicate", args=[original.pk]))
        copy = Conversation.objects.exclude(pk=original.pk).get()
        assert (copy.owner_kind, copy.owner_key) == ("user", str(admin.pk))

    def test_the_original_is_untouched(self, client):
        original = _conversation(make_user(), title="the original")
        make_turn(conversation=original, role=Turn.Role.USER, text="hello")
        client.post(reverse("chat-conversation-duplicate", args=[original.pk]))
        original.refresh_from_db()
        assert original.title == "the original"
        assert original.turns.count() == 1
