"""`agents/labels.py`'s agent/flow readers, and `agents/visibility.py`'s
one-row lookups `/chat/access/` writes through.

T15 REVIEW FINDING 3: the page's GET used to call `agent_label_ids`/
`flow_label_ids` once PER ROW (an N+1 the tool-label page's own
one-query note already warns against), and its POST used to full-scan
`labellable_agents()`/`labellable_flows()` for a single pk. This module
pins the ONE-QUERY replacement for both sides, mirroring
`agents/tests/test_tool_labels.py::TestReadingLabels` for the identical
shape on the tool side.
"""
from __future__ import annotations

import pytest

from agents.labels import (
    agent_entitlement_ids, flow_entitlement_ids, set_agent_labels, set_flow_labels,
    set_tool_labels,
)
from agents.models import AgentEntitlement, FlowEntitlement, ToolEntitlement
from agents.tests._helpers import (
    make_admin, make_agent, make_entitlement, make_flow, posture, reset_settings,
    seed_sweep_posture, user_principal,
)
from agents.visibility import labellable_agent, labellable_flow
from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestAgentEntitlementIds:
    def test_one_query_maps_every_labelled_agent_to_its_entitlements(
            self, django_assert_num_queries):
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        agent = make_agent()
        AgentEntitlement.objects.create(agent=agent, entitlement=finance)
        AgentEntitlement.objects.create(agent=agent, entitlement=legal)
        with django_assert_num_queries(1):
            mapping = agent_entitlement_ids()
        assert mapping == {agent.pk: frozenset({finance.pk, legal.pk})}

    def test_an_unlabelled_install_maps_nothing(self):
        assert agent_entitlement_ids() == {}


class TestFlowEntitlementIds:
    def test_one_query_maps_every_labelled_flow_to_its_entitlements(
            self, django_assert_num_queries):
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        flow = make_flow()
        FlowEntitlement.objects.create(flow=flow, entitlement=finance)
        FlowEntitlement.objects.create(flow=flow, entitlement=legal)
        with django_assert_num_queries(1):
            mapping = flow_entitlement_ids()
        assert mapping == {flow.pk: frozenset({finance.pk, legal.pk})}

    def test_an_unlabelled_install_maps_nothing(self):
        assert flow_entitlement_ids() == {}


class TestLabellableAgentAndFlow:
    """The POST-side one-row readers -- `agents/chat/views/access.py::
    _save` resolves its target through these instead of scanning
    `labellable_agents()`/`labellable_flows()` for a match."""

    def test_labellable_agent_finds_the_row_in_one_query(self, django_assert_num_queries):
        agent = make_agent()
        with django_assert_num_queries(1):
            found = labellable_agent(agent.pk)
        assert found == agent

    def test_labellable_agent_answers_none_for_an_unknown_pk(self):
        assert labellable_agent(424242424) is None

    def test_labellable_flow_finds_the_row_in_one_query(self, django_assert_num_queries):
        flow = make_flow()
        with django_assert_num_queries(1):
            found = labellable_flow(flow.pk)
        assert found == flow

    def test_labellable_flow_answers_none_for_an_unknown_pk(self):
        assert labellable_flow(424242424) is None


class TestWritingAgentLabels:
    """Mirrors `agents/tests/test_tool_labels.py::TestWritingLabels` for
    `set_agent_labels` -- the tool path's own coverage of the shared
    diff-and-audit shape, on the agent side."""

    def test_setting_labels_writes_the_difference_and_audits_both_directions(self):
        admin = make_admin()
        agent = make_agent()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_agent_labels(actor, agent, {finance.pk, legal.pk})
            assert AuditEvent.objects.filter(action=actions.AGENT_LABELLED).count() == 2
            set_agent_labels(actor, agent, {finance.pk})
        assert set(AgentEntitlement.objects.values_list("entitlement_id", flat=True)) == {
            finance.pk}
        assert AuditEvent.objects.filter(action=actions.AGENT_UNLABELLED).count() == 1

    def test_setting_the_same_labels_twice_writes_no_second_event(self):
        admin = make_admin()
        agent = make_agent()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_agent_labels(actor, agent, {finance.pk})
            set_agent_labels(actor, agent, {finance.pk})
        assert AuditEvent.objects.filter(action=actions.AGENT_LABELLED).count() == 1


class TestWritingFlowLabels:
    """Mirrors `agents/tests/test_tool_labels.py::TestWritingLabels` for
    `set_flow_labels` -- the tool path's own coverage of the shared
    diff-and-audit shape, on the flow side."""

    def test_setting_labels_writes_the_difference_and_audits_both_directions(self):
        admin = make_admin()
        flow = make_flow()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_flow_labels(actor, flow, {finance.pk, legal.pk})
            assert AuditEvent.objects.filter(action=actions.FLOW_LABELLED).count() == 2
            set_flow_labels(actor, flow, {finance.pk})
        assert set(FlowEntitlement.objects.values_list("entitlement_id", flat=True)) == {
            finance.pk}
        assert AuditEvent.objects.filter(action=actions.FLOW_UNLABELLED).count() == 1

    def test_setting_the_same_labels_twice_writes_no_second_event(self):
        admin = make_admin()
        flow = make_flow()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_flow_labels(actor, flow, {finance.pk})
            set_flow_labels(actor, flow, {finance.pk})
        assert AuditEvent.objects.filter(action=actions.FLOW_LABELLED).count() == 1


# `setter` below is called with `(actor, target, entitlement_ids)` for
# every kind -- `set_tool_labels(actor, tool_key, ids)`,
# `set_agent_labels(actor, agent, ids)`, `set_flow_labels(actor, flow, ids)`
# all share that positional shape, only the middle argument's TYPE differs.
_SETTERS = {
    "set_tool_labels": set_tool_labels,
    "set_agent_labels": set_agent_labels,
    "set_flow_labels": set_flow_labels,
}


def _target_for(target_type: str):
    """`(target, expected_target_key, expected_target_label)` for one
    writer kind -- the exact spelling `/chat/access/` reads off the
    audit row for that kind."""
    if target_type == "tool":
        return "rag.search", "rag.search", "rag.search"
    if target_type == "agent":
        agent = make_agent()
        return agent, str(agent.pk), agent.slug
    flow = make_flow()
    return flow, str(flow.pk), flow.slug


class TestEachWriterAuditsOnlyTheDifferenceWithItsOwnRowShape:
    @pytest.mark.parametrize(("setter_name", "target_type"), [
        ("set_tool_labels", "tool"),
        ("set_agent_labels", "agent"),
        ("set_flow_labels", "flow"),
    ])
    def test_each_writer_audits_only_the_difference_with_its_own_row_shape(
            self, setter_name, target_type):
        """C-28. The three writers share a shape and MUST NOT share their
        audit vocabulary: an operator reading `/chat/access/` sees
        `target_type`, `target_key` and `target_label` spelled per kind. This
        is the pin that makes the extraction safe, so write it first and read
        the real spellings out of the current code rather than guessing."""
        setter = _SETTERS[setter_name]
        target, expected_key, expected_label = _target_for(target_type)
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            setter(actor, target, {finance.pk, legal.pk})
            setter(actor, target, {finance.pk})

        expected_actions = {
            "tool": (actions.TOOL_LABELLED, actions.TOOL_UNLABELLED),
            "agent": (actions.AGENT_LABELLED, actions.AGENT_UNLABELLED),
            "flow": (actions.FLOW_LABELLED, actions.FLOW_UNLABELLED),
        }[target_type]
        labelled_action, unlabelled_action = expected_actions

        labelled = list(AuditEvent.objects.filter(target_type=target_type,
                                                    action=labelled_action))
        unlabelled = list(AuditEvent.objects.filter(target_type=target_type,
                                                      action=unlabelled_action))

        assert len(labelled) == 2
        assert len(unlabelled) == 1
        assert {row.detail["entitlement_id"] for row in labelled} == {finance.pk, legal.pk}
        for row in labelled:
            assert row.target_key == expected_key
            assert row.target_label == expected_label
        [removed] = unlabelled
        assert removed.target_key == expected_key
        assert removed.target_label == expected_label
        assert removed.detail["entitlement_id"] == legal.pk
