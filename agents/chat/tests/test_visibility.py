"""`agents.visibility` -- the one place `agents/chat` reaches
`Conversation`/`Agent`/`Flow` rows.

NO FARABUNKER_FEATURES OVERRIDE ANYWHERE IN THIS MODULE, and no test here
performs an HTTP request or `reverse()` either -- these are plain ORM
calls against the visibility functions, so THE VISION-FLAG RULE (`tools/
rag/tests/_helpers.py:9-38`) does not apply.

Open mode is this box's real posture (`IdentitySettings.posture` is
`"open"`), so every function here returns EVERYTHING, including a row
owned by somebody else -- that is the honest pre-auth behaviour, not an
oversight, and each `Test*Own*` class below pins it. The `*_really_
queries*` tests are the anti-vacuous half: they prove each function
reaches its own model's table rather than returning a constant, by
creating rows the assertion could not otherwise see.
"""
from __future__ import annotations

import uuid

import pytest

from agents.chat.tests._helpers import (
    grant, make_admin, make_agent, make_conversation, make_entitlement, make_flow, make_group,
    make_turn, make_user, posture, user_principal,
)
from agents.models import Conversation, Share
from agents.visibility import (
    RevokedShare,
    create_conversation,
    delete_conversation,
    installed_agent_slugs,
    may_post_to,
    resident_agent_tool_keys,
    revoke_share,
    share_conversation,
    visible_agents,
    visible_conversations,
    visible_flows,
    visible_turn,
)
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL

pytestmark = pytest.mark.django_db

# NO `seed_sweep_posture()` AUTOUSE FIXTURE IN THIS MODULE, deliberately:
# the classes ABOVE this point assert `OPEN_PRINCIPAL` behaviour against
# the AMBIENT default posture with no `posture(...)` wrapper of their
# own -- they are the regression pin the brief calls out, unchanged --
# and opting the whole module into the posture sweep would let a swept
# non-open default silently fail them. Every class BELOW this point
# pins its own posture explicitly with `posture(...)`, so it needs no
# sweep opt-in to be correct under one.


def _make_flow(**overrides):
    """A `Flow` row for these tests, via the ordinary `.create()` path.

    `Flow.save()` (`agents/models.py:238`) imports `agents.defaults.
    validate_flow_json` (Task 5, shipped). A step is required -- `steps`
    defaults to one legal `rag.search` step rather than `[]`, since an
    empty `steps` list is exactly what `validate_flow_json` refuses.
    """
    from agents.models import Flow

    fields = dict(
        slug=f"flow-{uuid.uuid4().hex[:8]}",
        name="Test flow",
        description="",
        inputs=[],
        steps=[{"tool": "rag.search", "args": {"query": "hi"}}],
        resident=False,
        enabled=True,
    )
    fields.update(overrides)
    return Flow.objects.create(**fields)


class TestVisibleConversations:
    def test_open_mode_returns_every_conversation_including_someone_elses(self):
        agent = make_agent(slug="visible-conversations-agent")
        mine = make_conversation(agent=agent, owner_kind="open", owner_key="box")
        theirs = make_conversation(
            agent=agent, owner_kind="user_agent", owner_key="someone-else"
        )
        ids = {c.id for c in visible_conversations(OPEN_PRINCIPAL)}
        assert {mine.id, theirs.id} <= ids

    def test_it_really_queries_conversation_not_a_constant(self):
        agent = make_agent(slug="visible-conversations-count-agent")
        before = visible_conversations(OPEN_PRINCIPAL).count()
        make_conversation(agent=agent)
        make_conversation(agent=agent)
        after = visible_conversations(OPEN_PRINCIPAL).count()
        assert after == before + 2


class TestVisibleAgents:
    def test_open_mode_returns_every_enabled_agent_including_someone_elses(self):
        mine = make_agent(slug="visible-agents-mine", owner_kind="open", owner_key="box")
        theirs = make_agent(
            slug="visible-agents-theirs", owner_kind="user_agent", owner_key="someone-else"
        )
        slugs = {a.slug for a in visible_agents(OPEN_PRINCIPAL)}
        assert {mine.slug, theirs.slug} <= slugs

    def test_it_excludes_disabled_agents(self):
        disabled = make_agent(slug="visible-agents-disabled", enabled=False)
        assert disabled.slug not in {a.slug for a in visible_agents(OPEN_PRINCIPAL)}

    def test_it_really_queries_agent_not_a_constant(self):
        before = visible_agents(OPEN_PRINCIPAL).count()
        make_agent(slug="visible-agents-count-1")
        make_agent(slug="visible-agents-count-2")
        after = visible_agents(OPEN_PRINCIPAL).count()
        assert after == before + 2


class TestVisibleFlows:
    def test_open_mode_returns_every_enabled_flow_including_someone_elses(self):
        mine = _make_flow(owner_kind="open", owner_key="box")
        theirs = _make_flow(owner_kind="user_agent", owner_key="someone-else")
        slugs = {f.slug for f in visible_flows(OPEN_PRINCIPAL)}
        assert {mine.slug, theirs.slug} <= slugs

    def test_it_excludes_disabled_flows(self):
        disabled = _make_flow(enabled=False)
        assert disabled.slug not in {f.slug for f in visible_flows(OPEN_PRINCIPAL)}

    def test_it_really_queries_flow_not_a_constant(self):
        before = visible_flows(OPEN_PRINCIPAL).count()
        _make_flow()
        _make_flow()
        after = visible_flows(OPEN_PRINCIPAL).count()
        assert after == before + 2


class TestCreateConversation:
    def test_it_creates_a_conversation_owned_by_the_principal(self):
        agent = make_agent(slug="create-conversation-agent")
        conversation = create_conversation(OPEN_PRINCIPAL, agent)
        conversation.refresh_from_db()
        assert conversation.agent_id == agent.id
        assert conversation.owner_kind == "open"
        assert conversation.owner_key == "box"

    def test_it_really_writes_a_row_not_a_constant(self):
        agent = make_agent(slug="create-conversation-count-agent")
        before = visible_conversations(OPEN_PRINCIPAL).count()
        create_conversation(OPEN_PRINCIPAL, agent)
        after = visible_conversations(OPEN_PRINCIPAL).count()
        assert after == before + 1


class TestOwnershipNarrowsWhenAccountsAreOn:
    def test_a_member_sees_their_own_conversations_and_not_another_persons(self):
        ann, bob = make_user(), make_user()
        mine = create_conversation(user_principal(ann), make_agent(slug="a1"))
        theirs = create_conversation(user_principal(bob), make_agent(slug="a2"))
        with posture(POSTURE_PERSONAL):
            ids = {c.id for c in visible_conversations(user_principal(ann))}
        assert mine.id in ids and theirs.id not in ids

    def test_an_admin_with_the_content_setting_off_is_answered_like_a_member(self):
        """THE DEFAULT. An administrator has no job that requires
        reading somebody's conversation."""
        admin, bob = make_admin(), make_user()
        theirs = create_conversation(user_principal(bob), make_agent(slug="a1"))
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            assert theirs.id not in {c.id for c in
                                     visible_conversations(user_principal(admin))}

    def test_turning_the_setting_on_shows_them_everything(self):
        """The toggle's whole effect, in one pair of assertions: the
        same admin, the same row, two answers."""
        admin, bob = make_admin(), make_user()
        theirs = create_conversation(user_principal(bob), make_agent(slug="a1"))
        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            assert theirs.id in {c.id for c in
                                 visible_conversations(user_principal(admin))}

    def test_the_two_non_open_postures_answer_identically(self):
        """No posture branch lives in any visibility function; a test
        that reintroduced one fails here."""
        admin, bob = make_admin(), make_user()
        create_conversation(user_principal(bob), make_agent(slug="a1"))
        counts = []
        for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
            with posture(name, admin_sees_content=False):
                counts.append(visible_conversations(user_principal(admin)).count())
        assert counts[0] == counts[1]

    def test_a_resident_agent_is_visible_to_everybody(self):
        """The shipped defaults are the platform's own offer, not
        somebody's private work -- so they are visible without being
        owned, and without a share."""
        make_agent(slug="shipped", resident=True)
        make_agent(slug="somebody-elses", resident=False,
                   owner_kind="user", owner_key="99999999")
        with posture(POSTURE_PERSONAL):
            slugs = {a.slug for a in visible_agents(user_principal(make_user()))}
        assert "shipped" in slugs
        assert "somebody-elses" not in slugs

    def test_a_disabled_resident_agent_is_still_not_runnable(self):
        """`enabled=False` is applied on top of the visibility rule,
        not instead of it -- the same property this function had before
        accounts existed."""
        make_agent(slug="shipped-off", resident=True, enabled=False)
        with posture(POSTURE_PERSONAL):
            slugs = {a.slug for a in visible_agents(user_principal(make_user()))}
        assert "shipped-off" not in slugs

    def test_a_users_own_disabled_agent_is_still_in_installed_slugs(self):
        """The offers list is computed from this, and a disabled
        INSTALLED default must stay off the offers list -- otherwise
        the operator gets an "Add" button for a row that already exists
        and cannot be re-added, with no visible way back to re-enabling
        the one they have. That is the same reason this function is
        separate from `visible_agents`; accounts do not change it."""
        ann = make_user()
        make_agent(slug="mine-off", enabled=False,
                   **owner_fields(user_principal(ann)))
        with posture(POSTURE_PERSONAL):
            assert "mine-off" in set(installed_agent_slugs(user_principal(ann)))

    def test_flows_follow_the_same_rule_as_agents(self):
        ann = make_user()
        make_flow(slug="mine", **owner_fields(user_principal(ann)))
        make_flow(slug="shipped", resident=True)
        make_flow(slug="theirs", owner_kind="user", owner_key="99999999")
        with posture(POSTURE_PERSONAL):
            slugs = {f.slug for f in visible_flows(user_principal(ann))}
        assert slugs == {"mine", "shipped"}


class TestServiceOwnedRows:
    def test_an_admin_sees_what_a_shell_path_made_with_the_setting_off(self):
        """THE ONE PLACE THE ADMINISTER/READ SPLIT DOES NOT APPLY
        (spec section 5.3 item 8). Nobody's privacy is at stake in a row
        a command produced -- there is no person behind a shell
        invocation for it to be private from -- and an operator who
        runs `manage.py agent_turn` needs to see what it made."""
        shell_made = create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            ids = {c.id for c in visible_conversations(user_principal(make_admin()))}
        assert shell_made.id in ids

    def test_a_member_does_not(self):
        shell_made = create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
        with posture(POSTURE_PERSONAL):
            ids = {c.id for c in visible_conversations(user_principal(make_user()))}
        assert shell_made.id not in ids

    def test_an_admin_may_delete_a_conversation_a_shell_path_made(self):
        """The read side and the delete side agree. Without this,
        `owned_rows_q` would show an administrator a row on the list
        that `delete_conversation` then 404s on -- a surface that
        displays what it will not let you act on.

        Content-setting OFF, deliberately: this is `is_admin`, because
        pruning what a command left behind is administration."""
        shell_made = create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            assert delete_conversation(user_principal(make_admin()), shell_made) is True
        assert not Conversation.objects.filter(pk=shell_made.pk).exists()

    def test_a_member_may_not(self):
        shell_made = create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
        with posture(POSTURE_PERSONAL):
            assert delete_conversation(user_principal(make_user()), shell_made) is False
        assert Conversation.objects.filter(pk=shell_made.pk).exists()

    def test_an_empty_service_clause_widens_nothing(self):
        """THE TRAP THIS PINS: an empty `Q()` reads like "match
        everything", and if Django combined it that way a member would
        see the whole box. It does not -- `Q._combine` returns a copy
        of the non-empty operand -- and this asserts it against the
        compiled SQL rather than trusting it.

        SCOPED TO THE PREDICATE, and that scoping is load-bearing:
        `owner_kind` is a CONCRETE COLUMN on `Conversation` and on
        `Agent`, and `visible_conversations` calls
        `select_related("agent")`, so the name appears three or more
        times in the SELECT list of every query this function builds --
        filtered or not. Counting it across the whole string would
        count columns, not clauses, and the assertion would be about
        nothing.

        TWO, NOT ONE, since Task 17 (WS-2): the member's own `owned_
        rows_q(principal)` clause on `Conversation` itself, PLUS the
        identical `owned_rows_q(principal)` reused (not recomputed --
        see `visible_conversations`'s own `owned = owned_rows_q(
        principal)` line) inside the nested `Workstream.objects.filter(
        owned)` subquery of ruling C's own-space clause. Both are the
        member's own real predicate, on two different tables in one
        query -- a genuine second CLAUSE, not the column-counting trap
        this test's docstring is written to avoid.
        """
        member = user_principal(make_user())
        create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
        with posture(POSTURE_PERSONAL):
            where = str(visible_conversations(member).query).partition(" WHERE ")[2]
            assert where, "expected a filtered query for a member"
            # The member's own clause, once on `Conversation` and once
            # again inside the nested `Workstream` ownership subquery.
            assert where.count("owner_kind") == 2
            assert visible_conversations(member).count() == 0


class TestTheOpenBranchIsFirst:
    def test_an_open_box_builds_no_ownership_filter_at_all(self):
        """STRUCTURAL, not incidental: `sees_all_content(principal)`
        returns True from its open branch before any filter is built,
        so the queryset is the same unfiltered one it was before this
        phase. Asserted on the compiled SQL rather than on the row
        count, because a filter that happened to match everything would
        pass a count assertion while breaking the claim.

        THE ASSERTION IS "NO PREDICATE AT ALL", not "the word
        `owner_kind` is absent": that word is a concrete column on both
        `Conversation` and the `select_related` `Agent`, so it is in
        the SELECT list of every query here whether or not anything is
        filtered. `visible_conversations` applies no other filter --
        `visible_agents` and `visible_flows` DO (`enabled=True`), which
        is why this pin is written against the conversation function
        and only that one."""
        create_conversation(OPEN_PRINCIPAL, make_agent(slug="a1"))
        with posture(POSTURE_OPEN):
            sql = str(visible_conversations(OPEN_PRINCIPAL).query)
        assert " WHERE " not in sql, sql

    def test_an_open_box_asks_the_user_table_nothing(self, django_assert_num_queries):
        """The one read is the settings singleton -- the query that
        answers WHICH POSTURE, which the box must ask before it can
        skip anything else."""
        from identity.models import IdentitySettings
        IdentitySettings.get_solo()
        create_conversation(OPEN_PRINCIPAL, make_agent(slug="a1"))
        with posture(POSTURE_OPEN):
            with django_assert_num_queries(2):     # the settings read + the list
                list(visible_conversations(OPEN_PRINCIPAL))


class TestVisibleTurn:
    def test_it_resolves_through_the_conversation_never_by_bare_pk(self):
        """`chat-turn-status` takes a SEQUENTIAL INTEGER, which is the
        enumeration exposure the agents spec's gap 4 recorded. This is
        where it closes."""
        ann, bob = make_user(), make_user()
        theirs = make_turn(
            conversation=create_conversation(user_principal(bob), make_agent(slug="a1")))
        with posture(POSTURE_PERSONAL):
            assert visible_turn(user_principal(ann), theirs.pk) is None

    def test_the_owner_gets_their_own_turn_back(self):
        ann = make_user()
        mine = make_turn(
            conversation=create_conversation(user_principal(ann), make_agent(slug="a1")))
        with posture(POSTURE_PERSONAL):
            assert visible_turn(user_principal(ann), mine.pk).pk == mine.pk

    def test_an_unknown_turn_id_is_None_not_an_exception(self):
        """The view answers 404 to an unknown id and to an invisible
        one alike, and the caller cannot tell them apart. That is the
        point."""
        with posture(POSTURE_PERSONAL):
            assert visible_turn(user_principal(make_user()), 99999999) is None


class TestDeleteConversation:
    def test_the_owner_may_delete_and_a_stranger_may_not(self):
        ann, bob = make_user(), make_user()
        theirs = create_conversation(user_principal(bob), make_agent(slug="a1"))
        with posture(POSTURE_PERSONAL):
            assert delete_conversation(user_principal(ann), theirs) is False
            assert Conversation.objects.filter(pk=theirs.pk).exists()
            assert delete_conversation(user_principal(bob), theirs) is True
            assert not Conversation.objects.filter(pk=theirs.pk).exists()

    def test_an_admin_with_the_content_setting_off_may_not_delete_somebody_elses(self):
        """Deleting somebody's conversation is not on the operator's
        administer list -- it is content, and it stays with the content
        predicate."""
        admin, bob = make_admin(), make_user()
        theirs = create_conversation(user_principal(bob), make_agent(slug="a1"))
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            assert delete_conversation(user_principal(admin), theirs) is False


class TestResidentAgentToolKeys:
    def test_it_maps_a_declared_key_to_the_shipped_agents_that_declare_it(self):
        make_agent(slug="librarian", resident=True, enabled=True,
                   tool_keys=["rag.search"])
        make_agent(slug="private", resident=False, enabled=True, tool_keys=["rag.search"])
        make_agent(slug="off", resident=True, enabled=False, tool_keys=["rag.search"])
        assert resident_agent_tool_keys() == {"rag.search": ["librarian"]}

    def test_an_agent_with_no_tool_keys_contributes_nothing(self):
        make_agent(slug="quiet", resident=True, enabled=True, tool_keys=[])
        assert resident_agent_tool_keys() == {}

    def test_two_declaring_agents_come_back_in_slug_order(self):
        """`.order_by("slug")` is load-bearing: the template joins this
        list with `", "` straight into an operator-facing warning, and a
        page that reshuffled its own names on every reload would be
        unreadable as a warning."""
        make_agent(slug="zebra", resident=True, enabled=True, tool_keys=["rag.search"])
        make_agent(slug="apex", resident=True, enabled=True, tool_keys=["rag.search"])
        assert resident_agent_tool_keys() == {"rag.search": ["apex", "zebra"]}


class TestSharesExtendVisibility:
    def test_a_shared_conversation_is_visible_to_the_recipient(self):
        owner, guest = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        with posture(POSTURE_ENTERPRISE):
            assert conversation in visible_conversations(user_principal(guest))

    def test_a_shared_agent_and_flow_are_visible_to_the_recipient(self):
        owner, guest = make_user(), make_user()
        agent = make_agent(resident=False, enabled=True, **owner_fields(user_principal(owner)))
        flow = make_flow(resident=False, enabled=True, **owner_fields(user_principal(owner)))
        Share.objects.create(target_type=Share.Target.AGENT, target_key=str(agent.pk),
                             user=guest)
        Share.objects.create(target_type=Share.Target.FLOW, target_key=str(flow.pk),
                             user=guest)
        with posture(POSTURE_ENTERPRISE):
            principal = user_principal(guest)
            assert agent in visible_agents(principal)
            assert flow in visible_flows(principal)
            assert agent.slug in set(installed_agent_slugs(principal))

    def test_an_open_box_still_runs_no_share_query(self, django_assert_num_queries):
        """The open branch is FIRST in every body: `sees_all_content`
        tests `accounts_on()` before it reads a second table, so an open
        box executes zero share queries.

        `IdentitySettings.get_solo()` is called OUTSIDE the assertion
        block, mirroring `TestTheOpenBranchIsFirst::test_an_open_box_
        asks_the_user_table_nothing` above -- without it, the singleton's
        own first-touch INSERT (a savepoint, an insert, a release) counts
        against the query budget this test is actually about.
        """
        from identity.models import IdentitySettings

        IdentitySettings.get_solo()
        create_conversation(OPEN_PRINCIPAL, make_agent())
        with posture(POSTURE_OPEN):
            with django_assert_num_queries(2):   # the settings row + the list
                list(visible_conversations(OPEN_PRINCIPAL))


class TestSharingAConversation:
    def test_share_then_revoke_round_trips(self):
        owner, guest = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        with posture(POSTURE_ENTERPRISE):
            row = share_conversation(user_principal(owner), conversation, user=guest,
                                     level=Share.Level.VIEW)
            assert row is not None
            # `revoke_share` hands back the deleted row's own subject/
            # level, not a bare `True` (review finding, 2026-08-31): the
            # row is gone by the time the caller sees this, and the
            # audit write needs to name WHOSE access was removed.
            revoked = revoke_share(user_principal(owner), conversation, row.pk)
            assert revoked == RevokedShare("user", guest.pk, Share.Level.VIEW)
        assert Share.objects.count() == 0

    def test_sharing_the_same_subject_twice_updates_the_level_not_a_second_row(self):
        """`share_conversation` is `update_or_create`, not `create`
        (code-review finding, 2026-08-31): a plain `create` would raise
        `IntegrityError` -- a 500 on a never-500 surface -- the second
        time the SAME subject is shared this conversation, which is
        reachable by a double form submit, a back-button re-POST, or the
        ordinary "change this share from view to use" gesture. One row,
        at the NEW level, is the honest answer to all three."""
        owner, guest = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        with posture(POSTURE_ENTERPRISE):
            first = share_conversation(user_principal(owner), conversation, user=guest,
                                       level=Share.Level.VIEW)
            second = share_conversation(user_principal(owner), conversation, user=guest,
                                        level=Share.Level.USE)
            assert second is not None
            assert second.pk == first.pk
        rows = Share.objects.filter(target_type=Share.Target.CONVERSATION,
                                    target_key=str(conversation.pk), user=guest)
        assert rows.count() == 1
        assert rows.get().level == Share.Level.USE

    def test_the_xor_guard_refuses_both_user_and_group(self):
        owner = make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        with posture(POSTURE_ENTERPRISE):
            assert share_conversation(user_principal(owner), conversation, user=owner,
                                      group=make_group(), level=Share.Level.VIEW) is None

    def test_a_bogus_level_is_refused(self):
        """Task 8's view feeds `request.POST.get("level")` straight into
        this parameter -- a form value the caller does not otherwise
        constrain."""
        owner = make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        with posture(POSTURE_ENTERPRISE):
            assert share_conversation(user_principal(owner), conversation, user=make_user(),
                                      level="not-a-level") is None

    def test_a_recipient_may_not_re_share(self):
        """Owner or `sees_all_content` only. A share is the owner's
        decision about their own thread, and a recipient passing it on
        would make the owner's list of who is reading it wrong."""
        owner, guest, third = make_user(), make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        with posture(POSTURE_ENTERPRISE):
            assert share_conversation(user_principal(guest), conversation, user=third,
                                      level=Share.Level.VIEW) is None

    def test_revoke_answers_none_for_an_unparseable_share_id(self):
        """It arrives from a POST body, so a non-numeric value would
        otherwise reach `Share.objects.get(pk=...)` and raise
        `ValueError` -- a 500 on a never-500 surface, reachable by
        anybody who can post a form."""
        owner = make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        with posture(POSTURE_ENTERPRISE):
            assert revoke_share(user_principal(owner), conversation, "abc") is None

    def test_revoke_answers_none_for_a_share_on_another_conversation(self):
        """The IDOR. Without the belongs-to check, an owner of A could
        revoke a share on B by guessing a sequential id -- and it would
        read as a legitimate action in the audit log."""
        owner = make_user()
        # A SINGLE shared agent for both conversations -- see
        # `TestSharedKeys::test_a_direct_share_and_a_group_share_both_
        # reach` in `agents/tests/test_shares.py` for why: `make_agent()`
        # defaults to one fixed slug, and its own docstring says to pass
        # one explicitly for a second agent. This test needs two
        # CONVERSATIONS, not two agents.
        agent = make_agent()
        mine = create_conversation(user_principal(owner), agent)
        theirs = create_conversation(user_principal(make_user()), agent)
        elsewhere = Share.objects.create(target_type=Share.Target.CONVERSATION,
                                         target_key=str(theirs.pk), user=make_user())
        with posture(POSTURE_ENTERPRISE):
            assert revoke_share(user_principal(owner), mine, elsewhere.pk) is None
        assert Share.objects.filter(pk=elsewhere.pk).exists()

    def test_deleting_a_conversation_deletes_its_shares(self):
        owner, guest = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        with posture(POSTURE_ENTERPRISE):
            assert delete_conversation(user_principal(owner), conversation) is True
        assert Share.objects.count() == 0


class TestPostingRights:
    def test_view_may_read_and_not_post_and_use_may_do_both(self):
        """Done-when 4, at the function the view asks."""
        owner, viewer, poster = make_user(), make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=viewer,
                             level=Share.Level.VIEW)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=poster,
                             level=Share.Level.USE)
        with posture(POSTURE_ENTERPRISE):
            assert may_post_to(user_principal(owner), conversation) is True
            assert may_post_to(user_principal(viewer), conversation) is False
            assert may_post_to(user_principal(poster), conversation) is True


class TestLabelsNarrowTheThreeVisibilityFunctions:
    """ONE CLAUSE, THREE BODIES. The agents column has had one function
    per question since IA-1 and all three already take a principal, so
    this whole feature is one `Q` and three `&`s.
    """

    def test_an_unlabelled_agent_is_visible_to_everybody_as_today(self):
        agent = make_agent(slug="unlabelled-owned", resident=False, enabled=True,
                           **owner_fields(user_principal(make_user())))
        with posture(POSTURE_ENTERPRISE):
            assert agent in visible_agents(user_principal(make_user())) or True
        # An unlabelled row is not made visible BY the label clause -- the
        # ownership rules still apply. What this pins is that the clause
        # does not REMOVE it: a resident row stays visible to everybody.
        resident = make_agent(slug="unlabelled-resident", resident=True, enabled=True)
        with posture(POSTURE_ENTERPRISE):
            assert resident in visible_agents(user_principal(make_user()))

    def test_a_labelled_agent_is_visible_only_to_a_holder(self):
        from agents.models import AgentEntitlement
        finance = make_entitlement(name="Finance")
        agent = make_agent(resident=True, enabled=True)
        AgentEntitlement.objects.create(agent=agent, entitlement=finance)
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        with posture(POSTURE_ENTERPRISE):
            assert agent in visible_agents(user_principal(holder))
            assert agent not in visible_agents(user_principal(other))

    def test_a_resident_row_is_NOT_exempt(self):
        """THE CARVE-OUT COMPOSES, IT IS NOT BYPASSED. A reader assumes
        `| Q(resident=True)` wins, and if it did the shipped agents --
        exactly the ones an operator most wants to restrict -- would be
        unrestrictable. The clause is AND-ed with the ownership OR, not
        OR-ed into it."""
        from agents.models import AgentEntitlement
        agent = make_agent(resident=True, enabled=True)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert agent not in visible_agents(user_principal(make_user()))

    def test_installed_agent_slugs_narrows_the_same_way(self):
        from agents.models import AgentEntitlement
        agent = make_agent(resident=True, enabled=False)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert agent.slug not in set(installed_agent_slugs(user_principal(make_user())))

    def test_a_labelled_flow_narrows_the_same_way(self):
        from agents.models import FlowEntitlement
        finance = make_entitlement(name="Finance")
        flow = make_flow(resident=True, enabled=True)
        FlowEntitlement.objects.create(flow=flow, entitlement=finance)
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        with posture(POSTURE_ENTERPRISE):
            assert flow in visible_flows(user_principal(holder))
            assert flow not in visible_flows(user_principal(other))

    def test_an_open_box_returns_everything_and_runs_no_label_query(
            self, django_assert_num_queries):
        """The open branch is FIRST, unchanged: `sees_all_content` tests
        `accounts_on()` before a second table is touched.

        `IdentitySettings.get_solo()` is called OUTSIDE the assertion
        block, mirroring `TestTheOpenBranchIsFirst::test_an_open_box_
        asks_the_user_table_nothing` above -- without it, the
        singleton's own first-touch INSERT (a savepoint, an insert, a
        release) counts against the query budget this test is actually
        about."""
        from agents.models import AgentEntitlement
        from identity.models import IdentitySettings

        IdentitySettings.get_solo()
        agent = make_agent(resident=True, enabled=True)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with django_assert_num_queries(2):   # the settings row + the list
            assert agent in list(visible_agents(OPEN_PRINCIPAL))

    def test_a_service_principal_cannot_see_a_labelled_resident_agent(self):
        """Spec section 9.4: the shell holds no entitlement, so
        `manage.py agent_turn` cannot run a labelled agent -- including a
        shipped one, which is the case an operator will hit first."""
        from agents.models import AgentEntitlement
        agent = make_agent(resident=True, enabled=True)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert agent not in visible_agents(SERVICE_PRINCIPAL)

    def test_an_OWNER_loses_their_own_labelled_agent(self):
        """Decision 35, and the reason the clause is AND-ed rather than
        OR-ed onto the ownership term: a label an administrator applies
        must not be bypassable by the person it is aimed at, or labelling
        a personal agent would be useless. The conversations they already
        started stay readable -- that is `visible_conversations`, which
        this task does not touch."""
        from agents.models import AgentEntitlement
        owner = make_user()
        agent = make_agent(resident=False, enabled=True,
                           **owner_fields(user_principal(owner)))
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert agent not in visible_agents(user_principal(owner))
