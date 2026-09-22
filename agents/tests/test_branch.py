"""Edit a past prompt: the provenance columns, the gate, and what a
branch is made of."""
from __future__ import annotations

import pytest

from agents.models import Conversation, Turn
from agents.tests._helpers import make_agent, make_conversation, make_turn

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
