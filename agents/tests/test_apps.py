"""`agents` is a Django app, and making it one did not cost the pure leaf.

Two properties, deliberately in one module: the app is installed under
the label every migration and every `apps.get_model` call will use, and
`agents/contracts/` is still reachable without Django being configured
at all. The second is not paranoia -- `agents/contracts/tests/
test_purity.py` proves it in a subprocess, and this asserts the ONE
thing that would break it here: `agents/__init__.py` must stay inert.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings

from agents.contracts.tools import all_tools, get_tool
from agents.tests._helpers import isolated_tool_registry  # noqa: F401
from models.contracts.jobkinds import resolve_dotted_path

pytestmark = pytest.mark.usefixtures("isolated_tool_registry")


def test_the_agents_app_is_installed_under_the_agents_label():
    config = apps.get_app_config("agents")
    assert config.name == "agents"
    assert config.label == "agents"


def test_agents_package_init_is_inert():
    """`import agents.contracts.tools` evaluates `agents/__init__.py`
    first. If that file ever grows an import or a statement, the rule-1
    pure leaf below it inherits whatever that import drags in. A
    docstring is the only body this file may have."""
    source = (Path(settings.BASE_DIR) / "agents" / "__init__.py").read_text(encoding="utf-8")
    body = ast.parse(source).body
    assert len(body) == 1, [type(node).__name__ for node in body]
    node = body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    assert isinstance(node.value.value, str)


class TestReadyRegistersAgentAsTool:
    """CRITICAL review finding (Task 11): nothing pinned `ready()`
    actually calling `register_tool` for the agent-as-tool specs -- a
    reviewer proved it by popping both `agent.*` keys from the registry
    and finding no test failed. `ready()` is idempotent (every
    `register_*` it calls overwrites, per each one's own docstring), so
    re-invoking it here under the `_TOOLS` snapshot/restore fixture is
    exactly what a real process does once at startup -- it proves the
    wiring in `AgentsConfig.ready()`, not the fact that some earlier
    startup already did it once.
    """

    def test_ready_registers_the_two_specialist_delegates(self):
        apps.get_app_config("agents").ready()
        library = get_tool("agent.library")
        illustrator = get_tool("agent.illustrator")
        assert library.runner == "agents.runtime.delegate.run_agent_tool"
        assert illustrator.runner == "agents.runtime.delegate.run_agent_tool"
        # `roles=()` on every spec, per the spec (section 6.4) and
        # `agent_tool_specs`'s own docstring: a delegate's roles are not
        # knowable at registration time, so `plan_turn` supplies them
        # later by walking the closure.
        assert library.roles == ()
        assert illustrator.roles == ()

    def test_anti_vacuous_popping_them_first_proves_ready_is_what_restores_them(self):
        """Without this, the test above could pass for the wrong reason
        -- some OTHER code path having registered these keys before
        `ready()` ever ran in this process."""
        from agents.contracts import tools as tools_module

        apps.get_app_config("agents").ready()
        tools_module._TOOLS.pop("agent.library", None)
        tools_module._TOOLS.pop("agent.illustrator", None)
        registered = {spec.key for spec in all_tools()}
        assert "agent.library" not in registered
        assert "agent.illustrator" not in registered

        apps.get_app_config("agents").ready()
        registered = {spec.key for spec in all_tools()}
        assert "agent.library" in registered
        assert "agent.illustrator" in registered


class TestReadyRegistersFlowRun:
    """Ruling 1 / deviation P3-D11: ONE registered `flow.run` tool, not
    one per flow. `ready()` registers it exactly like the two delegate
    specs above -- built from code, never from a `Flow` row -- so this
    class extends the same fixture and the same "re-invoke `ready()`
    under a snapshot" proof rather than writing a second copy of it.
    """

    def test_ready_registers_flow_run(self):
        apps.get_app_config("agents").ready()
        spec = get_tool("flow.run")
        assert spec.key == "flow.run"
        assert spec.runner == "agents.runtime.flow.run_flow"

    def test_the_runner_resolves_through_resolve_dotted_path(self):
        """The same resolution `invoke_tool` performs at call time --
        proving the dotted path is not merely a plausible-looking
        string."""
        apps.get_app_config("agents").ready()
        spec = get_tool("flow.run")
        assert callable(resolve_dotted_path(spec.runner))

    def test_the_flow_param_has_empty_choices_at_registration(self):
        """The anti-vacuous half of P3-D11: if `choices` were pre-filled
        here, the per-turn narrowing (`agents.runtime.flowtool.
        narrowed_flow_spec`) would be dead code that still looked alive.
        """
        apps.get_app_config("agents").ready()
        spec = get_tool("flow.run")
        flow_param = next(p for p in spec.params if p.key == "flow")
        assert flow_param.choices == ()
        assert flow_param.required is True

    def test_no_flow_dot_slug_key_is_registered(self):
        """The pin on P3-D3's retirement: the spec once proposed
        `flow.<slug>`, one registered tool per flow. Ruling 1 replaced
        that with ONE `flow.run` whose choices are filled per turn --
        so no `flow.<anything-but-run>` key should ever appear."""
        apps.get_app_config("agents").ready()
        flow_keys = {spec.key for spec in all_tools() if spec.key.startswith("flow.")}
        assert flow_keys == {"flow.run"}

    @pytest.mark.django_db
    def test_ready_still_touches_no_database(self, django_assert_num_queries):
        """`register_tool(FLOW_RUN)` is in-memory, code-driven
        registration -- the same property the two delegate specs above
        already have, extended to cover the new call rather than pinned
        a second way."""
        with django_assert_num_queries(0):
            apps.get_app_config("agents").ready()


class TestReadyRegistersTheToolLabelsCascade:
    """IA-2 T4 review finding: the `register_entitlement_cascade` call in
    `AgentsConfig.ready()` was untested production wiring -- deleting
    the whole block left every test in this repository green. Same
    anti-vacuous shape as `TestReadyRegistersAgentAsTool.
    test_anti_vacuous_popping_them_first_proves_ready_is_what_restores_them`:
    pop the key first, so passing proves `ready()` put it back rather
    than some earlier registration this process already ran.
    """

    def test_ready_registers_the_tool_labels_cascade(self):
        from identity.contracts import cascades as cascades_module

        apps.get_app_config("agents").ready()
        cascades_module._CASCADES.pop("agents.tool_labels", None)
        registered = {spec.key for spec in cascades_module.all_entitlement_cascades()}
        assert "agents.tool_labels" not in registered

        apps.get_app_config("agents").ready()
        by_key = {
            spec.key: spec for spec in cascades_module.all_entitlement_cascades()
        }
        assert "agents.tool_labels" in by_key
        assert by_key["agents.tool_labels"].handler == "agents.labels.tool_labels_cascade"


class TestReadyRegistersTheRunnableLabelsCascade:
    """Task 15's own cascade, pinned the SAME anti-vacuous way as
    `TestReadyRegistersTheToolLabelsCascade` above: pop the key first, so
    passing proves `ready()` put it back rather than some earlier
    registration this process already ran. ONE cascade for both
    `AgentEntitlement` and `FlowEntitlement` -- see `agents.labels.
    agent_flow_label_cascade`'s own docstring for why."""

    def test_ready_registers_the_agent_and_flow_labels_cascade(self):
        from identity.contracts import cascades as cascades_module

        apps.get_app_config("agents").ready()
        cascades_module._CASCADES.pop("agents.runnable_labels", None)
        registered = {spec.key for spec in cascades_module.all_entitlement_cascades()}
        assert "agents.runnable_labels" not in registered

        apps.get_app_config("agents").ready()
        by_key = {
            spec.key: spec for spec in cascades_module.all_entitlement_cascades()
        }
        assert "agents.runnable_labels" in by_key
        assert (by_key["agents.runnable_labels"].handler
                == "agents.labels.agent_flow_label_cascade")
