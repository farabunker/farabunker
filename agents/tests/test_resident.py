"""`agents/resident.py`, reduced (ruling 2, 2026-08-28) to the two
things `AppConfig.ready()` genuinely needs: `AGENT_TOOL_PREFIX` and
`agent_tool_specs()`.

`AgentSpec`/`RESIDENT_AGENTS` (renamed `DEFAULT_AGENTS`) and
`sync_resident_agents` moved to `agents/defaults.py` -- see
`agents/tests/test_defaults.py` for the catalogue's own declaration
tests, `validate_flow_json`, and `install_default`. This module keeps
only what is genuinely `agents/resident.py`'s own: that it still
derives `agent_tool_specs()` correctly from the catalogue, that it
still imports no Django, and that the retired names are actually gone
rather than merely unused.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.conf import settings

from agents.resident import AGENT_TOOL_PREFIX, agent_tool_specs
from agents.tests._helpers import isolated_tool_registry  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

_RESIDENT_PY = Path(settings.BASE_DIR) / "agents" / "resident.py"


class TestAgentToolSpecs:
    def test_one_spec_per_as_tool_default(self):
        assert {s.key for s in agent_tool_specs()} == {"agent.library", "agent.illustrator"}

    def test_every_key_carries_the_prefix(self):
        for spec in agent_tool_specs():
            assert spec.key.startswith(AGENT_TOOL_PREFIX)

    def test_they_share_one_runner(self):
        runners = {s.runner for s in agent_tool_specs()}
        assert runners == {"agents.runtime.delegate.run_agent_tool"}

    def test_none_of_them_mutate(self):
        assert not any(s.mutates for s in agent_tool_specs())

    def test_roles_are_empty_at_registration(self):
        """A delegate's roles are not knowable at registration time --
        `plan_turn` supplies them at enqueue time by walking the
        closure."""
        assert all(spec.roles == () for spec in agent_tool_specs())

    def test_general_is_not_exposed_as_a_tool_to_itself(self):
        """`general` is the root an operator talks to, not a subroutine."""
        assert "agent.general" not in {s.key for s in agent_tool_specs()}


class TestResidentPyImportsNoDjango:
    """Read as source, not as a live module: `agents/apps.py::ready()`
    imports this to build tool specs, and `ready()` must not pull
    Django models in. An AST check catches the import that a successful
    `import agents.resident` inside a configured Django would not."""

    def _tree(self):
        return ast.parse(_RESIDENT_PY.read_text())

    def test_no_django_import_anywhere(self):
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("django")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("django")

    def test_the_gate_would_actually_catch_a_django_import(self):
        planted = ast.parse("from django.db import models\n")
        found = any(
            isinstance(node, ast.ImportFrom) and node.module
            and node.module.startswith("django")
            for node in ast.walk(planted)
        )
        assert found


class TestTheRetiredNamesAreReallyGone:
    def test_sync_resident_agents_no_longer_exists(self):
        import agents.resident as resident

        assert not hasattr(resident, "sync_resident_agents")

    def test_resident_agents_constant_no_longer_lives_here(self):
        """Renamed `DEFAULT_AGENTS` and moved to `agents/defaults.py` --
        `agents/resident.py` re-exports nothing under the old name."""
        import agents.resident as resident

        assert not hasattr(resident, "RESIDENT_AGENTS")

    def test_agent_spec_no_longer_lives_here(self):
        import agents.resident as resident

        assert not hasattr(resident, "AgentSpec")

    def test_the_sync_agents_command_file_is_deleted(self):
        path = (
            Path(settings.BASE_DIR) / "agents" / "management" / "commands" / "sync_agents.py"
        )
        assert not path.exists()
