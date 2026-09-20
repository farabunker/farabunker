"""One tool call, four outcome classes, and the ordering that keeps them
apart.

`ToolRefused` and `ParamError` are BOTH `ValueError` subclasses
(`agents/contracts/tools.py:46`, `models/contracts/operations.py:228`).
`agents/contracts/README.md`'s "Two failure classes" section is the
authority on what that means: a handler that reaches `except ValueError`
first never lands in either narrower branch, and every honest refusal
becomes whatever the wide clause does. These tests plant a SUBCLASS of
each so a reordered try/except is caught by classification, not by luck.
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolRefused, ToolResult, ToolSpec
from agents.models import ToolInvocation
from agents.runtime.invoke import invoke_tool, invoke_unknown_tool
from agents.runtime.tests import _helpers
from agents.runtime.tests._helpers import (  # noqa: F401
    isolated_tool_registry, make_job_ctx, make_tool_ctx,
)
from models.contracts.operations import Param, ParamError

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]


def _spec(runner: str, **overrides) -> ToolSpec:
    fields = dict(key="stub.tool", label="Stub", description="A stub.", runner=runner)
    fields.update(overrides)
    return ToolSpec(**fields)


class TestOutcomeClassification:
    def test_a_returned_result_is_ok(self):
        out = invoke_tool(_spec("agents.runtime.tests._helpers.runner_ok"), {}, make_tool_ctx())
        assert out.outcome == ToolInvocation.Outcome.OK
        assert out.text == "it worked"
        assert out.failed is False

    def test_a_result_flagged_degraded_is_degraded_not_ok(self):
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_degraded"), {}, make_tool_ctx()
        )
        assert out.outcome == ToolInvocation.Outcome.DEGRADED
        assert out.failed is False        # degraded is NOT a failure
        assert "queue" in out.text

    def test_a_tool_refused_subclass_classifies_as_refused_not_error(self):
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_refused_subclass"), {}, make_tool_ctx()
        )
        assert out.outcome == ToolInvocation.Outcome.REFUSED
        assert out.failed is True
        assert out.bars_retry is True

    def test_a_param_error_subclass_classifies_as_param_error(self):
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_param_error_subclass"), {},
            make_tool_ctx(),
        )
        assert out.outcome == ToolInvocation.Outcome.PARAM_ERROR
        assert out.bars_retry is False

    def test_a_param_error_feeds_back_its_per_arg_reasons(self):
        """`.errors` maps a param key to a human-readable reason
        (`operations.py:228-236`), which is exactly the information a
        model needs to fix its own call -- and the reason this is the
        one class worth a repair attempt."""
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_param_error_subclass"), {},
            make_tool_ctx(),
        )
        assert "top_k" in out.text and "too large" in out.text

    def test_a_plain_value_error_classifies_as_error(self):
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_value_error"), {}, make_tool_ctx()
        )
        assert out.outcome == ToolInvocation.Outcome.ERROR
        assert out.bars_retry is False

    def test_a_non_value_error_exception_is_caught_and_classified_as_error(self):
        """Never-500: a runner that raises something nobody classified
        still becomes an honest tool error with `str(exc)`, never a
        traceback and never a crashed turn."""
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_runtime_error"), {}, make_tool_ctx()
        )
        assert out.outcome == ToolInvocation.Outcome.ERROR
        assert "Traceback" not in out.text

    def test_a_misconfigured_runner_path_classifies_as_error_not_importerror(self):
        """Task 14 review: a `ToolSpec.runner` that does not resolve --
        module moved, typo'd, a hand-built spec in a test double -- must
        become an honest `error` outcome with its own finished
        `ToolInvocation` row, not an uncaught `ImportError` out of a job
        handler. `models/registry/tests/test_registry_paths.py` proves
        every REGISTERED path resolves; this proves what happens when
        one doesn't."""
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.no_such_function"), {}, make_tool_ctx()
        )
        assert out.outcome == ToolInvocation.Outcome.ERROR
        assert out.failed is True
        assert "stub.tool" in out.text
        row = ToolInvocation.objects.get()
        assert row.finished_at is not None
        assert row.error

    def test_validation_runs_before_the_runner_is_even_resolved(self):
        """A bad argument must not reach a runner at all -- the schema
        floor is the first gate, and a runner that never ran cannot have
        side effects to undo."""
        spec = _spec(
            "agents.runtime.tests._helpers.runner_ok",
            params=(Param("query", "text", "Query", required=True),),
        )
        _helpers.CALLS.clear()
        out = invoke_tool(spec, {}, make_tool_ctx())
        assert out.outcome == ToolInvocation.Outcome.PARAM_ERROR
        assert _helpers.CALLS == []


class TestTheAuditRow:
    def test_every_call_writes_exactly_one_invocation_row(self):
        invoke_tool(_spec("agents.runtime.tests._helpers.runner_ok"), {}, make_tool_ctx())
        assert ToolInvocation.objects.count() == 1

    def test_the_row_carries_the_principal_and_the_validated_args(self):
        spec = _spec(
            "agents.runtime.tests._helpers.runner_ok",
            params=(Param("query", "text", "Query", required=True),),
        )
        invoke_tool(spec, {"query": "hello"}, make_tool_ctx())
        row = ToolInvocation.objects.get()
        assert (row.principal_kind, row.principal_key) == ("resident_agent", "test-agent")
        assert row.tool_key == "stub.tool"
        assert row.args == {"query": "hello"}

    def test_a_failure_row_carries_the_error_and_no_result_text(self):
        invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_value_error"), {}, make_tool_ctx()
        )
        row = ToolInvocation.objects.get()
        assert row.error and row.text == ""

    def test_the_row_is_finished_and_has_a_duration(self):
        invoke_tool(_spec("agents.runtime.tests._helpers.runner_ok"), {}, make_tool_ctx())
        row = ToolInvocation.objects.get()
        assert row.finished_at is not None
        assert row.duration_ms is not None and row.duration_ms >= 0

    def test_a_row_is_written_even_when_validation_fails(self):
        """An invocation that never reached its runner still HAPPENED. A
        principal that spends a turn sending malformed calls must be
        visible in the same table as one that succeeds."""
        spec = _spec(
            "agents.runtime.tests._helpers.runner_ok",
            params=(Param("query", "text", "Query", required=True),),
        )
        invoke_tool(spec, {}, make_tool_ctx())
        assert ToolInvocation.objects.get().outcome == ToolInvocation.Outcome.PARAM_ERROR


class TestToolKeyIsThreadedIn:
    def test_the_runner_sees_its_own_spec_key_on_the_context(self):
        """Deviation D4's mechanism: N `agent.<slug>` specs share one
        runner, and `ctx.tool_key` is the only way one can learn which
        spec invoked it."""
        _helpers.CALLS.clear()
        invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_ok", key="agent.library"),
            {}, make_tool_ctx(),
        )
        _args, ctx = _helpers.CALLS[-1]
        assert ctx.tool_key == "agent.library"

    def test_the_callers_context_is_not_mutated(self):
        """`ToolContext` is frozen; `invoke_tool` builds a replacement
        rather than reaching into the caller's."""
        ctx = make_tool_ctx()
        invoke_tool(_spec("agents.runtime.tests._helpers.runner_ok"), {}, ctx)
        assert ctx.tool_key == ""


class TestUnknownToolName:
    def test_an_unknown_wire_name_is_a_tool_error_with_an_audit_row(self):
        """Section 10.3: never guessed at, never fuzzy-matched, never
        silently ignored."""
        out = invoke_unknown_tool("no__such__tool", {"a": 1}, make_tool_ctx())
        assert out.outcome == ToolInvocation.Outcome.ERROR
        assert out.failed is True
        row = ToolInvocation.objects.get()
        assert row.tool_key == "no.such.tool"      # round-tripped, then reported honestly
        assert "no.such.tool" in out.text

    def test_it_still_records_whose_declaration_was_in_force(self):
        """Review finding 5 (IA-2 T5 fix round 1): a hallucinated wire
        name never resolves to a real `ToolSpec`, but the AGENT whose
        prompt produced it is still known -- `tool_ctx.agent_slug` is
        already populated by the time this runner is reached. Leaving
        it blank here would contradict `agents/models.py`'s own column
        doc ("blank... for a call with no agent at all") for a row that
        plainly has one, and a hallucinated-tool call is exactly the
        kind of row an operator investigates by agent."""
        ctx = make_tool_ctx(agent_slug="library")
        out = invoke_unknown_tool("no__such__tool", {}, ctx)
        row = ToolInvocation.objects.get(pk=out.invocation_id)
        assert row.agent_slug == "library"


class TestQueueJobStamping:
    def test_every_invocation_records_the_job_it_ran_inside(self):
        """Without this stamp `close_open_invocations` has nothing to
        correlate on, and a crashed call stays open forever (the live
        2026-08-28 incident)."""
        ctx = make_tool_ctx(job=make_job_ctx(job_id=4242))
        outcome = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_ok"), {}, ctx,
        )
        assert ToolInvocation.objects.get(pk=outcome.invocation_id).queue_job_id == 4242

    def test_an_unknown_tool_call_is_stamped_too(self):
        """A second creation site that forgot the stamp would leave
        exactly the rows hardest to explain."""
        ctx = make_tool_ctx(job=make_job_ctx(job_id=4242))
        outcome = invoke_unknown_tool("no__such", {}, ctx)
        assert ToolInvocation.objects.get(pk=outcome.invocation_id).queue_job_id == 4242
