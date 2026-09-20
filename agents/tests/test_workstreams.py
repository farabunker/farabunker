"""`Workstream` and its wall table — the rows, their constraints, and the
one relationship that must refuse rather than cascade."""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.urls import reverse

from agents.models import Conversation, ConversationTaint, Share, Workstream, WorkstreamScopeEntitlement
from agents.tests._helpers import _workstream, make_agent, make_conversation  # noqa: F401 -- _workstream used from Task 3 onward
from agents.visibility import (
    create_conversation, create_workstream, delete_workstream, duplicate_conversation,
    may_manage_workstream, name_for_viewer, rename_conversation, rename_workstream,
    set_conversation_archived, set_workstream_archived, set_workstream_include_universal,
    set_workstream_instructions, set_workstream_scope, visible_workstreams,
)
from agents.workstreams import tags_for_conversations
from identity import audit
from identity.access import owner_fields
from identity.contracts import actions
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.testing import grant, make_admin, make_entitlement, make_user, posture, sign_in, user_principal

pytestmark = pytest.mark.django_db


@pytest.fixture
def agent():
    """A plain `Agent` row -- this module's own fixture, not a shared
    one: no `agent` fixture exists anywhere else in this app's tests
    (every sibling module calls `make_agent()` directly), and the two
    tests below that take one as an argument need it built exactly the
    same way."""
    return make_agent()


def test_a_stream_is_owned_by_owner_columns_not_a_user_fk():
    """Owner COLUMNS, matching every other owned row in this codebase —
    the open posture's single principal is not a `User` row, and
    `owned_rows_q` is the one predicate that reads these two columns in
    every posture."""
    user = make_user()
    stream = Workstream.objects.create(name="Q3 planning",
                                       **owner_fields(user_principal(user)))
    assert stream.owner_kind == "user"
    assert stream.owner_key == str(user.pk)
    assert not hasattr(stream, "owner_id")


def test_two_people_may_each_have_a_stream_called_taxes():
    """CASE-INSENSITIVELY UNIQUE PER OWNER, not globally: a global unique
    would make the second person's stream unnameable for a reason no page
    could explain."""
    one, two = make_user(), make_user()
    Workstream.objects.create(name="Taxes", **owner_fields(user_principal(one)))
    Workstream.objects.create(name="Taxes", **owner_fields(user_principal(two)))
    assert Workstream.objects.filter(name="Taxes").count() == 2


def test_one_person_may_not_have_two_streams_called_taxes_in_any_casing():
    user = make_user()
    Workstream.objects.create(name="Taxes", **owner_fields(user_principal(user)))
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Workstream.objects.create(name="taxes", **owner_fields(user_principal(user)))


def test_the_upload_default_is_blank_not_null_when_it_is_unset():
    """`"" = ask every time` (owner decision 5). Not `null=True`: a
    nullable choice column would give one column two spellings of
    empty."""
    stream = Workstream.objects.create(name="One")
    assert stream.default_upload_placement == ""
    stream.default_upload_placement = Workstream.UploadPlacement.CONTAINED
    stream.save(update_fields=["default_upload_placement", "updated_at"])
    stream.refresh_from_db()
    assert stream.default_upload_placement == "contained"


def test_archived_at_is_a_timestamp_not_a_boolean():
    """Mirroring `Conversation.archived_at`: "when" is strictly more
    information than "whether" and costs the same column."""
    stream = Workstream.objects.create(name="One")
    assert stream.archived_at is None


def test_a_wall_row_is_unique_per_stream_and_entitlement():
    stream = Workstream.objects.create(name="One")
    ent = make_entitlement()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)


def test_the_walls_reverse_accessor_is_scope_entitlements_not_entitlement_labels():
    """AUTHOR DECISION 4 (spec §5.2), asserted rather than assumed. The
    four existing label tables all name theirs `entitlement_labels`
    precisely so `agents.visibility.label_permitted_q` can be one
    function for two models — and a WALL is not a label. Sharing the
    accessor would let a future `label_permitted_q` call compile against
    a stream and answer the wrong question SILENTLY."""
    stream = Workstream.objects.create(name="One")
    assert hasattr(stream, "scope_entitlements")
    assert not hasattr(stream, "entitlement_labels")


def test_a_conversation_is_loose_by_default_and_may_be_born_in_a_stream(agent):
    stream = Workstream.objects.create(name="One")
    loose = Conversation.objects.create(agent=agent)
    inside = Conversation.objects.create(agent=agent, workstream=stream)
    assert loose.workstream_id is None
    assert inside.workstream_id == stream.pk


def test_deleting_a_stream_that_still_holds_a_conversation_is_refused(agent):
    """PROTECT, not CASCADE. `CASCADE` would make one button delete
    somebody's conversations, which is the most destructive gesture on
    this surface hiding behind the least alarming control (author
    decision 15)."""
    stream = Workstream.objects.create(name="One")
    Conversation.objects.create(agent=agent, workstream=stream)
    with pytest.raises(ProtectedError):
        stream.delete()


def test_workstream_is_a_share_target_whose_key_parses_as_an_integer():
    stream = Workstream.objects.create(name="One")
    user = make_user()
    row = Share.objects.create(target_type=Share.Target.WORKSTREAM,
                               target_key=str(stream.pk), user=user,
                               level=Share.Level.USE)
    assert row.pk is not None


def test_a_workstream_share_with_an_unparseable_key_is_refused_at_save():
    """`Share.save()` already refuses a key its parser rejects; the new
    target inherits that rather than needing its own guard."""
    user = make_user()
    with pytest.raises(ValueError):
        Share.objects.create(target_type=Share.Target.WORKSTREAM,
                             target_key="not-an-int", user=user)


def test_an_open_box_sees_every_stream_and_runs_no_permission_query():
    """THE OPEN BRANCH IS FIRST, as in every function in this module —
    and the claim is asserted, not stated: ZERO queries against
    `EntitlementGrant`, named by table, so the pin cannot pass by
    counting a total that happened not to move."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _workstream()
    _workstream()
    with posture("open"):
        with CaptureQueriesContext(connection) as captured:
            rows = list(visible_workstreams(OPEN_PRINCIPAL))
    assert len(rows) == 2
    assert not [q for q in captured.captured_queries
                if "identity_entitlementgrant" in q["sql"]]


def test_a_member_sees_their_own_streams_and_the_ones_shared_to_them():
    owner, other = make_user(), make_user()
    mine = _workstream(user_principal(owner), name="Mine")
    theirs = _workstream(user_principal(other), name="Theirs")
    shared = _workstream(user_principal(other), name="Shared")
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(shared.pk),
                         user=owner, level=Share.Level.USE)
    with posture("enterprise"):
        keys = set(visible_workstreams(user_principal(owner)).values_list("pk", flat=True))
    assert keys == {mine.pk, shared.pk}
    assert theirs.pk not in keys


def test_a_dormant_share_still_lists():
    """THE DORMANCY CHECK IS NOT IN `visible_workstreams` (spec §6.4) —
    a dormant share still LISTS, and hiding it would leave a recipient
    with a stream that vanished and no sentence explaining why. In WS-1
    there are no tags, so this asserts the SHAPE: the share clause is
    ownership-and-share only, with no tag join."""
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert visible_workstreams(user_principal(reader)).filter(pk=stream.pk).exists()


def test_creating_a_stream_stamps_the_actor_and_audits_it():
    user = make_user()
    with posture("enterprise"):
        stream = create_workstream(user_principal(user), "Q3 planning")
    assert stream.owner_key == str(user.pk)
    rows = audit.for_target("workstream", stream.pk)
    assert [r.action for r in rows] == [actions.WORKSTREAM_CREATED]


def test_creating_a_stream_with_a_name_the_actor_already_used_answers_none():
    """AUTHOR DECISION 8: a real collision an operator can cause by
    typing a name twice, on a never-500 surface. `None` plus a named
    message on the page, the shape `share_conversation` already uses."""
    user = make_user()
    with posture("enterprise"):
        assert create_workstream(user_principal(user), "Taxes") is not None
        assert create_workstream(user_principal(user), "taxes") is None


def test_a_recipient_may_not_manage_a_stream_shared_to_them():
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert may_manage_workstream(user_principal(owner), stream) is True
        assert may_manage_workstream(user_principal(reader), stream) is False
        assert rename_workstream(user_principal(reader), stream, "Renamed") is False
        assert set_workstream_instructions(user_principal(reader), stream, "hi") is False
        assert set_workstream_archived(user_principal(reader), stream, archived=True) is False


def test_a_fresh_stream_defaults_include_universal_true():
    """DEFAULT TRUE (owner, verbatim: "default it on") -- pinned at the
    ROW level, the field this round's whole behaviour-equality pin
    (`tools/rag/tests/test_workstream_corpus.py`) ultimately rests on."""
    stream = _workstream(OPEN_PRINCIPAL)
    assert stream.include_universal is True


def test_set_workstream_include_universal_writes_and_audits_it():
    """AUDITED, like `set_workstream_upload_default`/`set_workstream_
    instructions` and unlike `set_workstream_description` -- this
    column is an access consequence (what the stream's own corpus
    admits at retrieval), not a label with none."""
    user = make_user()
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        assert set_workstream_include_universal(
            user_principal(user), stream, include_universal=False) is True
    stream.refresh_from_db()
    assert stream.include_universal is False
    rows = audit.for_target("workstream", stream.pk)
    assert [r.action for r in rows] == [actions.WORKSTREAM_INCLUDE_UNIVERSAL_SET]


def test_a_recipient_may_not_set_include_universal_on_a_stream_shared_to_them():
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert set_workstream_include_universal(
            user_principal(reader), stream, include_universal=False) is False
    stream.refresh_from_db()
    assert stream.include_universal is True


def test_the_wall_accepts_only_entitlements_the_setter_holds():
    """Spec §6.1: not because holding it would grant anything (it would
    not; the outer intersection sees to that) but because a wall naming
    an entitlement its owner does not hold is a wall that narrows to the
    empty set and reads as a bug."""
    user = make_user()
    held, unheld = make_entitlement(), make_entitlement()
    grant(held, user=user)
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        result = set_workstream_scope(user_principal(user), stream, [held.pk, unheld.pk])
    assert result is None
    assert stream.scope_entitlements.count() == 0


def test_setting_the_wall_writes_the_difference_and_audits_both_directions():
    user = make_user()
    one, two = make_entitlement(), make_entitlement()
    grant(one, user=user)
    grant(two, user=user)
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        added, removed = set_workstream_scope(user_principal(user), stream, [one.pk])
        assert (added, removed) == (frozenset({one.pk}), frozenset())
        added, removed = set_workstream_scope(user_principal(user), stream, [two.pk])
        assert (added, removed) == (frozenset({two.pk}), frozenset({one.pk}))
    kinds = [r.action for r in audit.for_target("workstream", stream.pk)]
    assert kinds.count(actions.WORKSTREAM_SCOPE_ADDED) == 2
    assert kinds.count(actions.WORKSTREAM_SCOPE_REMOVED) == 1


def test_deleting_a_stream_that_holds_rows_is_refused_by_name(agent):
    """`delete_workstream` COUNTS FIRST and refuses by name — the same
    count-then-name shape `delete_entitlement` already uses. "Delete them
    first", not "delete or re-home them first", because re-homing is not
    an action this product has."""
    user = make_user()
    stream = _workstream(user_principal(user))
    Conversation.objects.create(agent=agent, workstream=stream)
    with posture("enterprise"):
        message = delete_workstream(user_principal(user), stream)
    assert message is not None
    assert "1 conversation" in message
    assert "0 documents" in message
    assert Workstream.objects.filter(pk=stream.pk).exists()


def test_an_emptied_stream_deletes_and_takes_its_shares_with_it():
    user, reader = make_user(), make_user()
    stream = _workstream(user_principal(user))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert delete_workstream(user_principal(user), stream) is None
    assert not Workstream.objects.filter(pk=stream.pk).exists()
    assert not Share.objects.filter(target_type=Share.Target.WORKSTREAM,
                                    target_key=str(stream.pk)).exists()


def test_a_conversation_is_born_in_a_stream_and_the_column_is_never_written_again(agent):
    """OWNER DECISION 2: stream-first creation only. There is NO move
    affordance in v1, and `Conversation.workstream` is immutable for
    life -- a thread that can change containers is a thread whose taint
    history is a lie."""
    user = make_user()
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        conversation = create_conversation(user_principal(user), agent, workstream=stream)
    assert conversation.workstream_id == stream.pk


def test_the_column_is_written_at_creation_and_by_no_other_writer(agent):
    """OWNER DECISION 2, asserted as a PROPERTY rather than by grepping
    the module's source text.

    An earlier draft asserted `"workstream_id=" not in source`, which
    pinned an implementation detail: it survives Tasks 16 and 17 only
    because their new clauses happen to spell `workstream_id__in=`, and
    it would fail on a perfectly correct refactor. What actually matters
    is that the five mutating writers this module exposes leave the
    column alone, so that is what this checks."""
    user = make_user()
    one, two = _workstream(user_principal(user)), _workstream(user_principal(user))
    with posture("enterprise"):
        conversation = create_conversation(user_principal(user), agent, workstream=one)
        rename_conversation(user_principal(user), conversation, "Renamed")
        set_conversation_archived(user_principal(user), conversation, archived=True)
        set_conversation_archived(user_principal(user), conversation, archived=False)
        copy = duplicate_conversation(user_principal(user), conversation, title="Copy")
    conversation.refresh_from_db()
    assert conversation.workstream_id == one.pk
    # And the copy is stamped once at ITS creation, in the same stream --
    # nothing moved, and `two` is reachable by no writer here.
    assert copy.workstream_id == one.pk
    assert not Conversation.objects.filter(workstream=two).exists()


def test_duplicating_a_stream_conversation_keeps_it_in_the_stream(agent):
    """RULING D (spec §23.D). Unamended, one ⋯-menu click would produce a
    LOOSE thread holding labelled material with ZERO taint tags, outside
    every gate this phase builds and shareable through
    `chat-conversation-share`. §12's two gates would be one click from
    decorative."""
    user = make_user()
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        source = create_conversation(user_principal(user), agent, workstream=stream)
        copy = duplicate_conversation(user_principal(user), source, title="Copy")
    assert copy.workstream_id == stream.pk


def test_an_administrator_duplicating_somebody_elses_stream_conversation_cannot_fork_it_out(
        agent):
    """`may_manage_conversation`'s first branch admits them, so this is
    reachable -- and it is the case ruling D's own finding names."""
    owner, admin = make_user(), make_admin()
    stream = _workstream(user_principal(owner))
    with posture("enterprise", admin_sees_content=True):
        source = create_conversation(user_principal(owner), agent, workstream=stream)
        copy = duplicate_conversation(user_principal(admin), source, title="Copy")
    assert copy.workstream_id == stream.pk
    assert copy.owner_key == str(admin.pk)


def test_chat_start_refuses_a_stream_this_principal_may_not_reach(client, agent):
    owner, stranger = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.post(reverse("chat-start"),
                               {"agent": agent.slug, "workstream": str(stream.pk)})
    assert response.status_code == 400
    assert Conversation.objects.count() == 0


class TestTagsForConversations:
    """ROUND 14, Part 2. `tags_for_conversations`'s own docstring
    promises this: a NEW, batched function implementing the SAME
    partition rule `agents.visibility.name_for_viewer` already enforces
    per-message (named-if-held, counted-if-not, spec §12.3), answering a
    different QUESTION (a conversation's own full taint set, not a
    dormancy refusal's "missing" set) but agreeing bit for bit on any one
    row it is asked about.
    """

    def test_matches_name_for_viewer_row_for_row(self, agent):
        """Build one conversation tainted by three entitlements; the
        viewer holds exactly one of them. `tags_for_conversations`'s
        answer for that single row must equal what `name_for_viewer`
        would say about that SAME conversation's full taint set, called
        the ordinary (default, `disclose_all=False`) way -- the two
        functions are asking different questions of the platform but
        must agree about what ONE viewer may be told about ONE row.
        """
        viewer = make_user()
        held = make_entitlement(name="held-one")
        grant(held, user=viewer)
        hidden_a = make_entitlement(name="hidden-a")
        hidden_b = make_entitlement(name="hidden-b")

        conversation = make_conversation(agent=agent)
        for entitlement in (held, hidden_a, hidden_b):
            ConversationTaint.objects.create(conversation=conversation, entitlement=entitlement)

        with posture("enterprise"):
            taint_ids = frozenset(
                ConversationTaint.objects.filter(conversation=conversation)
                .values_list("entitlement_id", flat=True))
            expected_named, expected_unnamed = name_for_viewer(taint_ids, user_principal(viewer))

            batched = tags_for_conversations([conversation], user_principal(viewer))

        row = batched[conversation.pk]
        assert row["named"] == expected_named
        assert row["unnamed_count"] == expected_unnamed
        # Non-vacuous: the held one IS named, the other two are NOT --
        # otherwise both sides could trivially agree by both naming
        # nothing or both naming everything.
        assert row["named"] == ((held.pk, "held-one"),)
        assert row["unnamed_count"] == 2

    def test_a_conversation_with_no_taint_is_named_nothing_and_counted_zero(self, agent):
        """The empty case, both functions' own honest floor -- a
        conversation nobody has tagged names nothing and counts
        nothing, never a stray zero-length placeholder row."""
        viewer = make_user()
        conversation = make_conversation(agent=agent)
        with posture("enterprise"):
            batched = tags_for_conversations([conversation], user_principal(viewer))
        assert batched[conversation.pk] == {"named": (), "unnamed_count": 0}

    def test_it_costs_one_join_and_one_names_call_for_the_whole_page(
            self, agent, django_assert_num_queries):
        """The docstring's own claim: ONE `ConversationTaint` join over
        every listed conversation, ONE `held_entitlement_ids` read, ONE
        `entitlement_names` call over the union of held ids actually
        present -- never per row. Proven as flatness (10 rows costs the
        SAME as 1), the house idiom for a bounded-query claim, rather
        than a fixed count that would just be restating the number.

        FOUR, measured: the `ConversationTaint` join, `held_entitlement_
        ids`'s own `IdentitySettings`/`identity_user` pair (`is_admin`,
        un-threaded here the same way `sidebar_context`'s own baseline
        comment names for `owned_rows_q`), and `entitlement_names`. What
        this test actually needs is flatness -- the absolute number moves
        with the platform, the DIFFERENCE between 1 row and 10 must not."""
        viewer = make_user()
        held = make_entitlement(name="held-one")
        grant(held, user=viewer)

        def _world(rows):
            conversations = [make_conversation(agent=agent) for _ in range(rows)]
            for conversation in conversations:
                ConversationTaint.objects.create(conversation=conversation, entitlement=held)
            return conversations

        with posture("enterprise"):
            one = _world(1)
            with django_assert_num_queries(4):
                tags_for_conversations(one, user_principal(viewer))
            ten = _world(10)
            with django_assert_num_queries(4):
                tags_for_conversations(ten, user_principal(viewer))
