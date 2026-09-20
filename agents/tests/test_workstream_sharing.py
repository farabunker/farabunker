"""Two gates, one dormancy rule, and the platform's one scoped exception
to the 404 house rule."""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.models import Conversation, ConversationTaint, Share, WorkstreamTaint
from agents.tests._helpers import _workstream
from agents.visibility import (
    NAME_CAP, delete_workstream, may_manage_conversation, may_post_to, name_for_viewer,
    share_workstream, visible_conversations, workstream_taint_ids,
)
from agents.workstreams import stream_access
from identity.access import entitlement_ids_for_subject, owner_fields
from identity.testing import (
    grant, make_entitlement, make_group, make_user, posture, sign_in, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def agent():
    """A plain `Agent` row -- this module's own fixture, matching
    `agents/chat/tests/test_workstream_page.py`'s own precedent, not a
    shared one: `Conversation.objects.create(agent=...)` needs a row and
    nothing here cares which."""
    from agents.tests._helpers import make_agent

    return make_agent()


def test_gate_one_refuses_and_names_what_the_SHARER_holds_and_counts_the_rest():
    """RULING B (spec §23.B). The sharer does NOT necessarily hold every
    tag — a previous recipient's turn may have brought one in — so the
    message names what THIS VIEWER holds and counts the rest."""
    sharer, recipient = make_user(), make_user()
    finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
    grant(finance, user=sharer)
    stream = _workstream(user_principal(sharer))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=finance)
    WorkstreamTaint.objects.create(workstream=stream, entitlement=legal)
    with posture("enterprise"):
        result = share_workstream(user_principal(sharer), stream,
                                  user=recipient, level=Share.Level.USE)
    assert result.missing_ids == frozenset({finance.pk, legal.pk})
    assert [n for _, n in result.missing_names] == ["Finance"]
    assert result.unnamed_count == 1


def test_gate_one_names_both_when_the_sharer_holds_both():
    sharer, recipient = make_user(), make_user()
    finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
    grant(finance, user=sharer)
    grant(legal, user=sharer)
    stream = _workstream(user_principal(sharer))
    for ent in (finance, legal):
        WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        result = share_workstream(user_principal(sharer), stream,
                                  user=recipient, level=Share.Level.USE)
    assert sorted(n for _, n in result.missing_names) == ["Finance", "Legal"]
    assert result.unnamed_count == 0


def test_a_share_succeeds_once_the_recipient_holds_every_tag():
    sharer, recipient = make_user(), make_user()
    ent = make_entitlement()
    grant(ent, user=sharer)
    grant(ent, user=recipient)
    stream = _workstream(user_principal(sharer))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        row = share_workstream(user_principal(sharer), stream,
                               user=recipient, level=Share.Level.USE)
    assert isinstance(row, Share)
    assert row.level == Share.Level.USE


def test_a_view_level_share_is_refused_by_name():
    """AUTHOR DECISION 16. A stream share that could not converse would
    be a reading list, and owner decision 7 says a recipient reads AND
    converses — so storing a `view` level no reader honours would be a
    column value with two meanings."""
    sharer, recipient = make_user(), make_user()
    stream = _workstream(user_principal(sharer))
    with posture("enterprise"):
        result = share_workstream(user_principal(sharer), stream,
                                  user=recipient, level=Share.Level.VIEW)
    assert result is not None and not isinstance(result, Share)
    assert "converse" in str(result).lower() or "use" in str(result).lower()
    assert Share.objects.count() == 0


def test_a_group_recipient_is_checked_against_the_GROUPS_own_grants():
    """AUTHOR DECISION 23. A group is the subject of a grant in this
    codebase (`grant_user_xor_group`), so "does this group hold E" is a
    ROW, not a computation over membership — and a member who personally
    lacks E is caught by the READ-TIME gate, which checks each actual
    reader.

    THE GRANT IS REVOKED AFTER THE SHARE, not left standing, and that is
    load-bearing rather than incidental. While `group`'s own
    `EntitlementGrant` for `ent` stands, EVERY member of `group` —
    `member` included — holds `ent` too: `held_entitlement_ids` follows
    `group__user`, the identical join `entitlement_ids_for_subject`
    would need to disagree with in order to make "the group holds it but
    this member personally does not" true while that row exists. Gate
    one and the read-time gate ask two DIFFERENT questions on the SAME
    join, and the only way to observe them diverge for a group recipient
    is for the fact to change in between — a grant revoked after the
    share was made, exactly as `test_dormancy_is_computed_never_stored`
    shows for a `user` recipient a few tests up.
    """
    sharer = make_user()
    member = make_user()
    group = make_group()
    group.user_set.add(member)
    ent = make_entitlement()
    grant(ent, user=sharer)
    group_grant = grant(ent, group=group)
    stream = _workstream(user_principal(sharer))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        assert entitlement_ids_for_subject(group=group) == frozenset({ent.pk})
        row = share_workstream(user_principal(sharer), stream,
                               group=group, level=Share.Level.USE)
        assert isinstance(row, Share)
        # While the group's own grant stands, `member` holds `ent`
        # THROUGH IT (the same join `held_entitlement_ids` always
        # follows) -- so the read-time gate agrees with gate one, and
        # that is the platform's ordinary rule, not a defect.
        assert stream_access(user_principal(member), stream).ok is True
        # Revoke the GROUP's own grant. `member` never held `ent`
        # directly, so this is now the read-time gate's own case to
        # catch, checked against `member` -- the actual reader -- and
        # not against `group`, which gate one already cleared and never
        # asks again.
        group_grant.delete()
        access = stream_access(user_principal(member), stream)
    assert access.ok is False
    assert access.missing == frozenset({ent.pk})


def test_gate_two_names_EVERY_missing_entitlement_capped_at_five():
    """RULING E (spec §23.E), and the reason it exists: applying ruling
    B's DEFAULT here would name NOTHING, ALWAYS — at gate two the viewer
    IS the recipient and `missing = taint_ids - held(recipient)` by
    construction, so `missing_ids & held(viewer)` is empty by definition
    and the page would read "and N entitlements you don't hold", the
    exact inverse of owner decision 8's own words."""
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    ents = [make_entitlement(name=f"E{i}") for i in range(7)]
    for ent in ents:
        WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        named, rest = name_for_viewer(
            frozenset(e.pk for e in ents), user_principal(reader), disclose_all=True)
    assert len(named) == NAME_CAP
    assert rest == 7 - NAME_CAP


def test_the_owners_dormant_marker_takes_the_DEFAULT_mode():
    """The two default-mode sites of §12.1's table, pinned APART rather
    than assumed distinct: gate one's refusal and the owner's dormant
    marker each name what THEIR OWN viewer holds and count the rest."""
    owner = make_user()
    held, unheld = make_entitlement(name="Held"), make_entitlement(name="Unheld")
    grant(held, user=owner)
    with posture("enterprise"):
        named, rest = name_for_viewer(frozenset({held.pk, unheld.pk}),
                                      user_principal(owner))
    assert [n for _, n in named] == ["Held"]
    assert rest == 1


def test_dormancy_is_computed_never_stored():
    """A tag added AFTER a share was made makes it dormant with NO WRITE
    anywhere (spec §5.8). A stored flag is stale the moment a grant
    moves, and grants moving is the entire reason the read-time gate
    exists."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert stream_access(user_principal(reader), stream).ok is True
        WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
        access = stream_access(user_principal(reader), stream)
    assert access.ok is False
    assert Share.objects.get().pk is not None      # no write of any kind


def test_the_owner_is_never_locked_out_of_their_own_stream_by_its_tags():
    """Under ruling B a recipient's turn taints the owner's stream, so
    the owner may hold NO GRANT for a tag on their own stream — and a
    stream whose owner could be shut out of it by a recipient's
    retrieval would be a space nobody could administer."""
    owner = make_user()
    ent = make_entitlement()
    stream = _workstream(user_principal(owner))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        access = stream_access(user_principal(owner), stream)
    assert access.ok is True and access.is_owner is True


def test_ruling_B_a_recipients_turn_taints_the_owners_stream(agent):
    """Ruling B pinned with a REAL Share-recipient principal, not merely
    an unowned or owner-owned conversation. `stamp_turn_taint` itself
    never asks who owns the conversation it is stamping — this is the
    test that proves the platform's OTHER half agrees: a recipient's own
    conversation, created inside the owner's stream under a genuine
    `Share` row, taints the STREAM the same as the owner's own turn
    would. `WorkstreamTaint` carries no owner column of its own; it is
    keyed by `workstream` alone, so there is exactly one row for either
    turn to land on.

    ADDED BEYOND THE BRIEF (§17.5 has no such pin): every other test in
    this module and in `agents/tests/test_taint.py` drives
    `stamp_turn_taint` against an unowned or an owner-owned conversation,
    which cannot distinguish "any turn in the stream taints it" from "any
    turn BY A GENUINE SHARE RECIPIENT taints the owner's own row" — and
    ruling B's own sentence is about the second, sharper claim.
    """
    from agents.runtime.taint import stamp_turn_taint
    from agents.models import Turn
    from tools.rag.tests._helpers import make_document

    owner, recipient = make_user(), make_user()
    ent = make_entitlement(name="Confidential")
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=recipient, level=Share.Level.USE)
    doc = make_document()
    doc.entitlement_labels.create(entitlement=ent)
    with posture("enterprise"):
        theirs = Conversation.objects.create(
            agent=agent, workstream=stream, **owner_fields(user_principal(recipient)))
        turn = Turn.objects.create(
            conversation=theirs, index=Turn.next_index(theirs), role=Turn.Role.TOOL,
            text="", artifacts=[f"document:{doc.pk}"], state=Turn.State.DONE, depth=0)
        added = stamp_turn_taint(turn, theirs, turn.artifacts)
    assert added == frozenset({ent.pk})
    # The row landed on the OWNER'S stream, not on a second row of its
    # own: exactly one `WorkstreamTaint` for (stream, ent), and it is the
    # same row `workstream_taint_ids(stream)` reads for both gates.
    ws_tag = WorkstreamTaint.objects.get(workstream=stream, entitlement=ent)
    assert ws_tag.first_conversation == theirs.pk
    assert workstream_taint_ids(stream) == frozenset({ent.pk})
    assert ConversationTaint.objects.filter(conversation=theirs, entitlement=ent).exists()


def test_gate_two_answers_403_for_a_share_holder_and_404_with_the_row_deleted(client):
    """ASSERTED AS A PAIR, because the exception is only safe if the
    negative case holds. There is NO INPUT A STRANGER CAN SUPPLY that
    reaches the 403 branch: without the row, `visible_workstreams`
    excludes the stream and the view raises `Http404` before
    `stream_access` is called."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    share = Share.objects.create(target_type=Share.Target.WORKSTREAM,
                                 target_key=str(stream.pk), user=reader,
                                 level=Share.Level.USE)
    url = reverse("chat-workstream", args=[stream.pk])
    with posture("enterprise"):
        sign_in(client, reader)
        response = client.get(url)
        assert response.status_code == 403
        body = response.content.decode()
        assert "Finance" in body
        assert "Traceback" not in body
        share.delete()
        assert client.get(url).status_code == 404


def test_the_403_page_reveals_nothing_about_the_streams_contents(client, agent):
    """The fence: it reveals nothing about the stream's conversations,
    its documents or its other recipients (author decision 12)."""
    owner, reader, other = make_user(), make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)), name="Q3")
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    Conversation.objects.create(agent=agent, workstream=stream, title="Secret thread")
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=other, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "Q3" in body                      # the name, which they already have
    assert "Secret thread" not in body
    assert other.username not in body


def test_the_dormant_page_marks_the_status_word_not_its_whole_body(client):
    """C-49. `.dormant` is `chat/base.html`'s SMALL INLINE MARKER --
    `color: var(--danger)` on one word beside a name (`_sidebar.html`,
    `workstream_settings.html`), and `agents/chat/README.md` documents it
    as exactly that. Applied to this page's top-level `<article>` it
    inherited into the `<h1>`, the lede and the three paragraphs below it, so the
    whole 403 page read as an error rather than the stream's status
    reading as one. The class stays; the element it hangs on changes."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)), name="Q3")
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        response = client.get(reverse("chat-workstream", args=[stream.pk]))
    assert response.status_code == 403
    body = response.content.decode()
    # The page body is NOT the marker.
    assert '<article class="dormant">' not in body
    # The marker is still on the page, on one word, the same shape
    # `_sidebar.html` uses for the same fact about the same stream.
    assert '<span class="dormant">Dormant</span>' in body
    # And the fence still holds: the name is shown, nothing else is.
    assert "Q3" in body
    assert "Traceback" not in body


def test_a_dormant_share_still_LISTS_in_the_recipients_sidebar(client):
    """Hiding it would make a stream the recipient has been reading
    vanish with no sentence, which is strictly worse than the honest
    refusal — and the recipient already knows it exists."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement()
    stream = _workstream(**owner_fields(user_principal(owner)), name="Q3 planning")
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        body = client.get(reverse("chat-index")).content.decode()
    assert "Q3 planning" in body


def test_ruling_C_the_owner_reads_and_manages_a_recipients_thread(agent):
    """Without the owner clause, `create_conversation`'s
    `**owner_fields(principal)` stamp would make a recipient's new thread
    INVISIBLE to the stream's owner on every surface: the stream page
    would omit it, the owner-only consolidate could never reach it, and
    the `PROTECT` delete would refuse with a count of rows the owner can
    neither open nor delete."""
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        theirs = Conversation.objects.create(agent=agent, workstream=stream,
                                             **owner_fields(user_principal(reader)))
        assert visible_conversations(user_principal(owner)).filter(pk=theirs.pk).exists()
        assert may_manage_conversation(user_principal(owner), theirs) is True
        assert may_post_to(user_principal(owner), theirs) is True


def test_ruling_C_a_recipient_does_not_manage_another_recipients_thread(agent):
    owner, one, two = make_user(), make_user(), make_user()
    stream = _workstream(user_principal(owner))
    for u in (one, two):
        Share.objects.create(target_type=Share.Target.WORKSTREAM,
                             target_key=str(stream.pk), user=u, level=Share.Level.USE)
    with posture("enterprise"):
        theirs = Conversation.objects.create(agent=agent, workstream=stream,
                                             **owner_fields(user_principal(one)))
        # A stream share reaches every conversation IN the stream, so
        # `two` READS it — that is §12.4's own rule. What `two` may not
        # do is MANAGE it.
        assert visible_conversations(user_principal(two)).filter(pk=theirs.pk).exists()
        assert may_manage_conversation(user_principal(two), theirs) is False


def test_ruling_C_a_recipient_may_post_into_another_recipients_thread(agent):
    """THE POST-SIDE TWIN of the read pin just above (review finding 3,
    Task 17 review round 1): owner decision 7's own words are "a stream
    share reaches every conversation in the stream, at `use`" (spec
    §12.4's table, "new turns in those conversations"), and `may_post_to`
    gains the SAME two clauses `visible_conversations` does for exactly
    that reason -- a workstream share carries no `view`-only level to
    tell apart from `use` (author decision 16), so a recipient who reads
    a co-recipient's thread through the container clause may post into
    it too, and nothing pinned that ability on its own before this test.
    """
    owner, one, two = make_user(), make_user(), make_user()
    stream = _workstream(user_principal(owner))
    for u in (one, two):
        Share.objects.create(target_type=Share.Target.WORKSTREAM,
                             target_key=str(stream.pk), user=u, level=Share.Level.USE)
    with posture("enterprise"):
        theirs = Conversation.objects.create(agent=agent, workstream=stream,
                                             **owner_fields(user_principal(one)))
        assert may_post_to(user_principal(two), theirs) is True


def test_every_row_of_the_does_not_reach_table_is_a_404_for_a_recipient(client, agent):
    """Spec §12.4's table, one negative test each — re-sharing, editing
    the wall, pinning, uploading, editing instructions/name/upload
    default, consolidating, deleting the stream, and managing a
    conversation they did not start."""
    owner, reader, other = make_user(), make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader,
                         level=Share.Level.USE)
    owners_thread = Conversation.objects.create(
        agent=agent, workstream=stream, **owner_fields(user_principal(owner)))
    with posture("enterprise"):
        sign_in(client, reader)
        # re-sharing
        assert client.post(reverse("chat-workstream-share", args=[stream.pk]),
                           {"user": str(other.pk)}).status_code == 404
        # editing the wall
        assert client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                           {"entitlements": []}).status_code == 404
        # pinning and unpinning, and uploading into the stream -- both
        # gated on `scope.may_upload`, which is False for a recipient
        assert client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                           {"action": "pin", "document": "1"}).status_code == 404
        # editing instructions, the name and the upload default;
        # archiving; deleting the stream
        for action in ("rename", "description", "instructions", "upload_default",
                       "archive", "unarchive", "delete"):
            assert client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                               {"action": action, "name": "x"}).status_code == 404, action
        # CONSOLIDATION'S ROWS ARE ASSERTED IN TASK 18, not here: the
        # route does not exist yet, and `reverse` would raise
        # `NoReverseMatch` at this task's own green boundary. See
        # `tools/rag/tests/test_consolidate.py::
        # test_a_recipient_gets_404_consolidating_anything_including_their_own_thread`,
        # which asserts ruling F for the owner's threads AND the
        # recipient's own, beside the route that implements it.
        #
        # deleting or renaming a conversation they did not start
        assert client.post(
            reverse("chat-conversation-delete", args=[owners_thread.pk])
        ).status_code == 404

    # And nothing was written by any of them.
    stream.refresh_from_db()
    assert stream.archived_at is None
    assert stream.scope_entitlements.count() == 0
    assert Share.objects.filter(target_type=Share.Target.WORKSTREAM).count() == 1


def test_delete_workstreams_count_matches_what_the_owner_can_actually_open(agent):
    """Ruling C's consequence (spec §8.1): `PROTECT`'s count is then
    always a count of rows the person reading the refusal can act on."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader,
                         level=Share.Level.USE)
    # One thread the OWNER started, one the RECIPIENT started.
    Conversation.objects.create(agent=agent, workstream=stream,
                                **owner_fields(user_principal(owner)))
    Conversation.objects.create(agent=agent, workstream=stream,
                                **owner_fields(user_principal(reader)))

    with posture("enterprise"):
        message = delete_workstream(user_principal(owner), stream)
        openable = visible_conversations(user_principal(owner)).filter(
            workstream_id=stream.pk).count()

    assert "2 conversations" in message
    # Ruling C's consequence: the count and what the owner can actually
    # open are the same number, so the refusal is actionable rather than
    # a wall of rows the owner can neither open nor delete.
    assert openable == 2
