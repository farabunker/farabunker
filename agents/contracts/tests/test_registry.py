"""The tool registry and `describe_tool` (spec section 4.3)."""
from __future__ import annotations

import json

import pytest

from agents.contracts import tools as tools_module
from agents.contracts.tests._helpers import isolated_tool_registry, make_spec  # noqa: F401
from agents.contracts.tools import (
    ToolSpec, all_tools, describe_tool, get_tool, grantable_tools, register_tool,
)
from models.contracts.operations import Param

pytestmark = pytest.mark.usefixtures("isolated_tool_registry")


class TestRegisterTool:
    def test_it_registers_under_the_specs_key(self):
        register_tool(make_spec(key="x.one"))
        assert get_tool("x.one").key == "x.one"

    def test_it_is_idempotent_like_its_siblings(self):
        """Matches `register_role` and `register_job_kind`: registering
        the same key again overwrites, so re-importing a module that
        registers at import time is safe."""
        register_tool(make_spec(key="x.one", label="First"))
        register_tool(make_spec(key="x.one", label="Second"))
        assert get_tool("x.one").label == "Second"
        assert [t.key for t in all_tools()].count("x.one") == 1

    def test_all_tools_preserves_registration_order(self):
        before = len(all_tools())
        register_tool(make_spec(key="x.one"))
        register_tool(make_spec(key="x.two"))
        register_tool(make_spec(key="x.three"))
        assert [t.key for t in all_tools()[before:]] == ["x.one", "x.two", "x.three"]


class TestGetTool:
    def test_it_raises_naming_the_key(self):
        """RAISES, not None -- matching `get_job_kind`, not `get_role`. A
        tool call for an unregistered tool is a caller bug, not a state
        to degrade gracefully from."""
        with pytest.raises(ValueError) as excinfo:
            get_tool("nope.missing")
        assert "nope.missing" in str(excinfo.value)

    def test_the_none_returning_lookup_exists_for_the_normal_absent_case(self):
        """P2's prompt builder drops an unregistered granted key rather
        than crashing, so it needs a lookup that does not raise."""
        assert tools_module._TOOLS.get("nope.missing") is None


class TestGrantableTools:
    def test_it_excludes_every_mutating_spec(self):
        """ADR 0010:266-276 -- a settings-mutating tool is registered (so
        it is visible, documented, and testable) but must not be
        grantable before Identity & Auth."""
        register_tool(make_spec(key="x.read"))
        register_tool(make_spec(key="x.write", mutates=True))
        keys = {t.key for t in grantable_tools()}
        assert "x.read" in keys
        assert "x.write" not in keys

    def test_a_mutating_tool_is_still_registered_and_findable(self):
        register_tool(make_spec(key="x.write", mutates=True))
        assert get_tool("x.write").mutates is True
        assert "x.write" in {t.key for t in all_tools()}


class TestDescribeTool:
    def test_it_emits_one_key_per_serialised_field_and_no_more(self):
        """Written out explicitly (one key per field) rather than via
        `dataclasses.asdict`, so a field added without a serialization
        decision fails THIS test instead of silently appearing in a
        public contract -- the exact rationale
        `models/contracts/operations.py:178-182` gives for
        `_describe_param`."""
        described = describe_tool(make_spec())
        assert set(described) == {
            "key", "label", "description", "roles", "mutates", "params",
        }

    def test_runner_and_describer_are_deliberately_absent(self):
        """Both are INTERNAL: one is a dotted path into this codebase,
        the other is reserved. Neither belongs in a contract handed to an
        LLM, an HTTP client, or a future MCP surface. This asserts their
        ABSENCE, not their presence."""
        described = describe_tool(make_spec(describer="some.path.describe"))
        assert "runner" not in described
        assert "describer" not in described

    def test_it_carries_no_live_facts_keys(self):
        """Ruling R3: `supported`/`unsupported_reason`/`options` are
        reserved for R2/Phase 1.55 and unreachable now -- nothing calls a
        describer, so nothing can produce them."""
        described = describe_tool(make_spec(params=(Param("a", "text", "A"),)))
        serialised = json.dumps(described)
        for reserved in ("supported", "unsupported_reason", "options"):
            assert reserved not in serialised

    def test_it_is_json_safe(self):
        described = describe_tool(make_spec(
            roles=("rag.embed",),
            params=(Param("mode", "choice", "Mode", choices=("a", "b")),),
        ))
        assert json.loads(json.dumps(described)) == described
        assert described["roles"] == ["rag.embed"]        # list, not tuple
        assert described["params"][0]["choices"] == ["a", "b"]

    def test_params_reuse_the_operations_describer(self):
        """Not a second copy of the param-serialisation rule."""
        from models.contracts.operations import _describe_param

        param = Param("a", "text", "A", description="Alpha.")
        assert describe_tool(make_spec(params=(param,)))["params"] == [
            _describe_param(param)
        ]

    def test_every_toolspec_field_is_either_serialised_or_named_internal(self):
        """The anti-drift pin: a NEW ToolSpec field must be added to
        `describe_tool` or added to `_INTERNAL_FIELDS` with a reason. It
        cannot be silently ignored."""
        import dataclasses

        declared = {f.name for f in dataclasses.fields(ToolSpec)}
        serialised = set(describe_tool(make_spec()))
        internal = {"runner", "describer"}
        assert declared == serialised | internal
