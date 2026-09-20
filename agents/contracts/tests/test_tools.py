"""The tool contract's dataclasses (spec sections 4.1, 4.2, 4.4)."""
from __future__ import annotations

import logging
import time

import pytest

from agents.contracts.tests._helpers import (  # noqa: F401
    isolated_tool_registry, make_budget, make_principal, make_spec, make_tool_ctx,
)
from agents.contracts.tools import (
    TOOL_PARAM_KINDS, Principal, StepBudget, ToolAccess, ToolContext,
    ToolRefused, ToolResult, ToolSpec, UNRESTRICTED_TOOL_ACCESS, granted_tools, register_tool,
    validate_tool_args,
)
from identity.contracts.principals import OPEN_PRINCIPAL
from models.contracts.operations import Param, ParamError

# `TestGrantedTools` registers tools of its own; without this a
# registration here would leak into every other module's test of
# `all_tools()`/`grantable_tools()` and into `foundation/ops/tests/
# test_column_boundaries.py`'s registry-wide sweep.
pytestmark = pytest.mark.usefixtures("isolated_tool_registry")


def _explode(*args, **kwargs):
    raise AssertionError("get_tool must not be called by granted_tools")


class TestToolParamKinds:
    def test_file_is_excluded_because_a_tool_call_is_json(self):
        """ADR 0012:140 -- a tool call is JSON, so it cannot carry an
        upload object. An image input is an artifact REFERENCE
        (agents/contracts/artifacts.py), never a file."""
        assert "file" not in TOOL_PARAM_KINDS

    def test_every_other_operation_param_kind_is_usable(self):
        from models.contracts.operations import PARAM_KINDS

        assert TOOL_PARAM_KINDS == frozenset(PARAM_KINDS) - {"file"}


class TestToolSpecValidation:
    def test_a_file_param_is_rejected_at_definition_time(self):
        with pytest.raises(ValueError) as excinfo:
            make_spec(params=(Param("image", "file", "Image"),))
        assert "image" in str(excinfo.value)
        assert "file" in str(excinfo.value)

    def test_a_blank_runner_is_rejected(self):
        with pytest.raises(ValueError, match="declares no runner"):
            make_spec(runner="")

    def test_a_spaced_key_is_rejected(self):
        with pytest.raises(ValueError, match="space-free"):
            make_spec(key="rag search")

    def test_a_blank_key_is_rejected(self):
        with pytest.raises(ValueError, match="non-blank"):
            make_spec(key="")

    def test_a_key_containing_a_double_underscore_is_rejected(self):
        """`wire_name` maps "." -> "__" and `key_from_wire_name` maps
        back. A key that already contains "__" makes that round trip
        ambiguous, so it is a definition-time error, not a runtime
        surprise on the one tool call that happens to hit it."""
        with pytest.raises(ValueError, match="wire-name collision"):
            make_spec(key="rag__search")

    def test_a_valid_spec_is_frozen(self):
        spec = make_spec()
        with pytest.raises(Exception):
            spec.key = "other"

    def test_describer_defaults_to_blank_and_is_inert(self):
        """Ruling R3: the field exists so R2/Phase-1.55 is a fill-in
        rather than a contract change. NOTHING in P1-P4 calls it."""
        assert make_spec().describer == ""


class TestToolResult:
    def test_defaults_are_empty_and_do_not_share_state(self):
        a, b = ToolResult(), ToolResult()
        a.data["x"] = 1
        assert b.data == {}
        assert a.artifacts == ()
        assert a.text == ""

    def test_artifacts_is_a_tuple_of_reference_strings(self):
        result = ToolResult(artifacts=("document:7", "output:3"))
        assert result.artifacts == ("document:7", "output:3")


class TestStepBudget:
    def test_spend_decrements_and_exhausts(self):
        budget = make_budget(steps=2)
        assert not budget.exhausted
        budget.spend()
        assert budget.steps_left == 1
        budget.spend()
        assert budget.steps_left == 0
        assert budget.exhausted

    def test_spend_takes_a_count(self):
        budget = make_budget(steps=5)
        budget.spend(3)
        assert budget.steps_left == 2

    def test_a_past_deadline_is_expired(self):
        assert StepBudget(steps=8, deadline_monotonic=time.monotonic() - 1.0).expired

    def test_a_future_deadline_is_not_expired(self):
        assert not make_budget().expired

    def test_recoveries_default_to_one(self):
        assert make_budget().recoveries_left == 1

    def test_it_is_mutable_by_design(self):
        """The ONE mutable object in this module: a turn's remaining
        allowance, SHARED by every tool call and every delegated
        sub-agent in that turn. `ToolContext` stays frozen and holds a
        reference -- the same shape `JobContext` uses for its injected
        closures."""
        budget = make_budget(steps=4)
        ctx = make_tool_ctx(budget=budget)
        ctx.budget.spend()
        assert budget.steps_left == 3


class TestToolContext:
    def test_it_is_frozen_and_carries_the_shared_budget(self):
        ctx = make_tool_ctx()
        with pytest.raises(Exception):
            ctx.depth = 9
        assert ctx.depth == 0
        assert isinstance(ctx.budget, StepBudget)

    def test_job_is_the_runners_only_progress_path(self):
        reported = []
        ctx = make_tool_ctx()
        object.__setattr__(ctx.job, "_report", reported.append)
        ctx.job.report_progress(1, total=8, unit="items", label="thinking")
        assert reported == [{"done": 1, "total": 8, "unit": "items", "label": "thinking"}]

    def test_it_carries_a_principal_not_an_agent_key(self):
        ctx = make_tool_ctx()
        assert isinstance(ctx.principal, Principal)
        assert not hasattr(ctx, "agent_key")

    def test_tool_key_defaults_blank_and_is_set_per_call(self):
        """N `agent.<slug>` specs share one runner (deviation D4); a
        runner's signature is `(args, ctx)`, so `ctx.tool_key` is the
        only way it can learn which spec invoked it. `invoke_tool` sets
        it; a hand-built context leaves it blank."""
        import dataclasses

        ctx = make_tool_ctx()
        assert ctx.tool_key == ""
        assert dataclasses.replace(ctx, tool_key="agent.library").tool_key == "agent.library"


class TestToolContextCarriesBothFacts:
    def test_principal_is_who_and_agent_slug_is_whose_declaration(self):
        """They were ONE field, which is why the acting rule could not
        be expressed before: `principal` now always means WHO THIS IS
        BEING DONE FOR, and `agent_slug` means WHOSE TOOL DECLARATION IS
        IN FORCE. A delegate carries the caller's principal and its own
        slug -- that pair is the whole of "an agent is never a way
        around labels"."""
        ctx = make_tool_ctx(principal=Principal("user", "7"), agent_slug="librarian")
        assert ctx.principal == Principal("user", "7")
        assert ctx.agent_slug == "librarian"

    def test_it_defaults_blank_so_a_direct_runner_call_still_works(self):
        """Every test that builds a bare `ToolContext` and skips
        `invoke_tool` -- and the future MCP edge, which has no agent at
        all."""
        ctx = make_tool_ctx(principal=OPEN_PRINCIPAL)
        assert ctx.agent_slug == ""


class TestValidateToolArgs:
    def test_it_delegates_to_the_platforms_one_floor(self):
        spec = make_spec(params=(
            Param("query", "text", "Query", required=True),
            Param("top_k", "int", "Results", default=None, min=1, max=50),
        ))
        assert validate_tool_args(spec, {"query": "x", "top_k": "7"}) == {
            "query": "x", "top_k": 7,
        }

    def test_it_raises_paramerror_with_per_arg_reasons(self):
        """`.errors` maps a param key to a human-readable reason
        (operations.py:201-209) -- which is what makes a bad tool call
        the ONE failure worth handing back to the model for a retry."""
        spec = make_spec(params=(Param("query", "text", "Query", required=True),))
        with pytest.raises(ParamError) as excinfo:
            validate_tool_args(spec, {"query": ""})
        assert excinfo.value.errors == {"query": "Query is required."}

    def test_an_unknown_arg_is_rejected_not_swallowed(self):
        spec = make_spec(params=(Param("query", "text", "Query"),))
        with pytest.raises(ParamError) as excinfo:
            validate_tool_args(spec, {"quary": "x"})
        assert "quary" in excinfo.value.errors

    def test_a_blank_seed_resolves_to_an_integer(self):
        spec = make_spec(params=(Param("seed", "seed", "Seed"),))
        clean = validate_tool_args(spec, {})
        assert isinstance(clean["seed"], int)


class TestToolRefused:
    def test_it_is_a_distinct_exception_from_paramerror(self):
        """Section 10.1 splits them: a refusal gets NO retry, a
        ParamError gets exactly one. Nothing catches ToolRefused until
        P2's `invoke_tool`, but a runner author -- including the
        VISION-OWNED session -- needs to know which exception means
        which, and this module is the only place that can say so."""
        assert issubclass(ToolRefused, ValueError)
        assert not issubclass(ToolRefused, ParamError)
        assert not issubclass(ParamError, ToolRefused)


class TestGrantedTools:
    def test_an_unregistered_key_is_dropped_and_logged(self, caplog):
        # `caplog` captures at WARNING by default; without the level and
        # the logger name this assertion passes even if the log call is
        # deleted.
        with caplog.at_level(logging.INFO, logger="agents.contracts.tools"):
            assert granted_tools(make_principal(), ["nope.missing"]) == []
        assert "nope.missing" in caplog.text

    def test_a_mutating_key_is_dropped(self):
        register_tool(make_spec(key="stub.mutating", mutates=True))
        assert granted_tools(make_principal(), ["stub.mutating"]) == []

    def test_a_registered_non_mutating_key_survives(self):
        register_tool(make_spec(key="stub.safe"))
        assert granted_tools(make_principal(), ["stub.safe"]) == ["stub.safe"]

    def test_order_is_the_caller_s_order_and_duplicates_collapse(self):
        """The agent row's order is the order the model sees its tools
        in. Preserved, because a stable prompt is a debuggable one."""
        register_tool(make_spec(key="stub.a"))
        register_tool(make_spec(key="stub.b"))
        assert granted_tools(make_principal(), ["stub.b", "stub.a", "stub.b"]) == [
            "stub.b", "stub.a",
        ]

    def test_it_never_calls_get_tool(self, monkeypatch):
        """An absent key is NORMAL here, and `get_tool` RAISES on one
        (`agents/contracts/tools.py:238`). This function must read the
        registry directly -- `agents/contracts/README.md`'s "get_tool
        raises; the prompt builder does not" section is the rule."""
        import agents.contracts.tools as module

        monkeypatch.setattr(module, "get_tool", _explode)
        assert granted_tools(make_principal(), ["nope.missing"]) == []


class TestToolAccess:
    """A PURE VALUE. `granted_tools` lives in a rule-1 leaf and cannot
    query `ToolEntitlement`, so the facts arrive as data."""

    def test_the_default_is_unrestricted_and_allows_everything(self):
        from agents.contracts.tools import UNRESTRICTED_TOOL_ACCESS
        assert UNRESTRICTED_TOOL_ACCESS.allows("anything.at.all") is True

    def test_an_absent_key_is_unlabelled_and_therefore_allowed(self):
        from agents.contracts.tools import ToolAccess
        access = ToolAccess(required={"rag.search": frozenset({1})},
                            held=frozenset(), unrestricted=False)
        assert access.allows("vision.generate") is True
        assert access.allows("rag.search") is False

    def test_holding_any_one_of_a_keys_entitlements_is_enough(self):
        """OR-match only. "Must hold both" is a named non-goal; the
        answer to it is a more specific entitlement."""
        from agents.contracts.tools import ToolAccess
        access = ToolAccess(required={"rag.search": frozenset({1, 2})},
                            held=frozenset({2}), unrestricted=False)
        assert access.allows("rag.search") is True

    def test_unrestricted_short_circuits_even_a_labelled_key(self):
        from agents.contracts.tools import ToolAccess
        access = ToolAccess(required={"rag.search": frozenset({1})},
                            held=frozenset(), unrestricted=True)
        assert access.allows("rag.search") is True


class TestGrantedToolsThirdDrop:
    """LOCALLY REGISTERED STUBS, never `tools/rag`'s real keys. This is a
    rule-1 pure leaf's test module: depending on another column's
    `AppConfig.ready()` registrations would make a leaf's test fail when a
    feature flag moved. `make_spec` and `make_principal` come from
    `agents/contracts/tests/_helpers.py`, already imported at the top of
    this module, and the module-level
    `pytestmark = pytest.mark.usefixtures("isolated_tool_registry")` at
    `agents/contracts/tests/test_tools.py:23` keeps every registration
    below out of every other module's registry sweep.
    """

    def test_the_two_argument_call_behaves_exactly_as_before(self):
        """The regression pin for OPEN posture and for every existing
        call site and test: `access` defaults to unrestricted, so
        omitting it changes nothing."""
        register_tool(make_spec(key="stub.safe"))
        assert granted_tools(make_principal(), ["stub.safe"]) == ["stub.safe"]

    def test_a_labelled_key_is_dropped_for_a_principal_without_the_entitlement(self):
        register_tool(make_spec(key="stub.safe"))
        access = ToolAccess(required={"stub.safe": frozenset({1})},
                            held=frozenset({2}), unrestricted=False)
        assert granted_tools(make_principal(), ["stub.safe"], access) == []

    def test_an_unlabelled_key_is_kept_for_everybody(self):
        register_tool(make_spec(key="stub.safe"))
        access = ToolAccess(required={}, held=frozenset(), unrestricted=False)
        assert granted_tools(make_principal(), ["stub.safe"], access) == ["stub.safe"]

    def test_a_mutating_key_is_still_dropped_regardless_of_access(self):
        """ADR 0010's rule stays in force THROUGH IA-2 (spec section 9.1,
        non-goal 12): a settings-mutating tool is registered but not
        grantable, and lifting that is a later phase's decision. Even a
        principal holding the labelling entitlement does not get it."""
        register_tool(make_spec(key="stub.mutating", mutates=True))
        access = ToolAccess(required={"stub.mutating": frozenset({1})},
                            held=frozenset({1}), unrestricted=False)
        assert granted_tools(make_principal(), ["stub.mutating"], access) == []

    def test_order_and_de_duplication_survive_the_third_drop(self):
        register_tool(make_spec(key="stub.a"))
        register_tool(make_spec(key="stub.b"))
        access = ToolAccess(required={"stub.b": frozenset({1})},
                            held=frozenset(), unrestricted=False)
        assert granted_tools(make_principal(), ["stub.a", "stub.b", "stub.a"],
                             access) == ["stub.a"]
