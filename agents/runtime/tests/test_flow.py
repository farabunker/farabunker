"""`agents/runtime/flow.py` -- the flow resolver and runner.

Every step goes through the SAME `invoke_tool` the ReAct loop uses, so
these tests register real `ToolSpec`s and drive real calls rather than
mocking the runner -- the same discipline `test_invoke.py` and
`test_delegate.py` already hold.

A NOTE ON THE REFERENCE GRAMMAR. Every `$steps.<N>.<field>` reference
below is spelled the way `agents.defaults`'s own `_STEP_REF` regex
checks it -- plural "steps", dotted before the index, per the spec
(`docs/superpowers/specs/2026-08-25-agents-and-tools-design.md:1490-
1504,2144`). `_run_steps` parses a row through
`agents.defaults.parse_flow_json`, the identical function `Flow.save()`
calls, so a reference this module's `resolve_ref` can resolve and a
reference `Flow.save()` will accept are the SAME string.
"""
from __future__ import annotations

import json
import uuid

import pytest

from agents.contracts.tools import ToolRefused, ToolResult, ToolSpec, register_tool
from agents.models import Flow, ToolInvocation
from agents.runtime.flow import (
    FlowReferenceError,
    _run_steps,
    resolve_args,
    resolve_ref,
    run_flow,
)
from agents.runtime.invoke import invoke_tool
from agents.runtime.tests._helpers import (  # noqa: F401
    CALLS, isolated_tool_registry, make_budget, make_tool_ctx,
)
from models.contracts.operations import Param, ParamError

# A module-global registry surviving between tests is exactly the state
# that makes a suite pass in one collection order and fail in the other.
# `isolated_tool_registry` is defined in `agents/tests/_helpers.py`, the
# same fixture shape every other tool-registering test module uses.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]


def _make_flow(**overrides):
    """A persisted `Flow` row, via the ordinary `.create()` path (so
    `Flow.save()`'s own validator runs). `steps` defaults to one legal,
    unregistered-is-fine step -- declaration-time validation does not
    check registration (ruling R1)."""
    fields = dict(
        slug=f"flow-test-{uuid.uuid4().hex[:8]}",
        name="Test flow",
        description="",
        inputs=[],
        steps=[{"tool": "flow-test.default-noop", "args": {}}],
        resident=False,
        enabled=True,
    )
    fields.update(overrides)
    return Flow.objects.create(**fields)


def _register(key: str, runner: str, *, params=(), mutates: bool = False) -> None:
    register_tool(ToolSpec(
        key=key, label=key, description="a flow-test stub",
        params=params, runner=runner, mutates=mutates,
    ))


def _two_step_flow(**overrides) -> Flow:
    _register("flow-test.one", "agents.runtime.tests._helpers.runner_flow_step_one")
    _register("flow-test.two", "agents.runtime.tests._helpers.runner_flow_step_two")
    fields = dict(steps=[
        {"tool": "flow-test.one", "args": {}},
        {"tool": "flow-test.two", "args": {}},
    ])
    fields.update(overrides)
    return _make_flow(**fields)


# --- resolve_ref ------------------------------------------------------


class TestResolveRef:
    def test_an_undeclared_input_raises_naming_it(self):
        """M7, half one: an UNDECLARED input key is a declaration bug."""
        with pytest.raises(FlowReferenceError) as exc:
            resolve_ref("$input.missing", input={"topic": "x"}, steps=[])
        assert "$input.missing" in str(exc.value)

    def test_a_declared_but_unsupplied_optional_input_resolves_to_its_default(self):
        """M7, half two: membership in `input` is the discriminator, not
        truthiness -- a present-but-`None` key does NOT raise."""
        assert resolve_ref("$input.topic", input={"topic": None}, steps=[]) is None

    def test_input_returns_the_input_value(self):
        assert resolve_ref(
            "$input.topic", input={"topic": "quantum computing"}, steps=[]
        ) == "quantum computing"

    def test_steps_0_text_returns_that_steps_text(self):
        step0 = ToolResult(text="hello there")
        assert resolve_ref("$steps.0.text", input={}, steps=[step0]) == "hello there"

    def test_a_dotted_data_walk_indexes_a_list_on_an_all_digit_segment(self):
        """`$steps.0.data.results.0.title` -- the shape a search result
        actually has."""
        step0 = ToolResult(data={"results": [{"title": "first"}, {"title": "second"}]})
        assert resolve_ref(
            "$steps.0.data.results.0.title", input={}, steps=[step0]
        ) == "first"

    def test_artifacts_index_returns_one_reference_string(self):
        step0 = ToolResult(artifacts=("document:1", "output:2"))
        assert resolve_ref("$steps.0.artifacts.0", input={}, steps=[step0]) == "document:1"
        assert resolve_ref("$steps.0.artifacts.1", input={}, steps=[step0]) == "output:2"

    def test_an_out_of_range_step_index_raises_naming_the_reference(self):
        with pytest.raises(FlowReferenceError) as exc:
            resolve_ref("$steps.1.text", input={}, steps=[ToolResult(text="only one")])
        assert "$steps.1.text" in str(exc.value)

    def test_an_out_of_range_list_index_raises_naming_the_reference(self):
        step0 = ToolResult(data={"results": []})
        with pytest.raises(FlowReferenceError) as exc:
            resolve_ref("$steps.0.data.results.5", input={}, steps=[step0])
        assert "$steps.0.data.results.5" in str(exc.value)

    def test_a_missing_dict_key_raises_naming_the_reference(self):
        step0 = ToolResult(data={})
        with pytest.raises(FlowReferenceError) as exc:
            resolve_ref("$steps.0.data.title", input={}, steps=[step0])
        assert "$steps.0.data.title" in str(exc.value)

    def test_a_missing_input_raises_naming_the_reference(self):
        with pytest.raises(FlowReferenceError) as exc:
            resolve_ref("$input.absent", input={"other": 1}, steps=[])
        assert "$input.absent" in str(exc.value)

    def test_a_malformed_reference_raises_naming_it(self):
        with pytest.raises(FlowReferenceError) as exc:
            resolve_ref("$bogus", input={}, steps=[])
        assert "$bogus" in str(exc.value)

    def test_a_malformed_step_reference_with_no_index_raises(self):
        with pytest.raises(FlowReferenceError) as exc:
            resolve_ref("$steps", input={}, steps=[ToolResult(text="x")])
        assert "$steps" in str(exc.value)

    def test_an_unknown_result_field_raises_naming_it(self):
        step0 = ToolResult(text="x")
        with pytest.raises(FlowReferenceError) as exc:
            resolve_ref("$steps.0.bogus_field", input={}, steps=[step0])
        assert "$steps.0.bogus_field" in str(exc.value)

    def test_a_miss_never_returns_none_it_raises(self):
        """Anti-vacuous pin: `resolve_ref` never returns `None` for a
        miss -- `assert pytest.raises` rather than `assert result is
        None`, which a stray `return None` fallback would pass wrongly."""
        with pytest.raises(FlowReferenceError):
            resolve_ref("$steps.99.text", input={}, steps=[])


# --- resolve_args -------------------------------------------------------


class TestResolveArgs:
    def test_a_nested_list_and_dict_is_walked_at_any_depth(self):
        step0 = ToolResult(text="ok")
        result = resolve_args(
            {"filters": [{"a": "$input.topic"}, "$steps.0.text"], "b": {"c": "$input.topic"}},
            input={"topic": "quantum"}, steps=[step0],
        )
        assert result == {
            "filters": [{"a": "quantum"}, "ok"],
            "b": {"c": "quantum"},
        }

    def test_a_non_dollar_value_passes_through_untouched(self):
        assert resolve_args({"query": "plain text", "n": 3}, input={}, steps=[]) == {
            "query": "plain text", "n": 3,
        }

    def test_a_dollar_prefixed_non_reference_is_a_literal(self):
        assert resolve_args({"price": "$5.00"}, input={}, steps=[]) == {"price": "$5.00"}


# --- _run_steps / run_flow -----------------------------------------------


class TestRunSteps:
    def test_both_steps_run_in_order_and_the_recorded_args_prove_the_reference_resolved(self):
        """This is where `$steps.<N>` gets its end-to-end proof (the
        resident flow declares none -- Task 13): a REAL, `Flow.save()`-
        validated row, whose second step's own reference resolves
        against the first step's actual recorded result."""
        _register("flow-test.step-a", "agents.runtime.tests._helpers.runner_ok",
                   params=(Param("in", "text", "In"),))
        _register("flow-test.step-b", "agents.runtime.tests._helpers.runner_ok",
                   params=(Param("ref", "text", "Ref"),))
        flow = _make_flow(
            inputs=[{"key": "topic", "label": "Topic"}],
            steps=[
                {"tool": "flow-test.step-a", "args": {"in": "$input.topic"}},
                {"tool": "flow-test.step-b", "args": {"ref": "$steps.0.data.args.in"}},
            ],
        )

        _run_steps(flow, {"topic": "quantum computing"}, make_tool_ctx())

        assert [call_args for call_args, _ in CALLS] == [
            {"in": "quantum computing"},
            {"ref": "quantum computing"},
        ]

    def test_the_returned_text_is_the_last_steps_text(self):
        flow = _two_step_flow()
        result = _run_steps(flow, {}, make_tool_ctx())
        assert result.text == "step-two-text"

    def test_artifacts_are_the_union_of_every_steps_deduped_order_preserved(self):
        flow = _two_step_flow()
        result = _run_steps(flow, {}, make_tool_ctx())
        assert result.artifacts == ("document:1", "output:2", "document:3")

    def test_data_carries_the_last_steps_data_merged_with_flow_and_steps_summary(self):
        """A closing `rag.ask`'s `citations` reach `citations_of`
        unchanged -- `data["citations"]` here is exactly step two's own,
        untouched by the merge."""
        flow = _two_step_flow()
        result = _run_steps(flow, {}, make_tool_ctx())
        assert result.data["citations"] == [{"title": "two"}]
        assert result.data["flow"] == flow.slug
        assert result.data["steps"] == [
            {"tool": "flow-test.one", "text": "step-one-text"},
            {"tool": "flow-test.two", "text": "step-two-text"},
        ]

    def test_one_toolinvocation_row_per_step(self):
        flow = _two_step_flow()
        _run_steps(flow, {}, make_tool_ctx())
        assert ToolInvocation.objects.count() == 2

    def test_run_flow_input_as_json_string_is_parsed_and_reaches_the_steps(self):
        _register("flow-test.echo", "agents.runtime.tests._helpers.runner_ok",
                   params=(Param("topic", "text", "Topic"),))
        flow = _make_flow(
            inputs=[{"key": "topic", "label": "Topic"}],
            steps=[{"tool": "flow-test.echo", "args": {"topic": "$input.topic"}}],
        )

        run_flow(
            {"flow": flow.slug, "input": json.dumps({"topic": "quantum"})},
            make_tool_ctx(),
        )

        assert CALLS[-1][0] == {"topic": "quantum"}

    def test_run_flow_input_as_dict_is_accepted_unchanged(self):
        _register("flow-test.echo", "agents.runtime.tests._helpers.runner_ok",
                   params=(Param("topic", "text", "Topic"),))
        flow = _make_flow(
            inputs=[{"key": "topic", "label": "Topic"}],
            steps=[{"tool": "flow-test.echo", "args": {"topic": "$input.topic"}}],
        )

        run_flow({"flow": flow.slug, "input": {"topic": "quantum"}}, make_tool_ctx())

        assert CALLS[-1][0] == {"topic": "quantum"}

    def test_run_flow_input_omitted_runs_a_flow_whose_inputs_are_all_optional(self):
        _register("flow-test.noop", "agents.runtime.tests._helpers.runner_ok")
        flow = _make_flow(inputs=[], steps=[{"tool": "flow-test.noop", "args": {}}])

        result = run_flow({"flow": flow.slug}, make_tool_ctx())

        assert result.text == "it worked"

    def test_malformed_json_input_raises_param_error_naming_input(self):
        flow = _make_flow()
        with pytest.raises(ParamError) as exc:
            run_flow({"flow": flow.slug, "input": "{not json"}, make_tool_ctx())
        assert "input" in exc.value.errors

    def test_a_bare_non_object_input_raises_param_error_naming_input(self):
        flow = _make_flow()
        with pytest.raises(ParamError) as exc:
            run_flow({"flow": flow.slug, "input": json.dumps(["a", "b"])}, make_tool_ctx())
        assert "input" in exc.value.errors

    def test_a_step_raising_ends_the_flow_and_later_steps_do_not_run(self):
        _register("flow-test.boom", "agents.runtime.tests._helpers.runner_value_error")
        _register("flow-test.never", "agents.runtime.tests._helpers.runner_ok")
        flow = _make_flow(steps=[
            {"tool": "flow-test.boom", "args": {}},
            {"tool": "flow-test.never", "args": {}},
        ])

        with pytest.raises(ValueError) as exc:
            _run_steps(flow, {}, make_tool_ctx())

        assert "0" in str(exc.value)
        assert "flow-test.boom" in str(exc.value)
        assert len(CALLS) == 1  # the never-step's runner_ok was never called

    def test_a_refused_step_re_raises_tool_refused_no_retry(self):
        _register("flow-test.refuse", "agents.runtime.tests._helpers.runner_refused_subclass")
        flow = _make_flow(steps=[{"tool": "flow-test.refuse", "args": {}}])

        with pytest.raises(ToolRefused):
            _run_steps(flow, {}, make_tool_ctx())

    def test_the_deadline_case_is_degraded_with_the_completed_steps_output(self):
        _register("flow-test.expire", "agents.runtime.tests._helpers.runner_expire_deadline")
        _register("flow-test.never", "agents.runtime.tests._helpers.runner_ok")
        flow = _make_flow(steps=[
            {"tool": "flow-test.expire", "args": {}},
            {"tool": "flow-test.never", "args": {}},
        ])

        result = _run_steps(flow, {}, make_tool_ctx())

        assert result.data["degraded"] is True
        assert result.data["stopped_at_step"] == 1
        assert "step0 finished just before the deadline moved" in result.text
        assert len(CALLS) == 1  # the never-step was never reached

    def test_the_deadline_case_classifies_as_degraded_through_invoke_tool(self):
        """Drive the assertion through `invoke_tool` itself, so the
        opt-in flag (`data["degraded"] = True`) and its classifier
        (`agents.runtime.invoke.invoke_tool`) are proven to agree."""
        _register("flow-test.expire", "agents.runtime.tests._helpers.runner_expire_deadline")
        _register("flow-test.never", "agents.runtime.tests._helpers.runner_ok")
        flow = _make_flow(steps=[
            {"tool": "flow-test.expire", "args": {}},
            {"tool": "flow-test.never", "args": {}},
        ])
        flow_spec = ToolSpec(
            key="flow.run", label="Run a flow", description="a flow-test stub spec",
            params=(
                Param("flow", "choice", "Flow", required=True),
                Param("input", "text", "Input", required=False),
            ),
            runner="agents.runtime.flow.run_flow",
        )

        outcome = invoke_tool(flow_spec, {"flow": flow.slug}, make_tool_ctx())

        assert outcome.outcome == ToolInvocation.Outcome.DEGRADED
        assert outcome.failed is False
        assert outcome.result.data["degraded"] is True

    def test_the_deadline_already_exhausted_before_step_0_is_degraded_with_no_fabricated_text(
        self,
    ):
        """Review fix wave item 8 -- the ZERO-completed-steps edge of
        `_degraded` (`agents/runtime/flow.py:363-390`), distinct from
        `test_the_deadline_case_is_degraded_with_the_completed_steps_
        output` above (which stops after ONE completed step). Here the
        budget is already expired before `_run_steps`'s own loop ever
        reaches step 0 -- `get_tool` is never even called, so the step's
        own tool need not be registered -- and the result must still be
        `degraded`, must name step 0 as where it stopped, and must never
        claim a step "returned" something when none has run this turn
        (the same honesty rule `loop._honest_ending` holds)."""
        import time

        flow = _make_flow(steps=[{"tool": "flow-test.never-reached", "args": {}}])
        expired_budget = make_budget(deadline_monotonic=time.monotonic() - 1)

        result = _run_steps(flow, {}, make_tool_ctx(budget=expired_budget))

        assert result.data["degraded"] is True
        assert result.data["stopped_at_step"] == 0
        assert result.text == (
            f"The turn ran out of time before step 0 of flow {flow.slug!r}."
        )
        assert "Completed" not in result.text   # no "Completed N of M" trailer
        assert result.artifacts == ()
        assert CALLS == []                       # no step runner ever called

    def test_an_unregistered_step_tool_is_refused_naming_the_key(self):
        flow = _make_flow(steps=[{"tool": "flow-test.nope-really", "args": {}}])

        with pytest.raises(ToolRefused) as exc:
            _run_steps(flow, {}, make_tool_ctx())

        assert "flow-test.nope-really" in str(exc.value)

    def test_a_mutating_step_tool_is_refused(self):
        """The ADR 0010 bypass guard: `granted_tools` filters an AGENT's
        keys and never sees a flow's steps, so without this check a flow
        would be a way around "no settings-mutating tool is grantable"
        (ADR 0010:266-276)."""
        _register("flow-test.mutator", "agents.runtime.tests._helpers.runner_ok",
                   mutates=True)
        flow = _make_flow(steps=[{"tool": "flow-test.mutator", "args": {}}])

        with pytest.raises(ToolRefused):
            _run_steps(flow, {}, make_tool_ctx())

    def test_run_flow_with_no_visible_row_is_refused_no_retry(self):
        """The model was handed an `enum` of real slugs (Task 13), so
        reaching here means the row was disabled or deleted between the
        prompt and the call -- a race, not a state to retry."""
        with pytest.raises(ToolRefused) as exc:
            run_flow({"flow": "does-not-exist-at-all"}, make_tool_ctx())
        assert "does-not-exist-at-all" in str(exc.value)

    def test_run_flow_against_a_disabled_row_is_refused_the_same_way(self):
        """Driven through `visible_flows` (which filters `enabled=True`)
        rather than a second `enabled` check in the runner, so the tool
        list and the runner can never disagree about which flows
        exist."""
        flow = _make_flow(enabled=False)

        with pytest.raises(ToolRefused) as exc:
            run_flow({"flow": flow.slug}, make_tool_ctx())
        assert flow.slug in str(exc.value)


# --- Budget: a flow spends none of its own steps -------------------------


class TestBudget:
    def test_a_flow_of_three_steps_spends_no_steps_of_the_shared_budget(self):
        """A step of the budget is an LLM call, and a flow makes none;
        one whole flow is one step of whatever loop invoked it. This is
        the property that makes "one flow = one loop step" true."""
        _register("flow-test.noop3", "agents.runtime.tests._helpers.runner_ok")
        flow = _make_flow(steps=[
            {"tool": "flow-test.noop3", "args": {}},
            {"tool": "flow-test.noop3", "args": {}},
            {"tool": "flow-test.noop3", "args": {}},
        ])
        budget = make_budget(steps=5)
        ctx = make_tool_ctx(budget=budget)

        _run_steps(flow, {}, ctx)

        assert budget.steps_left == 5
