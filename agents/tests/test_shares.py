"""`agents/shares.py` -- the two small queries every visibility body
needs, and the parser that keeps one bad row from 500-ing a page.
"""
from __future__ import annotations

import pytest

from agents.models import Share
from agents.shares import share_level, shared_keys
from agents.tests._helpers import (
    make_agent, make_conversation, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestSharedKeys:
    def test_a_direct_share_and_a_group_share_both_reach(self):
        user = make_user()
        group = make_group()
        user.groups.add(group)
        # A SINGLE shared agent for both conversations: `make_agent()`'s
        # own docstring documents a fixed default slug and says to pass
        # one explicitly for a second agent -- this test needs two
        # CONVERSATIONS, not two agents, so it reuses one rather than
        # tripping that documented contract.
        agent = make_agent()
        one = make_conversation(agent=agent)
        two = make_conversation(agent=agent)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(one.pk), user=user)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(two.pk), group=group)
        with posture(POSTURE_ENTERPRISE):
            keys = shared_keys(Share.Target.CONVERSATION, user_principal(user))
        assert set(keys) == {str(one.pk), str(two.pk)}

    def test_a_non_user_principal_reaches_nothing(self):
        conversation = make_conversation()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=make_user())
        with posture(POSTURE_ENTERPRISE):
            assert shared_keys(Share.Target.CONVERSATION, SERVICE_PRINCIPAL) == []
            assert shared_keys(Share.Target.CONVERSATION, OPEN_PRINCIPAL) == []

    def test_an_unparseable_key_is_dropped_rather_than_reaching_the_queryset(self):
        """The SECOND half of the two-sided rule. `Share.save()` refuses
        such a key on the way in; this drops one that arrived from a
        shell or an older schema on the way out. Without it,
        `Q(pk__in=[...])` raises INSIDE a listing queryset and turns a
        page into a 500 -- a never-500 violation reachable by one bad
        row."""
        user = make_user()
        conversation = make_conversation()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=user)
        # Written past `save()` deliberately, exactly as a shell would.
        Share.objects.filter(user=user).update(target_key="not-a-uuid")
        with posture(POSTURE_ENTERPRISE):
            assert shared_keys(Share.Target.CONVERSATION, user_principal(user)) == []


class TestShareLevel:
    def test_the_widest_level_wins_when_two_shares_reach_one_row(self):
        """A person can be reached by a direct `view` share and a group
        `use` share at once. The answer has to be one level, and the
        honest one is the WIDEST -- anything else would mean adding
        somebody to a group silently REMOVED an ability."""
        user = make_user()
        group = make_group()
        user.groups.add(group)
        conversation = make_conversation()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=user,
                             level=Share.Level.VIEW)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), group=group,
                             level=Share.Level.USE)
        with posture(POSTURE_ENTERPRISE):
            assert share_level(user_principal(user), Share.Target.CONVERSATION,
                               str(conversation.pk)) == Share.Level.USE

    def test_no_share_answers_none(self):
        conversation = make_conversation()
        with posture(POSTURE_ENTERPRISE):
            assert share_level(user_principal(make_user()), Share.Target.CONVERSATION,
                               str(conversation.pk)) is None
