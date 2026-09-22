"""Edit a past prompt: the provenance columns, the gate, and what a
branch is made of."""
from __future__ import annotations

import pytest

from agents.models import Conversation, Turn
from agents.tests._helpers import make_agent, make_conversation, make_turn
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.testing import (
    make_admin, make_entitlement, make_user, posture, user_principal,
)

pytestmark = pytest.mark.django_db


class TestTheProvenanceColumns:
    def test_a_plain_conversation_carries_neither(self):
        conversation = make_conversation(agent=make_agent(slug="plain"))
        assert conversation.branched_from_id is None
        assert conversation.branched_at_index is None

    def test_a_branch_names_its_parent_and_the_index_it_left_from(self):
        agent = make_agent(slug="parented")
        parent = make_conversation(agent=agent)
        child = make_conversation(agent=agent, branched_from=parent,
                                  branched_at_index=3)
        assert child.branched_from_id == parent.id
        assert child.branched_at_index == 3
        assert list(parent.branches.all()) == [child]

    def test_deleting_the_parent_leaves_the_branch_standing(self):
        """SET_NULL, not CASCADE: a branch is a conversation in its own
        right, and losing the row it came from is not a reason to lose
        it. The index survives so the line can still say WHERE it left
        from even when it can no longer say what from."""
        agent = make_agent(slug="orphaned")
        parent = make_conversation(agent=agent)
        child = make_conversation(agent=agent, branched_from=parent,
                                  branched_at_index=2)
        parent.delete()
        child.refresh_from_db()
        assert Conversation.objects.filter(pk=child.pk).exists()
        assert child.branched_from_id is None
        assert child.branched_at_index == 2

    def test_a_branch_of_a_branch_reports_its_immediate_parent(self):
        agent = make_agent(slug="chained")
        first = make_conversation(agent=agent)
        second = make_conversation(agent=agent, branched_from=first,
                                   branched_at_index=1)
        third = make_conversation(agent=agent, branched_from=second,
                                  branched_at_index=1)
        assert third.branched_from_id == second.id


class TestMayEditTurn:
    def _own_thread(self, owner):
        conversation = make_conversation(agent=make_agent(slug="editable-thread"),
                                         **owner_fields(user_principal(owner)))
        turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="first",
                         state=Turn.State.DONE)
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="answer",
                  state=Turn.State.DONE)
        return conversation, turn

    def test_the_owner_may_edit_their_own_finished_user_turn(self):
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turn = self._own_thread(owner)
            assert may_edit_turn(user_principal(owner), conversation, turn) is True

    def test_an_assistant_turn_is_not_editable(self):
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turn = self._own_thread(owner)
            assistant = conversation.turns.filter(role=Turn.Role.ASSISTANT).get()
            assert may_edit_turn(user_principal(owner), conversation, assistant) is False

    def test_a_delegates_turn_is_not_editable(self):
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turn = self._own_thread(owner)
            deep = make_turn(conversation=conversation, role=Turn.Role.USER, text="d",
                             depth=1, state=Turn.State.DONE)
            assert may_edit_turn(user_principal(owner), conversation, deep) is False

    def test_a_turn_from_another_conversation_is_not_editable(self):
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turn = self._own_thread(owner)
            elsewhere = make_turn(role=Turn.Role.USER, text="x", state=Turn.State.DONE)
            assert may_edit_turn(user_principal(owner), conversation,
                                 elsewhere) is False

    def test_any_in_flight_turn_in_the_conversation_blocks_every_edit(self):
        """Editing while an answer is in flight would branch from a
        conversation whose shape is still changing, and the job would
        write its answer back to the ORIGINAL's row anyway."""
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turn = self._own_thread(owner)
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.QUEUED)
            assert may_edit_turn(user_principal(owner), conversation, turn) is False


class TestTheShareAnswersArePinnedExplicitly:
    """Spec review M2. Gated on `may_manage_conversation`, where an
    earlier draft gated on the strictly wider `may_post_to`. Under that
    gate a share recipient could, with one click on somebody else's
    message, mint a NEW conversation THEY OWN holding the original's
    full terminal history -- stamped with their own `owner_fields`,
    surviving revocation of the share that permitted it, and neither
    visible nor deletable by the original owner. A branch IS a copy, so
    it answers to the copy predicate, and `duplicate_conversation`
    refuses exactly this today."""

    def _shared_thread(self, owner, recipient, *, level):
        from agents.visibility import share_conversation

        conversation = make_conversation(agent=make_agent(slug="shared-thread"),
                                         **owner_fields(user_principal(owner)))
        turn = make_turn(conversation=conversation, role=Turn.Role.USER,
                         text="theirs", state=Turn.State.DONE)
        share_conversation(user_principal(owner), conversation, user=recipient,
                           level=level)
        return conversation, turn

    def test_a_view_share_recipient_may_not(self):
        from agents.models import Share
        from agents.visibility import may_edit_turn

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            conversation, turn = self._shared_thread(owner, recipient,
                                                     level=Share.Level.VIEW)
            assert may_edit_turn(user_principal(recipient), conversation,
                                 turn) is False

    def test_a_use_share_recipient_may_not_either(self):
        from agents.models import Share
        from agents.visibility import may_edit_turn

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            conversation, turn = self._shared_thread(owner, recipient,
                                                     level=Share.Level.USE)
            assert may_edit_turn(user_principal(recipient), conversation,
                                 turn) is False

    def test_a_workstream_share_recipient_may_not(self):
        from agents.models import Share
        from agents.tests._helpers import _workstream
        from agents.visibility import may_edit_turn, share_workstream

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            stream = _workstream(**owner_fields(user_principal(owner)))
            conversation = make_conversation(agent=make_agent(slug="stream-thread"),
                                             workstream=stream,
                                             **owner_fields(user_principal(owner)))
            turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="t",
                             state=Turn.State.DONE)
            share_workstream(user_principal(owner), stream, user=recipient,
                             level=Share.Level.USE)
            assert may_edit_turn(user_principal(recipient), conversation,
                                 turn) is False

    def test_the_streams_own_owner_may_because_they_own_it(self):
        from agents.tests._helpers import _workstream
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            stream = _workstream(**owner_fields(user_principal(owner)))
            conversation = make_conversation(agent=make_agent(slug="own-stream"),
                                             workstream=stream,
                                             **owner_fields(user_principal(owner)))
            turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="t",
                             state=Turn.State.DONE)
            assert may_edit_turn(user_principal(owner), conversation, turn) is True

    def test_an_administrator_with_content_access_may(self):
        from agents.visibility import may_edit_turn

        owner, admin = make_user(), make_admin()
        # A KEYWORD ON THE POSTURE HELPER, not a context manager of its
        # own: `identity/testing.py::posture(name, *,
        # admin_sees_content=None, library_posture=None)`. There is no
        # `identity.testing.admin_sees_content` to import, and importing
        # one raises at COLLECTION, which takes the whole module down.
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            conversation = make_conversation(agent=make_agent(slug="admin-reach"),
                                             **owner_fields(user_principal(owner)))
            turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="t",
                             state=Turn.State.DONE)
            assert may_edit_turn(user_principal(admin), conversation, turn) is True


class TestBranchConversation:
    def _thread_of(self, owner, texts, *, slug="branchable"):
        conversation = make_conversation(agent=make_agent(slug=slug),
                                         **owner_fields(user_principal(owner)))
        turns = [make_turn(conversation=conversation, role=Turn.Role.USER, text=text,
                           state=Turn.State.DONE) for text in texts]
        return conversation, turns

    def test_it_copies_turns_strictly_BEFORE_the_edited_index(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b", "c", "d"])
            branch = branch_conversation(user_principal(owner), conversation, turns[2],
                                         title="Branch")
            texts = list(branch.turns.order_by("index").values_list("text", flat=True))
        assert texts == ["a", "b"]

    def test_the_indexes_are_renumbered_from_zero(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b", "c"])
            branch = branch_conversation(user_principal(owner), conversation, turns[2],
                                         title="Branch")
            indexes = list(branch.turns.order_by("index").values_list("index",
                                                                     flat=True))
        assert indexes == [0, 1]

    def test_the_branch_is_the_BRANCHERS_own_and_records_where_it_left(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b"])
            branch = branch_conversation(user_principal(owner), conversation, turns[1],
                                         title="Branch")
        assert branch.owner_kind == "user"
        assert branch.branched_from_id == conversation.id
        assert branch.branched_at_index == turns[1].index

    def test_it_carries_the_agent_and_the_workstream(self):
        """Ruling D: a branch stays in the stream, or one click would
        launder labelled material out of every gate."""
        from agents.tests._helpers import _workstream
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            stream = _workstream(**owner_fields(user_principal(owner)))
            agent = make_agent(slug="streamed")
            conversation = make_conversation(agent=agent, workstream=stream,
                                             **owner_fields(user_principal(owner)))
            make_turn(conversation=conversation, role=Turn.Role.USER, text="a",
                      state=Turn.State.DONE)
            second = make_turn(conversation=conversation, role=Turn.Role.USER, text="b",
                               state=Turn.State.DONE)
            branch = branch_conversation(user_principal(owner), conversation, second,
                                         title="Branch")
        assert branch.workstream_id == stream.pk
        assert branch.agent_id == agent.pk

    def test_it_preserves_author_id_and_drops_invocation_and_queue_job_id(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation = make_conversation(agent=make_agent(slug="authored"),
                                             **owner_fields(user_principal(owner)))
            make_turn(conversation=conversation, role=Turn.Role.USER, text="a",
                      state=Turn.State.DONE, author=owner, queue_job_id=11)
            second = make_turn(conversation=conversation, role=Turn.Role.USER, text="b",
                               state=Turn.State.DONE)
            branch = branch_conversation(user_principal(owner), conversation, second,
                                         title="Branch")
            copied = branch.turns.order_by("index").first()
        assert copied.author_id == owner.pk
        assert copied.queue_job_id is None
        assert copied.invocation_id is None

    def test_a_cancelled_turn_before_the_index_IS_copied_because_it_is_terminal(self):
        """"Terminal" is not "successful". `CANCELLED` is one of
        `_TERMINAL_TURN_STATES`, so a cancelled turn is part of what
        happened in this thread and comes across -- dropping it would
        make the branch a tidied-up version of a conversation that did
        not go that way, which is the rule `duplicate_conversation`
        already holds.

        NO TURN IN FLIGHT CAN REACH THIS FUNCTION, so there is no such
        case to pin from here: `may_edit_turn` refuses while ANY turn in
        the conversation is non-terminal, and a turn that started
        between the gate and the copy would have an index at or above
        the branch point and is already excluded by the bound. The
        copier's state filter earns its place through the OTHER caller
        -- `agents/chat/tests/test_conversation_actions.py::
        TestDuplicate::test_a_mid_run_turn_is_left_behind_and_the_
        indexes_close_up` is where it does real work.
        """
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a"])
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.CANCELLED)
            last = make_turn(conversation=conversation, role=Turn.Role.USER, text="z",
                             state=Turn.State.DONE)
            branch = branch_conversation(user_principal(owner), conversation, last,
                                         title="Branch")
            states = list(branch.turns.order_by("index")
                          .values_list("text", "state"))
        assert states == [("a", Turn.State.DONE), ("", Turn.State.CANCELLED)]

    def test_it_copies_every_taint_row_including_one_after_the_branch_point(self):
        """Over-tainting is safe; under-tainting is a leak. `first_turn`
        is a plain integer precisely so it can name the turn that really
        caused it, in the conversation where it really happened."""
        from agents.models import ConversationTaint
        from agents.visibility import branch_conversation

        owner = make_user()
        entitlement = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b", "c"])
            ConversationTaint.objects.create(conversation=conversation,
                                             entitlement=entitlement,
                                             first_turn=turns[2].index)
            branch = branch_conversation(user_principal(owner), conversation, turns[1],
                                         title="Branch")
        assert branch.taint_tags.count() == 1
        assert branch.taint_tags.get().first_turn == turns[2].index

    def test_it_writes_no_workstream_taint(self):
        """The branch stays in the parent's stream, and the stream's
        materialised union already holds every one of the parent's tags
        -- it was unioned upward when each was stamped. Nothing new
        enters the stream."""
        from agents.models import ConversationTaint, WorkstreamTaint
        from agents.tests._helpers import _workstream
        from agents.visibility import branch_conversation

        owner = make_user()
        entitlement = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            stream = _workstream(**owner_fields(user_principal(owner)))
            conversation = make_conversation(agent=make_agent(slug="tainted-stream"),
                                             workstream=stream,
                                             **owner_fields(user_principal(owner)))
            first = make_turn(conversation=conversation, role=Turn.Role.USER, text="a",
                              state=Turn.State.DONE)
            second = make_turn(conversation=conversation, role=Turn.Role.USER, text="b",
                               state=Turn.State.DONE)
            ConversationTaint.objects.create(conversation=conversation,
                                             entitlement=entitlement,
                                             first_turn=first.index)
            before = WorkstreamTaint.objects.count()
            branch_conversation(user_principal(owner), conversation, second,
                                title="Branch")
        assert WorkstreamTaint.objects.count() == before

    def test_it_copies_no_document_attachment_rows(self):
        """There is no copier seam -- the four registered attachment
        seams are provider, cleanup, uploader and detacher -- and
        inventing a fifth for v1 is not earned (owner ruling, flag 6)."""
        from tools.rag.models import DocumentAttachment

        from agents.tests._helpers import make_document
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b"])
            DocumentAttachment.objects.create(document=make_document(),
                                              conversation_id=conversation.id,
                                              turn_id=turns[0].pk)
            branch = branch_conversation(user_principal(owner), conversation, turns[1],
                                         title="Branch")
        assert not DocumentAttachment.objects.filter(
            conversation_id=branch.id).exists()

    def test_it_carries_no_shares_and_is_neither_pinned_nor_archived(self):
        from agents.models import Share
        from agents.visibility import branch_conversation, share_conversation

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b"])
            share_conversation(user_principal(owner), conversation, user=recipient,
                               level=Share.Level.VIEW)
            branch = branch_conversation(user_principal(owner), conversation, turns[1],
                                         title="Branch")
        assert branch.pinned_at is None
        assert branch.archived_at is None
        assert not Share.objects.filter(target_type=Share.Target.CONVERSATION,
                                        target_key=str(branch.id)).exists()

    def test_the_original_is_left_byte_identical(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b", "c"])
            before = list(conversation.turns.order_by("index")
                          .values_list("index", "text", "state"))
            branch_conversation(user_principal(owner), conversation, turns[1],
                                title="Branch")
            conversation.refresh_from_db()
            after = list(conversation.turns.order_by("index")
                         .values_list("index", "text", "state"))
        assert after == before
        # The parent keeps every turn it had, not merely the same ones in
        # the same order: BRANCH, never rewind.
        assert len(after) == 3

    def test_a_principal_who_may_not_gets_None_and_writes_nothing(self):
        from agents.visibility import branch_conversation

        owner, stranger = make_user(), make_user(username="stranger")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b"])
            before = Conversation.objects.count()
            result = branch_conversation(user_principal(stranger), conversation,
                                         turns[1], title="Branch")
        assert result is None
        assert Conversation.objects.count() == before

    def test_the_cost_is_FLAT_in_the_number_of_turns_copied(self):
        """`bulk_create`, not one write per turn (spec section 8: one
        `.exists()`, one ordered read, one `bulk_create`, one taint read,
        one taint `bulk_create`, all in one transaction).

        NON-VACUOUS ON PURPOSE: two threads of very different lengths,
        compared for EQUALITY. A budget of the form "fewer than N" stays
        green on a per-turn write for every thread shorter than N, which
        is every thread a test would bother to build.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            short, short_turns = self._thread_of(owner, [str(n) for n in range(3)],
                                                 slug="flat-short")
            long_thread, long_turns = self._thread_of(
                owner, [str(n) for n in range(30)], slug="flat-long")
            with CaptureQueriesContext(connection) as two_copied:
                branch_conversation(user_principal(owner), short, short_turns[2],
                                    title="Branch")
            with CaptureQueriesContext(connection) as many_copied:
                branch_conversation(user_principal(owner), long_thread, long_turns[29],
                                    title="Branch")
        assert len(many_copied) == len(two_copied)

    def test_a_threaded_settings_row_is_not_re_read(self):
        """M2. `may_edit_turn` carries `settings_row=` so a caller that
        already holds the singleton need not make the predicate fetch it
        again -- and until `branch_conversation` accepted and threaded
        one, that keyword had no reachable caller and was decorative.

        NON-VACUOUS THE SAME WAY THE TURN PIN IS: the two calls are
        compared for EQUALITY MINUS ONE, the one being the read the
        unthreaded call makes and the threaded one does not. A test that
        only asserted "the threaded call is no worse" would stay green
        if the keyword were dropped on the floor.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from agents.visibility import branch_conversation
        from identity.models import IdentitySettings

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            first, first_turns = self._thread_of(owner, ["a", "b"], slug="row-unthreaded")
            second, second_turns = self._thread_of(owner, ["a", "b"], slug="row-threaded")
            row = IdentitySettings.get_solo()
            with CaptureQueriesContext(connection) as unthreaded:
                branch_conversation(user_principal(owner), first, first_turns[1],
                                    title="Branch")
            with CaptureQueriesContext(connection) as threaded:
                branch_conversation(user_principal(owner), second, second_turns[1],
                                    title="Branch", settings_row=row)
            settings_table = IdentitySettings._meta.db_table
        assert len(threaded) == len(unthreaded) - 1
        assert not [q for q in threaded.captured_queries
                    if settings_table in q["sql"]]

    def test_it_is_callable_with_no_HTTP_anywhere_in_reach(self):
        """Spec review M7. The import-law gate covers the general rule;
        this pins the specific direction the split exists to preserve:
        `agents/visibility.py` imports NOTHING from `agents/chat`, so
        this function cannot call the chat column's turn starter and is
        not a view.

        READ THROUGH `ast`, NOT AS A SUBSTRING OF THE SOURCE. A
        substring grep cannot tell an `import` from a sentence, and this
        module's docstrings NAME the chat column repeatedly -- the rule
        is written down where it binds -- so a whole-source grep is red
        on a module that obeys the rule perfectly.

        WHAT THIS WALK IS AND IS NOT, stated exactly, because the honest
        comparison depends on the baseline. It is STRONGER THAN A
        MODULE-HEADER CHECK: `ast.walk` descends into function bodies,
        so a function-local import cannot slip past it. It is
        PROSE-BLIND where a whole-source grep is not. It is NOT strictly
        stronger than that grep: a dynamic `importlib.import_module(
        "agents.chat.service")` is a string literal, not an `Import`
        node, so this test cannot see it. `foundation/ops/tests/
        test_import_law.py` is the repo-wide backstop for that shape,
        and its own deliberate gap -- relative imports are not matched
        -- is closed here by reading `node.level`.
        """
        import ast
        import inspect

        from agents import visibility

        tree = ast.parse(inspect.getsource(visibility))
        # `from .chat import service` inside `agents/visibility.py` has
        # `module="chat"`, which starts with neither "agents" nor
        # "agents.chat"; resolving it against this module's own package
        # is what closes that hole. A level above 1 walks out of
        # `agents/` entirely and cannot name the chat column at all.
        package = visibility.__package__
        # AND THE RESOLUTION ITSELF IS PINNED (wave r3). A `None` or
        # empty `__package__` collapses `prefix` to `""`, `module`
        # becomes bare `"chat"`, and the `agents.chat` assertion below
        # FAILS OPEN -- this gate would then pass on the very import it
        # exists to catch.
        assert package == "agents", package
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = package if node.level == 1 else ""
                module = ".".join(part for part in (prefix, node.module) if part)
                imported.add(module)
                imported.update(f"{module}.{alias.name}" for alias in node.names)
        assert not [name for name in imported if name.startswith("agents.chat")]
        assert "django.shortcuts" not in imported
        assert not hasattr(visibility, "start_turn")


class TestDuplicateConversationNowCopiesTheAuthor:
    """A DELIBERATE RE-PIN, named in the commit message.
    `agents/runtime/prompt.py::_is_foreign_user_turn` reads `author` to
    fence another person's words in a replay, so a copy that dropped it
    changed how the model was shown a shared conversation's history."""

    def test_the_author_survives_a_duplicate(self):
        from agents.visibility import duplicate_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation = make_conversation(agent=make_agent(slug="dup-author"),
                                             **owner_fields(user_principal(owner)))
            make_turn(conversation=conversation, role=Turn.Role.USER, text="a",
                      state=Turn.State.DONE, author=owner)
            copy = duplicate_conversation(user_principal(owner), conversation,
                                          title="Copy")
        assert copy.turns.get().author_id == owner.pk
