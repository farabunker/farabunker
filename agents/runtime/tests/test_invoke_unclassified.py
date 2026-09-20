"""C-6 (security round 3, H38): the unclassified `except Exception`
branch in `invoke_tool` (`agents/runtime/invoke.py`) says nothing
specific to the model or the page.

Before this fix, that branch finished the invocation with the
exception's own `str(exc)` as BOTH `text` and `error`. `text` is what
the loop writes into the turn, appends to the live message list, and
what `agents/chat/rendering.py` renders on the tool card -- so whatever
a third-party library chose to say (a database error text, an OS error
naming an absolute path, an HTTP error naming the engine endpoint)
reached a share recipient reading the thread page (C-1, H25, is what
makes that reachable). The fix keeps the full string exactly where an
operator already reads it -- `ToolInvocation.error` and the log -- and
hands the model and the page a fixed sentence plus the invocation id
that joins the two.

This module is new and separate from `test_invoke.py` (H38 addendum):
`test_invoke.py` is untouched by this task, and its own
`test_a_non_value_error_exception_is_caught_and_classified_as_error`
(a plain `RuntimeError`) still only asserts the outcome classifies as
`error` and that no traceback leaks -- both still true.

`ToolRefused`, `ParamError` and the plain `ValueError` branch above the
catch-all are deliberate, platform-authored copy and out of scope --
this task touches exactly one branch, and
`test_the_three_typed_branches_are_unchanged` below proves it left them
alone.
"""
from __future__ import annotations

import logging

import pytest

from agents.contracts.tools import ToolSpec
from agents.models import ToolInvocation
from agents.runtime.invoke import invoke_tool
from agents.runtime.tests._helpers import isolated_tool_registry, make_tool_ctx

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]


def _spec(runner: str, **overrides) -> ToolSpec:
    fields = dict(key="stub.tool", label="Stub", description="A stub.", runner=runner)
    fields.update(overrides)
    return ToolSpec(**fields)


class TestC6TheUnclassifiedBranchSaysNothingSpecific:
    def test_the_model_gets_a_fixed_sentence_and_an_id(self):
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_os_error"), {}, make_tool_ctx()
        )
        assert "/srv/data" not in out.text
        assert str(out.invocation_id) in out.text

    def test_the_exception_class_name_is_the_most_it_carries(self):
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_os_error"), {}, make_tool_ctx()
        )
        assert "OSError" in out.text
        assert "secret.env" not in out.text

    def test_the_full_string_is_still_on_the_invocation_row(self):
        out = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_os_error"), {}, make_tool_ctx()
        )
        row = ToolInvocation.objects.get(pk=out.invocation_id)
        assert "/srv/data/secret.env" in row.error

    def test_it_is_still_logged_for_the_operator(self, caplog):
        with caplog.at_level(logging.ERROR, logger="agents.runtime.invoke"):
            out = invoke_tool(
                _spec("agents.runtime.tests._helpers.runner_os_error"), {}, make_tool_ctx()
            )
        assert any(
            "/srv/data/secret.env" in record.getMessage() or record.exc_text
            for record in caplog.records
        )
        assert any("stub.tool" in record.getMessage() for record in caplog.records)
        assert any(
            str(out.invocation_id) in record.getMessage() for record in caplog.records
        )

    def test_the_three_typed_branches_are_unchanged(self):
        """`ToolRefused`, `ParamError` and the plain `ValueError` branch
        all carry deliberate, platform-authored copy. This task touches
        the catch-all branch only."""
        refused = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_refused_subclass"), {}, make_tool_ctx()
        )
        assert refused.outcome == ToolInvocation.Outcome.REFUSED
        assert refused.text == "this tool is not granted"

        param_error = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_param_error_subclass"),
            {}, make_tool_ctx(),
        )
        assert param_error.outcome == ToolInvocation.Outcome.PARAM_ERROR
        assert "top_k" in param_error.text

        value_error = invoke_tool(
            _spec("agents.runtime.tests._helpers.runner_value_error"), {}, make_tool_ctx()
        )
        assert value_error.outcome == ToolInvocation.Outcome.ERROR
        assert value_error.text == "no row matches that reference"
