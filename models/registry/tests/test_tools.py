"""`models.status` -- the models/ column's own tool (spec section 5,
ruling R2)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agents.contracts.tools import all_tools, get_tool, grantable_tools
from models.contracts.jobkinds import resolve_dotted_path
from models.contracts.operations import ParamError
from models.registry.tests._helpers import (  # noqa: F401
    isolated_tool_registry, make_chat_connection, make_tool_ctx,
)

pytestmark = pytest.mark.usefixtures("isolated_tool_registry")


class TestRegistration:
    def test_it_is_registered_so_the_models_column_adopts_the_contract(self):
        """Without it, `models/` is the one column that registers no tool
        -- which is half of ruling R2's reason for keeping it."""
        assert "models.status" in {spec.key for spec in all_tools()}

    def test_its_runner_resolves(self):
        assert callable(resolve_dotted_path(get_tool("models.status").runner))

    def test_it_declares_no_roles_and_no_params(self):
        spec = get_tool("models.status")
        assert spec.roles == ()
        assert spec.params == ()

    def test_it_does_not_mutate_and_is_grantable(self):
        assert get_tool("models.status").mutates is False
        assert "models.status" in {spec.key for spec in grantable_tools()}

    def test_it_declares_no_describer(self):
        assert get_tool("models.status").describer == ""


class TestItIsDatabaseOnly:
    def test_the_source_makes_no_engine_call_of_any_kind(self):
        """Ruling R2. Reachability is a live network fact whose cost is
        unbounded and whose latency lands inside a turn already holding
        the machine's one execution slot. `/setup/` answers "is the
        engine up", on demand, for a human, outside the queue."""
        import inspect

        from models.registry import tools as tools_module

        source = inspect.getsource(tools_module)
        for forbidden in ("is_healthy", "httpx", "get_engine", "preflight",
                          "list_installed", "loaded_footprint"):
            assert forbidden not in source, forbidden

    @pytest.mark.django_db
    def test_it_makes_no_http_request_when_it_runs(self):
        """The source check above is necessary but not sufficient -- a
        transitive call could still reach the network. This proves it
        does not."""
        from models.registry.tools import run_status

        with patch("httpx.get", side_effect=AssertionError("no HTTP allowed")), \
             patch("httpx.post", side_effect=AssertionError("no HTTP allowed")):
            run_status({}, make_tool_ctx())


@pytest.mark.django_db
class TestRunStatus:
    def test_it_reports_a_bound_connection_with_its_name_and_pk(self):
        from models.registry.models import RoleBinding
        from models.registry.tools import run_status

        connection = make_chat_connection(name="the box's chat model")
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        result = run_status({}, make_tool_ctx())
        row = next(r for r in result.data["roles"] if r["role"] == "rag.answer")
        assert row["model"] == "the box's chat model"
        assert row["connection_id"] == connection.pk
        assert row["assigned"] is True
        assert row["capability"] == "chat"

    def test_an_unassigned_role_is_reported_as_unassigned_not_omitted(self):
        """Three honest states, none collapsed: bound, environment
        override, genuinely unassigned (bindings.py:192-221)."""
        from models.registry.tools import run_status

        with patch("models.registry.bindings.role_primary", return_value=("", None)):
            result = run_status({}, make_tool_ctx())

        assert result.data["roles"]
        for row in result.data["roles"]:
            assert row["assigned"] is False
            assert row["connection_id"] is None
            assert "not assigned" in result.text

    def test_an_environment_override_is_reported_with_no_connection_id(self):
        from models.registry.tools import run_status

        with patch("models.registry.bindings.role_primary",
                   return_value=("something (environment override)", None)):
            result = run_status({}, make_tool_ctx())

        row = result.data["roles"][0]
        assert row["assigned"] is True
        assert row["connection_id"] is None
        assert "environment override" in row["model"]

    def test_it_covers_every_registered_role(self):
        from models.contracts.roles import all_roles
        from models.registry.tools import run_status

        result = run_status({}, make_tool_ctx())
        assert {r["role"] for r in result.data["roles"]} == {
            role.key for role in all_roles()
        }

    def test_the_text_is_one_readable_line_per_role(self):
        from models.contracts.roles import all_roles
        from models.registry.tools import run_status

        result = run_status({}, make_tool_ctx())
        assert len(result.text.splitlines()) == len(all_roles())

    def test_the_data_is_json_safe(self):
        from models.registry.tools import run_status

        data = run_status({}, make_tool_ctx()).data
        assert json.loads(json.dumps(data)) == data

    def test_it_produces_no_artifacts(self):
        from models.registry.tools import run_status

        assert run_status({}, make_tool_ctx()).artifacts == ()

    def test_an_unexpected_argument_is_rejected_not_ignored(self):
        """It declares no params, so ANY argument is a bug in the call --
        and `validate_tool_args` is the one floor that says so."""
        from models.registry.tools import run_status

        with pytest.raises(ParamError) as excinfo:
            run_status({"role": "rag.answer"}, make_tool_ctx())
        assert "role" in excinfo.value.errors
