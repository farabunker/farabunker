"""How an audit row READS -- the one place `ToolInvocation` is
interpreted, and the one place an unfinished one is closed.

`agents.runtime.invoke.invoke_tool` creates a `ToolInvocation` BEFORE
it runs the runner, with `outcome=ERROR` as a placeholder it
overwrites once it knows better. That is the right shape -- a row
exists even if the process dies -- but it means `outcome` alone is not
a readable state, and any renderer reading it alone would show a red
card for a call that is working. `finished_at` is the discriminator,
and `invocation_state` is the only place that rule lives.

It also means a crashed call leaves a row open forever. Observed live
on 2026-08-28: a delegate's `agent.library` invocation was open when
the database entered recovery, and nothing existed to close it.
`close_open_invocations` is that something, called from the two
terminal paths -- `run_turn`'s own `except` (the handler ran and
raised) and `on_turn_terminal` (the handler never started).

Pure reads and one conditional UPDATE. It never raises: both callers
are already handling a failure, and a helper that raised there would
replace the real reason a turn died with its own.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from agents.models import ToolInvocation

logger = logging.getLogger(__name__)

# Not a member of `ToolInvocation.Outcome`, deliberately: an outcome is
# what a FINISHED call produced, and "running" is the absence of one.
# Putting it in the enum would put a non-outcome in the column every
# later report is built on.
RUNNING = "running"

_STILL_RUNNING = "This tool call is still running."


def invocation_state(invocation) -> str:
    """`"running"`, one of `ToolInvocation.Outcome`'s five values, or
    `""` for no row at all.

    `""` is not an error either: `Turn.invocation` is `SET_NULL`, so a
    pruned audit row leaves a TOOL turn whose outcome is genuinely
    unknown, and rendering that as a failure would invent one.
    """
    if invocation is None:
        return ""
    if invocation.finished_at is None:
        return RUNNING
    return invocation.outcome


def invocation_message(invocation, turn_text: str = "") -> str:
    """The sentence to show for one tool call.

    `turn_text` -- `Turn.text`, what the MODEL was told -- wins
    whenever it exists, because `agents.runtime.loop._tool_message_text`
    builds it from the runner's own words PLUS the discarded-calls
    clause, so it is strictly the fuller sentence.

    Falls back to `invocation.error`, NOT to `invocation.text`:
    `invoke.py::_finish` writes `text` only when a `ToolResult` came
    back, so on every refusal, param error, and raise it is blank and
    the words are in `error`.
    """
    if turn_text:
        return turn_text
    if invocation is None:
        return ""
    if invocation.finished_at is None:
        return _STILL_RUNNING
    return invocation.error or invocation.text or ""


def close_open_invocations(queue_job_id, *, reason: str) -> int:
    """Close every unfinished `ToolInvocation` stamped with
    `queue_job_id`, and return how many moved.

    ONE conditional UPDATE, filtered on `finished_at__isnull=True`, so
    a call that finished in the window between this being scheduled
    and running is never rewritten -- the same shape
    `on_turn_terminal`'s own `state__in` guard uses, for the same
    reason.

    `outcome` is left at whatever the row holds, which for an open row
    is always the `ERROR` placeholder `invoke_tool` created it with. A
    call interrupted before it reported did in fact not succeed, and
    inventing a sixth outcome class for it would put a value in the
    column that no runner can ever produce.

    A falsy job id closes NOTHING. A turn whose `queue_job_id` was
    never stamped has nothing to correlate on, and closing every open
    row on the box instead would be far worse than closing none.

    NEVER RAISES.
    """
    if not queue_job_id:
        return 0
    try:
        return ToolInvocation.objects.filter(
            queue_job_id=queue_job_id, finished_at__isnull=True,
        ).update(finished_at=timezone.now(), error=reason)
    except Exception:  # noqa: BLE001 -- never mask the failure being cleaned up after
        logger.exception(
            "agents: could not close the open tool-invocation rows for queue job %r",
            queue_job_id,
        )
        return 0
