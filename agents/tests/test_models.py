"""Field wiring, and the three rules that are not just field wiring.

Rule 1 (ruling R1, spec section 7.1): `Agent.save()` REJECTS a tool key
whose registered spec is `mutates=True`, and ACCEPTS-and-logs a key that
is not registered at all. The asymmetry is the whole point. A mutating
tool is registered but not grantable before Identity & Auth
(ADR 0010:266-276) and a row that granted one must never save. An
UNREGISTERED key is a not-yet or a not-here -- `general` grants tools
that exist only when the vision feature flag is on -- and rejecting it
would make installing that shipped default fail on a legal install.

Rule 2 (RULING 3, 2026-08-28 -- retires spec section 7.5's lock): a
`resident=True` row is an ORIGIN MARKER, not a lock. It is freely
editable and deletable like any other row; the one piece of the old
lock that survives is that `slug` is immutable, on every `Agent`.

Rule 3 (spec section 7.3): `Turn.tool_call` carries all five keys or is
null. `"discarded"` is never OMITTED -- a missing key and an empty list
must not both mean "nothing was discarded".
"""
from __future__ import annotations

import logging
import uuid

import pytest
from django.db import IntegrityError

from agents.contracts.tools import ToolSpec, register_tool
from agents.models import Agent, ToolInvocation, Turn
from agents.tests._helpers import (  # noqa: F401
    isolated_tool_registry, make_agent, make_conversation, make_flow, make_turn,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]


class TestAgentToolKeyValidation:
    def test_a_registered_mutating_tool_key_is_rejected_by_name(self):
        register_tool(ToolSpec(
            key="stub.mutating", label="Stub", description="d",
            runner="agents.tests._helpers.stub_runner", mutates=True,
        ))
        with pytest.raises(ValueError) as exc:
            make_agent(tool_keys=["stub.mutating"])
        assert "stub.mutating" in str(exc.value)

    def test_an_unregistered_tool_key_is_accepted_and_written_verbatim(self, caplog):
        # `caplog` captures at WARNING by default, so an assertion on an
        # INFO line passes vacuously without this -- it would pass just
        # as happily if the log call were deleted. The logger name is
        # the module's own, so a stray INFO from anywhere else cannot
        # satisfy the assertion either.
        with caplog.at_level(logging.INFO, logger="agents.models"):
            agent = make_agent(tool_keys=["not.registered.anywhere"])
        agent.refresh_from_db()
        assert agent.tool_keys == ["not.registered.anywhere"]
        assert "not.registered.anywhere" in caplog.text

    def test_a_registered_non_mutating_key_is_accepted_silently(self, caplog):
        register_tool(ToolSpec(
            key="stub.safe", label="Stub", description="d",
            runner="agents.tests._helpers.stub_runner",
        ))
        with caplog.at_level(logging.INFO, logger="agents.models"):
            make_agent(tool_keys=["stub.safe"])
        assert "stub.safe" not in caplog.text

    def test_tool_keys_must_be_a_list_of_strings(self):
        with pytest.raises(ValueError):
            make_agent(tool_keys={"not": "a list"})


class TestAgentOwnership:
    """RULING 3 (2026-08-28): a shipped default is a STARTING POINT an
    operator adopts, not a mirror the platform maintains.

    P2 held the opposite -- `resident=True` locked every field and
    `delete()` refused outright, because a resident row was a
    projection of `agents/resident.py` and a drifted row would make the
    code lie about what was running. The owner's ruling replaces that
    model: `resident` is now an ORIGIN MARKER ("this row started as a
    shipped default"), the operator owns the row, and `install_defaults
    --reset <slug>` is how the original comes back -- a deliberate act
    with the shipped text in front of them.
    """

    def test_a_row_that_came_from_a_shipped_default_is_freely_editable(self):
        agent = make_agent(resident=True)
        agent.system_prompt = "my own words"
        agent.save()
        agent.refresh_from_db()
        assert agent.system_prompt == "my own words"
        # And it still remembers where it came from.
        assert agent.resident is True

    def test_a_row_that_came_from_a_shipped_default_is_deletable(self):
        """P2 refused this because `Conversation.agent` is PROTECT and a
        delete would orphan history. PROTECT still holds -- a row with
        conversations still cannot be deleted, and that is the DATABASE
        saying so, which is the honest place for it."""
        make_agent(resident=True, slug="scratch").delete()
        assert Agent.objects.filter(slug="scratch").count() == 0

    def test_an_agent_with_conversations_still_cannot_be_deleted(self):
        """The guard that was doing the real work all along.

        `from django.db.models.deletion import ProtectedError` -- it is
        not in `django.db.models`' top level namespace, and importing it
        from the wrong place is the kind of error a test file gets away
        with until the first red run.
        """
        from django.db.models.deletion import ProtectedError

        agent = make_agent(resident=True)
        make_conversation(agent=agent)
        with pytest.raises(ProtectedError):
            agent.delete()

    def test_the_slug_is_immutable(self):
        """The ONE piece of the lock that survives. A slug is what
        `flow.run` resolves, what an `agent.<slug>` grant names, and
        what `--reset` matches; renaming one in place would silently
        repoint every reference to it."""
        agent = make_agent(slug="general")
        agent.slug = "renamed"
        with pytest.raises(ValueError) as exc:
            agent.save()
        assert "general" in str(exc.value) and "renamed" in str(exc.value)

    def test_a_flows_slug_is_immutable_too(self):
        """CQ-10: `Flow.save()` shares `Agent.save()`'s slug-immutability
        check (`_refuse_slug_change`) rather than carrying its own copy
        of the same eleven lines -- this pins that the shared helper
        still refuses a flow's rename, naming both slugs."""
        flow = make_flow(slug="original")
        flow.slug = "renamed"
        with pytest.raises(ValueError) as exc:
            flow.save()
        assert "original" in str(exc.value) and "renamed" in str(exc.value)

    def test_from_resident_sync_is_gone(self):
        """Anti-vacuous pin on the removal itself: a bypass keyword left
        in place would be a second, invisible way to save."""
        with pytest.raises(TypeError):
            make_agent().save(_from_resident_sync=True)

    def test_a_new_agent_records_its_owner(self):
        agent = make_agent(owner_kind="open", owner_key="box")
        assert (agent.owner_kind, agent.owner_key) == ("open", "box")


class TestConversationAndTurn:
    def test_conversation_gets_a_uuid_primary_key(self):
        assert isinstance(make_conversation().id, uuid.UUID)

    def test_conversation_owner_is_blank_by_default_and_filterable_when_set(self):
        from agents.models import Conversation

        agent = make_agent()
        unowned = make_conversation(agent=agent)
        owned = make_conversation(agent=agent, owner_kind="user", owner_key="alice")
        assert unowned.owner_kind == ""
        assert unowned.owner_key == ""
        assert list(
            Conversation.objects.filter(owner_kind="user", owner_key="alice")
        ) == [owned]

    def test_a_conversation_is_active_until_it_is_archived(self):
        """UI-3b. `archived_at` is a TIMESTAMP, not a boolean: `null`
        means active and a value records WHEN it left the list. The
        sidebar's two lists are this column's two sides."""
        from django.utils import timezone

        from agents.models import Conversation

        agent = make_agent()
        active = make_conversation(agent=agent)
        archived = make_conversation(agent=agent, archived_at=timezone.now())
        assert active.archived_at is None
        assert list(Conversation.objects.filter(archived_at__isnull=True)) == [active]
        assert list(Conversation.objects.filter(archived_at__isnull=False)) == [archived]

    def test_the_title_column_width_matches_the_constant_chat_truncates_to(self):
        """`agents.chat.service.TITLE_COLUMN_MAX` is a HAND-COPIED 255:
        no module under `agents/chat` may import `agents.models`
        (ruling 4c), so the rename and duplicate views cannot read the
        field's own `max_length`. This is the pin that keeps the copy
        from drifting -- widen the column and this test names the
        constant to widen with it."""
        from agents.chat.service import TITLE_COLUMN_MAX
        from agents.models import Conversation

        assert Conversation._meta.get_field(
            "title").max_length == TITLE_COLUMN_MAX

    def test_an_agent_with_a_conversation_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        agent = make_agent(slug="protected", resident=False)
        make_conversation(agent=agent)
        with pytest.raises(ProtectedError):
            agent.delete()

    def test_next_index_starts_at_zero_and_then_increments(self):
        conv = make_conversation()
        assert Turn.next_index(conv) == 0
        make_turn(conversation=conv, index=0)
        assert Turn.next_index(conv) == 1

    def test_two_turns_cannot_share_an_index_in_one_conversation(self):
        from django.db import IntegrityError

        conv = make_conversation()
        make_turn(conversation=conv, index=0)
        with pytest.raises(IntegrityError):
            make_turn(conversation=conv, index=0)

    def test_turns_order_by_index(self):
        conv = make_conversation()
        make_turn(conversation=conv, index=1, text="second")
        make_turn(conversation=conv, index=0, text="first")
        assert [t.text for t in conv.turns.all()] == ["first", "second"]

    def test_a_tool_turn_carries_all_five_tool_call_keys(self):
        conv = make_conversation()
        turn = make_turn(
            conversation=conv, index=0, role=Turn.Role.TOOL,
            tool_call={"tool": "rag.search", "args": {"query": "q"},
                       "agent": "general", "id": "", "discarded": []},
        )
        turn.refresh_from_db()
        assert set(turn.tool_call) == {"tool", "args", "agent", "id", "discarded"}

    def test_queue_job_id_is_a_plain_integer_not_a_foreign_key(self):
        """`agents/` may not import `models.queue` (import-law rule 2) --
        the same call `tools/vision/models.py:135-143` records for
        `GenerationJob.queue_job_id`."""
        field = Turn._meta.get_field("queue_job_id")
        assert field.get_internal_type() == "BigIntegerField"
        assert not field.is_relation


class TestToolInvocation:
    def test_a_turn_references_an_invocation_and_survives_losing_it(self):
        conv = make_conversation()
        inv = ToolInvocation.objects.create(
            principal_kind="resident_agent", principal_key="general",
            tool_key="rag.search", args={"query": "q"},
            outcome=ToolInvocation.Outcome.OK, text="ok",
        )
        turn = make_turn(conversation=conv, index=0, role=Turn.Role.TOOL, invocation=inv)
        inv.delete()
        turn.refresh_from_db()
        assert turn.invocation_id is None
        assert Turn.objects.filter(pk=turn.pk).exists()

    def test_every_outcome_class_the_addendum_names_is_a_choice(self):
        assert {value for value, _ in ToolInvocation.Outcome.choices} == {
            "ok", "refused", "param_error", "error", "degraded",
        }

    def test_duration_ms_is_none_until_the_call_finishes(self):
        inv = ToolInvocation.objects.create(
            principal_kind="api_client", principal_key="k",
            tool_key="rag.search", args={}, outcome=ToolInvocation.Outcome.OK,
        )
        assert inv.duration_ms is None


class TestToolEntitlement:
    """A tool label is a STRING plus an entitlement -- tools are
    code-registered and there is no tool table to point at, the same
    reason `Turn.queue_job_id` is a plain integer."""

    def test_one_label_per_tool_and_entitlement(self):
        from agents.models import ToolEntitlement
        from agents.tests._helpers import make_entitlement
        finance = make_entitlement(name="Finance")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        with pytest.raises(IntegrityError):
            ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)

    def test_a_key_not_registered_on_this_install_is_tolerated(self):
        """Exactly as `Agent.tool_keys` tolerates one: a feature-gated
        tool with its flag off is a row that names a key this box does
        not have, and refusing it would make a label depend on which
        features happened to be on when it was written."""
        from agents.models import ToolEntitlement
        from agents.tests._helpers import make_entitlement
        row = ToolEntitlement.objects.create(tool_key="not.registered.anywhere",
                                             entitlement=make_entitlement())
        assert row.pk


class TestShare:
    """A CONVERSATION'S KEY IS A UUID, so every conversation-target row
    below carries a real one. `Share.save()` parses `target_key` with the
    TARGET'S OWN parser before the database ever sees the row, so
    `target_key="1"` on a conversation share raises `ValueError` -- not
    the constraint violation these tests are about."""

    def test_a_share_naming_both_a_user_and_a_group_is_refused(self):
        import uuid
        from agents.models import Share
        from agents.tests._helpers import make_group, make_user
        with pytest.raises(IntegrityError):
            Share.objects.create(target_type=Share.Target.CONVERSATION,
                                 target_key=str(uuid.uuid4()),
                                 user=make_user(), group=make_group())

    def test_a_share_naming_neither_is_refused(self):
        import uuid
        from agents.models import Share
        with pytest.raises(IntegrityError):
            Share.objects.create(target_type=Share.Target.CONVERSATION,
                                 target_key=str(uuid.uuid4()))

    def test_one_share_per_target_and_user(self):
        import uuid
        from agents.models import Share
        from agents.tests._helpers import make_user
        user = make_user()
        key = str(uuid.uuid4())
        Share.objects.create(target_type=Share.Target.CONVERSATION, target_key=key,
                             user=user)
        with pytest.raises(IntegrityError):
            Share.objects.create(target_type=Share.Target.CONVERSATION, target_key=key,
                                 user=user)

    def test_save_refuses_a_target_key_the_targets_own_parser_rejects(self):
        """The FIRST half of the two-sided rule. A conversation's primary
        key is a UUID; a `target_key` that is not a parseable one would
        make `Q(pk__in=[...])` raise inside a listing queryset -- a 500
        on a never-500 surface, reachable by one bad row. The second half
        drops such a row on the way OUT (`agents/shares.py`), because a
        row can also arrive from a shell or an older schema."""
        from agents.models import Share
        from agents.tests._helpers import make_user
        with pytest.raises(ValueError):
            Share.objects.create(target_type=Share.Target.CONVERSATION,
                                 target_key="not-a-uuid", user=make_user())
        with pytest.raises(ValueError):
            Share.objects.create(target_type=Share.Target.AGENT,
                                 target_key="not-an-int", user=make_user())

    def test_deleting_the_group_takes_its_shares(self):
        from agents.models import Share
        from agents.tests._helpers import make_group
        group = make_group()
        Share.objects.create(target_type=Share.Target.AGENT, target_key="1", group=group)
        group.delete()
        assert Share.objects.count() == 0


class TestToolInvocationRecordsTheAgent:
    def test_the_column_exists_and_defaults_blank(self):
        """`principal` is WHO this was done for; `agent_slug` is WHOSE
        TOOL DECLARATION was in force. Without this column the acting
        rule would LOSE the fact that an agent made the call, which is
        the fact an operator most wants when reading the trail."""
        from agents.models import ToolInvocation
        row = ToolInvocation.objects.create(
            principal_kind="user", principal_key="1", tool_key="rag.search",
            outcome=ToolInvocation.Outcome.OK)
        assert row.agent_slug == ""


class TestAgentAndFlowEntitlements:
    def test_one_label_per_agent_and_entitlement(self):
        from django.db.utils import IntegrityError
        from agents.models import AgentEntitlement
        from agents.tests._helpers import make_agent, make_entitlement
        agent = make_agent()
        finance = make_entitlement(name="Finance")
        AgentEntitlement.objects.create(agent=agent, entitlement=finance)
        with pytest.raises(IntegrityError):
            AgentEntitlement.objects.create(agent=agent, entitlement=finance)

    def test_deleting_the_agent_takes_its_labels(self):
        from agents.models import AgentEntitlement
        from agents.tests._helpers import make_agent, make_entitlement
        agent = make_agent()
        AgentEntitlement.objects.create(agent=agent, entitlement=make_entitlement())
        agent.delete()
        assert AgentEntitlement.objects.count() == 0

    def test_a_flow_carries_its_own_table(self):
        """TWO TABLES, not one polymorphic row: `Agent` and `Flow` are two
        models with two primary keys, read by two functions that each
        already know which model they are filtering."""
        from agents.models import FlowEntitlement
        from agents.tests._helpers import make_entitlement, make_flow
        flow = make_flow()
        FlowEntitlement.objects.create(flow=flow, entitlement=make_entitlement())
        assert FlowEntitlement.objects.count() == 1
