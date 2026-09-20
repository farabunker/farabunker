"""The on-timeout admin hint (one-timeout task, 2026-09-17) and I1's
preserved partial text -- split out of `test_thread.py` (C-56, the
2,100-line test-module split threshold, `foundation/ops/tests/
test_column_boundaries.py::test_no_test_module_grows_past_the_split_
threshold`) once this task's own fix rounds pushed that file two lines
past it. A cohesive, self-contained topic -- everything here is about
what a FAILED turn's own card shows an admin versus a member -- rather
than a file split mid-topic, so it earns its own module instead of a
dated exemption entry.

NO FARABUNKER_FEATURES OVERRIDE, matching `test_thread.py`'s own module
docstring (`test_mount.py`).
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401
    make_admin, make_agent, make_conversation, make_turn, make_user, posture, sign_in,
    user_principal,
)
from agents.models import Turn
from agents.visibility import create_conversation
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


class TestOnTimeoutAdminHint:
    """The one-timeout task (2026-09-17): a turn that failed because it
    hit its own response timeout carries the ONE shared, generic
    sentence (`agents.runtime.loop.TURN_TIMEOUT_ERROR`) in `Turn.error`,
    and an administrator viewing it gets an extra hint pointing at the
    Job execution settings page -- the content-withheld pattern, gated
    on `identity_is_admin` exactly like `test_thread.py::
    TestAgentNotPermittedBanner`'s own admin-vs-member split."""

    def test_an_admin_sees_the_settings_hint_on_a_timed_out_turn(self, client):
        from agents.runtime.loop import TURN_TIMEOUT_ERROR

        admin = make_admin()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(admin), agent)
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error=TURN_TIMEOUT_ERROR)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])).content.decode()
        assert TURN_TIMEOUT_ERROR in body
        assert reverse("jobs-settings") in body

    def test_a_member_sees_no_settings_hint_on_the_identical_turn(self, client):
        """MINOR 5 (fix round 1): scoped to the THREAD region, not the
        whole body -- the settings-assistant panel can render its own
        `assistant.links` from the same help-card table that now carries
        a `jobs-settings` card, which would make an unscoped negative
        assertion one opened panel away from being vacuous (the same
        reasoning `test_thread.py::test_a_cancelled_turn_carries_no_
        model_setup_link` already applies to `inference-console`)."""
        from agents.runtime.loop import TURN_TIMEOUT_ERROR

        member = make_user()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(member), agent)
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error=TURN_TIMEOUT_ERROR)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])).content.decode()
        assert TURN_TIMEOUT_ERROR in body
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert reverse("jobs-settings") not in thread_region

    def test_an_admin_sees_no_hint_when_the_failure_is_a_different_error(self, client):
        """The hint is keyed to the ONE shared constant, never to the
        FAILED state alone -- a genuinely different failure (a stalled
        worker, a crash) never grows an admin hint that has nothing to
        do with the response timeout. Scoped to the thread region for
        the same reason as the sibling test above (MINOR 5)."""
        admin = make_admin()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(admin), agent)
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error="the worker stopped responding")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])).content.decode()
        thread_region = body[body.index('class="thread"'):body.index('id="turn-errors"')]
        assert reverse("jobs-settings") not in thread_region

    def test_a_timed_out_turns_partial_text_is_preserved_above_the_error(self, client):
        """I1 (fix round 1): PRESERVE, don't discard. A timed-out turn's
        own composed text -- `_finish` still writes it verbatim into
        `Turn.text` -- is a timed-out turn's ONLY FAILED path that ever
        populates `Turn.text` at all; every other FAILED reason (a crash,
        a stalled-worker writeback) leaves it blank, so this must not
        become a blanket "show the text on any failure" change."""
        from agents.runtime.loop import TURN_TIMEOUT_ERROR

        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error=TURN_TIMEOUT_ERROR,
                  text="This turn hit its time limit before I reached an answer.")
        body = client.get(
            reverse("chat-conversation", args=[conversation.pk])).content.decode()
        assert "This turn hit its time limit before I reached an answer." in body
        assert body.index("This turn hit its time limit") < body.index(TURN_TIMEOUT_ERROR)

    def test_a_blank_text_failed_turn_renders_no_empty_text_block(self, client):
        """The OTHER failure shape (a crash, a stalled worker): `Turn.
        text` is blank, and the new preservation block must be a silent
        no-op for it -- no stray empty `<div class="turn-text">`."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error="the worker stopped responding")
        body = client.get(
            reverse("chat-conversation", args=[conversation.pk])).content.decode()
        assert 'class="turn-text"' not in body
