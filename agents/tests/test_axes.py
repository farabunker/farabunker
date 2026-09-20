"""This column's three entitlement axes: registered from `ready()`, and
writing through `agents/labels.py` rather than around it.

The registration tests use the same ANTI-VACUOUS pop-first shape
`TestReadyRegistersTheToolLabelsCascade` in `agents/tests/test_apps.py`
uses: clear the key, prove it is gone, run `ready()` again, prove it came
back -- so passing means `ready()` put it there rather than some earlier
registration this pytest process already ran.
"""
from __future__ import annotations

import pytest
from django.apps import apps

from agents.axes import (
    PAGE_ONLY_NOTE, agent_counts, agent_ids_for, agent_rows, flow_counts,
    flow_ids_for, flow_rows, set_agent_axis, set_flow_axis, set_tool_axis,
    tool_counts, tool_ids_for, tool_rows,
)
from agents.models import AgentEntitlement, FlowEntitlement, ToolEntitlement
from agents.tests._helpers import make_agent, make_flow
from identity.contracts import actions
from identity.contracts import axes as axes_module
from identity.contracts.axes import AxisRow
from identity.models import AuditEvent
from identity.testing import make_entitlement, make_user, user_principal

pytestmark = pytest.mark.django_db


def _actions_for(action: str) -> list[str]:
    return list(AuditEvent.objects.filter(action=action)
                .values_list("target_key", flat=True))


class TestReadyRegistersTheThreeAxes:
    @pytest.mark.parametrize("key,label,order", [
        ("agents.tools", "Tools", 10),
        ("agents.agents", "Agents", 20),
        ("agents.flows", "Flows", 30),
    ])
    def test_ready_registers_it(self, key, label, order):
        apps.get_app_config("agents").ready()
        axes_module._AXES.pop(key, None)
        assert key not in {spec.key for spec in axes_module.all_entitlement_axes()}

        apps.get_app_config("agents").ready()
        by_key = {spec.key: spec for spec in axes_module.all_entitlement_axes()}
        assert key in by_key
        assert (by_key[key].label, by_key[key].order) == (label, order)
        assert by_key[key].editable is True

    def test_running_ready_twice_registers_three_axes_not_six(self):
        apps.get_app_config("agents").ready()
        apps.get_app_config("agents").ready()
        keys = [spec.key for spec in axes_module.all_entitlement_axes()]
        for key in ("agents.tools", "agents.agents", "agents.flows"):
            assert keys.count(key) == 1

    def test_every_registered_handler_really_resolves(self):
        """The registration is a STRING, so a renamed function is caught
        at import time by nothing at all -- `identity/cascades.py`'s own
        docstring says a registration that landed before its module makes
        every entitlement page raise. This resolves all four paths of all
        three axes."""
        from django.utils.module_loading import import_string

        apps.get_app_config("agents").ready()
        for spec in axes_module.all_entitlement_axes():
            if not spec.key.startswith("agents."):
                continue
            for path in (spec.counts, spec.rows, spec.ids_for, spec.set_for):
                assert callable(import_string(path)), path


class TestTheToolAxis:
    def test_the_catalogue_marks_the_tools_no_agent_can_be_granted(self):
        """LABELLABLE IS NOT GRANTABLE (`agents/chat/views/tools.py`'s
        own note). A mutating tool can still have a page of its own, and
        a label there narrows who reaches it -- so it is in the
        catalogue, marked."""
        from agents.contracts.tools import all_tools, grantable_tools

        grantable = {spec.key for spec in grantable_tools()}
        rows = tool_rows()
        assert [row.id for row in rows] == [spec.key for spec in all_tools()]
        page_only = {row.id for row in rows if row.note == PAGE_ONLY_NOTE}
        assert page_only == {spec.key for spec in all_tools()} - grantable
        assert page_only, "a box with no page-only tool makes this vacuous"

    def test_active_ids_and_counts_read_the_table(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        ToolEntitlement.objects.create(tool_key="rag.ingest", entitlement=finance)
        assert tool_ids_for(finance.pk) == {"rag.search", "rag.ingest"}
        assert tool_ids_for(legal.pk) == set()
        assert tool_counts() == {finance.pk: 2}

    def test_adding_and_removing_go_through_the_labels_writer_and_audit(self):
        """The whole reason this column registers dotted paths rather
        than letting `identity/` write the join table: `/chat/access/`
        reads this trail."""
        actor = user_principal(make_user())
        finance = make_entitlement(name="Finance")
        added = set_tool_axis(finance.pk, add={"rag.search"}, remove=set(), actor=actor)
        assert added == {"added": 1, "removed": 0}
        assert _actions_for(actions.TOOL_LABELLED) == ["rag.search"]

        removed = set_tool_axis(finance.pk, add=set(), remove={"rag.search"},
                                actor=actor)
        assert removed == {"added": 0, "removed": 1}
        assert _actions_for(actions.TOOL_UNLABELLED) == ["rag.search"]
        assert not ToolEntitlement.objects.filter(entitlement=finance).exists()

    def test_another_entitlements_label_on_the_same_tool_survives_a_remove(self):
        """THE POINT OF ADD/REMOVE RATHER THAN A WHOLE WANTED SET. The
        column's writer takes one tool's FULL entitlement set, so this
        axis has to hand it the set it already has, minus one -- not the
        one entitlement being edited."""
        actor = user_principal(make_user())
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        set_tool_axis(finance.pk, add={"rag.search"}, remove=set(), actor=actor)
        set_tool_axis(legal.pk, add={"rag.search"}, remove=set(), actor=actor)
        set_tool_axis(finance.pk, add=set(), remove={"rag.search"}, actor=actor)
        assert tool_ids_for(legal.pk) == {"rag.search"}
        assert tool_ids_for(finance.pk) == set()

    def test_a_no_op_submission_writes_nothing_and_says_so(self):
        actor = user_principal(make_user())
        finance = make_entitlement(name="Finance")
        set_tool_axis(finance.pk, add={"rag.search"}, remove=set(), actor=actor)
        AuditEvent.objects.all().delete()
        again = set_tool_axis(finance.pk, add={"rag.search"}, remove=set(), actor=actor)
        assert again == {"added": 0, "removed": 0}
        assert AuditEvent.objects.count() == 0


class TestTheAgentAndFlowAxes:
    def test_agent_rows_carry_the_pk_as_the_id_and_the_slug_as_the_note(self):
        agent = make_agent(slug="alpha", name="Alpha")
        assert agent_rows() == [AxisRow(str(agent.pk), "Alpha", "alpha")]

    def test_agent_add_and_remove_audit_through_the_labels_writer(self):
        actor = user_principal(make_user())
        agent = make_agent(slug="alpha", name="Alpha")
        finance = make_entitlement(name="Finance")
        assert set_agent_axis(finance.pk, add={str(agent.pk)}, remove=set(),
                              actor=actor) == {"added": 1, "removed": 0}
        assert agent_ids_for(finance.pk) == {str(agent.pk)}
        assert agent_counts() == {finance.pk: 1}
        assert _actions_for(actions.AGENT_LABELLED) == [str(agent.pk)]
        assert set_agent_axis(finance.pk, add=set(), remove={str(agent.pk)},
                              actor=actor) == {"added": 0, "removed": 1}
        assert not AgentEntitlement.objects.filter(entitlement=finance).exists()

    def test_flow_add_and_remove_audit_through_the_labels_writer(self):
        actor = user_principal(make_user())
        flow = make_flow(slug="beta", name="Beta")
        finance = make_entitlement(name="Finance")
        assert [row.id for row in flow_rows()] == [str(flow.pk)]
        assert set_flow_axis(finance.pk, add={str(flow.pk)}, remove=set(),
                             actor=actor) == {"added": 1, "removed": 0}
        assert flow_ids_for(finance.pk) == {str(flow.pk)}
        assert flow_counts() == {finance.pk: 1}
        assert _actions_for(actions.FLOW_LABELLED) == [str(flow.pk)]
        assert set_flow_axis(finance.pk, add=set(), remove={str(flow.pk)},
                             actor=actor) == {"added": 0, "removed": 1}
        assert not FlowEntitlement.objects.filter(entitlement=finance).exists()

    def test_a_row_deleted_between_render_and_submit_is_a_no_op(self):
        """`identity.axes.apply_axis` has already refused every id
        outside the catalogue, so a miss here is a row that went away --
        not something an operator can act on."""
        actor = user_principal(make_user())
        agent = make_agent(slug="alpha", name="Alpha")
        pk = agent.pk
        agent.delete()
        assert set_agent_axis(make_entitlement(name="Finance").pk, add={str(pk)},
                              remove=set(), actor=actor) == {"added": 0, "removed": 0}


class TestTheCountsStayFlat:
    def test_one_query_per_axis_however_many_entitlements(self, django_assert_num_queries):
        """The shape the entitlements list depends on: a per-entitlement
        counter would be the sidebar N+1 this codebase has already paid
        for once."""
        for index in range(5):
            entitlement = make_entitlement(name=f"E{index}")
            ToolEntitlement.objects.create(tool_key="rag.search",
                                           entitlement=entitlement)
        with django_assert_num_queries(1):
            counts = tool_counts()
        assert sorted(counts.values()) == [1, 1, 1, 1, 1]
