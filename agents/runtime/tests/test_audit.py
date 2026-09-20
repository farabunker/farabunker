"""`agents.runtime.audit` -- the three invocation-state truths every
renderer depends on (P2 ledger, deferred to P3).

These are not cosmetic. An open row that reads as an error would make
a working tool call render red, and the live 2026-08-28 incident
proved a crashed call leaves a row open forever with nothing to close
it.
"""
from __future__ import annotations

import pytest
from django.utils import timezone

from agents.models import ToolInvocation
from agents.runtime.audit import (
    RUNNING, close_open_invocations, invocation_message, invocation_state,
)

pytestmark = pytest.mark.django_db


def _open_row(**overrides):
    fields = dict(
        principal_kind="resident_agent", principal_key="general",
        tool_key="rag.search", args={"query": "q"},
        outcome=ToolInvocation.Outcome.ERROR, queue_job_id=7,
    )
    fields.update(overrides)
    return ToolInvocation.objects.create(**fields)


class _BoomManager:
    """A manager stand-in whose `filter` raises, so the never-raises
    guarantee is proven against a real exception rather than asserted."""

    def filter(self, *args, **kwargs):
        raise RuntimeError("database is gone")


class TestInvocationState:
    def test_an_unfinished_row_reads_as_running_not_as_its_placeholder_outcome(self):
        """`invoke_tool` creates the row with `outcome=ERROR` as a
        placeholder it overwrites in `_finish`. Until then the ONLY
        honest reading is `finished_at`."""
        row = _open_row()
        assert row.outcome == ToolInvocation.Outcome.ERROR
        assert invocation_state(row) == RUNNING

    def test_a_finished_row_reads_as_its_own_outcome(self):
        row = _open_row(outcome=ToolInvocation.Outcome.OK,
                        finished_at=timezone.now())
        assert invocation_state(row) == ToolInvocation.Outcome.OK

    def test_no_invocation_at_all_is_the_empty_string_not_an_error(self):
        """`Turn.invocation` is SET_NULL, so a pruned audit row leaves
        a TOOL turn whose outcome is genuinely unknown. Rendering that
        as a failure would invent one."""
        assert invocation_state(None) == ""


class TestInvocationMessage:
    def test_a_failure_reads_its_error_because_text_is_blank_on_that_path(self):
        """`invoke.py::_finish` writes `row.text` ONLY when a
        `ToolResult` came back, so every failure path stores its words
        in `error`. A renderer reading `text` would show an empty
        card."""
        row = _open_row(outcome=ToolInvocation.Outcome.ERROR,
                        text="", error="the engine refused the request",
                        finished_at=timezone.now())
        assert invocation_message(row) == "the engine refused the request"

    def test_the_turns_own_text_wins_when_there_is_one(self):
        """`Turn.text` is what the MODEL was told, discards clause and
        all (`loop._tool_message_text`), so it is strictly the fuller
        sentence."""
        row = _open_row(outcome=ToolInvocation.Outcome.OK, text="two results",
                        finished_at=timezone.now())
        told = "two results\n\n(Only rag.search was run this step.)"
        assert invocation_message(row, told) == told

    def test_an_open_row_says_it_is_still_running_rather_than_nothing(self):
        assert "still running" in invocation_message(_open_row()).lower()


class TestCloseOpenInvocations:
    def test_it_closes_only_the_named_jobs_unfinished_rows(self):
        mine = _open_row(queue_job_id=7)
        other_job = _open_row(queue_job_id=8)
        already_done = _open_row(queue_job_id=7,
                                 outcome=ToolInvocation.Outcome.OK,
                                 text="fine", finished_at=timezone.now())

        moved = close_open_invocations(7, reason="the turn stopped")

        assert moved == 1
        mine.refresh_from_db()
        other_job.refresh_from_db()
        already_done.refresh_from_db()
        assert mine.finished_at is not None
        assert mine.error == "the turn stopped"
        assert other_job.finished_at is None
        # An OK row is never rewritten: that call finished, and this
        # helper's whole subject is calls that did not.
        assert already_done.outcome == ToolInvocation.Outcome.OK
        assert already_done.error == ""

    def test_a_null_job_id_closes_nothing_and_does_not_raise(self):
        """A turn whose `queue_job_id` was never stamped has nothing to
        correlate on. Closing every open row on the box instead would
        be far worse than closing none."""
        _open_row(queue_job_id=None)
        assert close_open_invocations(None, reason="x") == 0

    def test_it_never_raises_even_when_the_write_itself_fails(self, monkeypatch):
        """Both callers are terminal paths already handling a failure.
        A helper that raised there would replace the real reason a turn
        died with its own."""
        monkeypatch.setattr(
            "agents.runtime.audit.ToolInvocation.objects", _BoomManager(),
        )
        assert close_open_invocations(7, reason="x") == 0
