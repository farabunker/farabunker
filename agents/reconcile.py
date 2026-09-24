"""A chat turn whose job row vanished recovers (owner decision 7, spec
§3.8, §9.15).

DELIBERATELY NOT IN `agents/runtime/`. `reconcile_stranded_turn` calls
`models.contracts.queue.get_job` -- that IS the check, since the only way
to know a job row is gone is to ask the queue -- but every module under
`agents/runtime/` is swept by `foundation/ops/tests/test_column_
boundaries.py::test_no_runtime_module_blocks_on_a_queue_job` for exactly
that call: that sweep exists because a turn's planner/loop/invoker/hooks
all execute INSIDE the `agent.turn` job, holding the machine's one
execution slot in sequential mode, and a `get_job` call from in there can
deadlock (`models/queue/scheduler.py:374-380`'s sequential-mode note).
This module never runs inside a job -- it is called from the poll view
(`agents.chat.views.turns._queued_body`/`_running_body`) and from
`manage.py reconcile_turns`, the same two kinds of caller
`test_the_queue_polling_callers_really_poll_and_are_really_excluded`
already names as legitimate (`agents/chat/views/turns.py`,
`agents/management/commands/agent_turn.py`) -- a view answers one
request and returns, a management command stands outside the queue
waiting for it, and neither ever holds the execution slot the sweep is
protecting. It lives beside `agents/limits.py`, `agents/visibility.py`
and this column's other top-level modules for the same reason.

Reuses `on_turn_terminal`'s (`agents/runtime/jobs.py`) exact
conditional-UPDATE write shape and its own `close_open_invocations` call
-- the mechanics are identical, only the trigger differs: `on_turn_
terminal` is driven by the job row's own terminal write; this is driven
by the job row not existing at all.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from agents.models import Turn
from agents.runtime.audit import close_open_invocations
from models.contracts.queue import get_job

# How long an assistant turn may sit queued/running with no job row
# behind it before it is treated as STRANDED. Comfortably longer than the
# enqueue-then-commit window, so a turn whose job row simply has not been
# written yet is never touched -- that window is milliseconds, and this is
# the difference between repairing a broken row and racing a healthy one.
STRANDED_TURN_GRACE_SECONDS = 60

_STRANDED_ERROR = "The queue lost this turn before it ran; nothing was retried."


def _stranded_candidates(grace_seconds: int = STRANDED_TURN_GRACE_SECONDS):
    """Every non-terminal assistant turn older than `grace_seconds` --
    the AGE AND STATE half of the condition only, not yet checked against
    the queue. This is the candidate set `reconcile_stranded_turns` walks
    and `manage.py reconcile_turns --dry-run` counts against (through
    `_is_stranded` below) without writing anything."""
    cutoff = timezone.now() - timedelta(seconds=grace_seconds)
    return Turn.objects.filter(
        role=Turn.Role.ASSISTANT,
        state__in=(Turn.State.QUEUED, Turn.State.RUNNING),
        created_at__lt=cutoff,
    )


def _is_stranded(turn, grace_seconds: int = STRANDED_TURN_GRACE_SECONDS) -> bool:
    """THE CONDITION, deliberately narrow (spec §3.8): an ASSISTANT turn,
    still `queued` or `running`, whose `queue_job_id` is null or names a
    job row that NO LONGER EXISTS, and whose row has been in that state
    longer than `grace_seconds`.

    `grace_seconds` IS THE CONDITION, NOT JUST THE CANDIDATE FILTER: a
    caller that narrows `reconcile_stranded_turns`' own `grace_seconds`
    must see the age check narrow too, or a shorter grace would widen the
    candidate set (`_stranded_candidates`, below) and then have every row
    younger than the module default rejected here anyway -- closing
    nothing and reporting a clean sweep for a value the caller explicitly
    asked to act sooner on. Review round 1, finding 1.

    "HOW LONG THE ROW HAS BEEN IN THAT STATE" READS `created_at`: `Turn`
    carries no per-state timestamp (only `created_at`, `auto_now_add`),
    and an ASSISTANT turn is written QUEUED at the moment it is created
    -- its creation IS the start of its queued/running life, so there is
    nothing else on the row to read this from.

    The READ-ONLY half of `reconcile_stranded_turn`'s condition, shared
    with `count_stranded_turns` (`manage.py reconcile_turns --dry-run`'s
    own answer, via `_stranded_candidates` above) so a dry run reports
    the exact count a real run would close, never an approximation that
    skips the one check -- whether the job row actually exists -- that
    only a live `get_job` can answer.
    """
    if turn.role != Turn.Role.ASSISTANT:
        return False
    if turn.state not in (Turn.State.QUEUED, Turn.State.RUNNING):
        return False
    if timezone.now() - turn.created_at < timedelta(seconds=grace_seconds):
        return False
    return not (turn.queue_job_id and get_job(turn.queue_job_id) is not None)


def reconcile_stranded_turn(turn, grace_seconds: int = STRANDED_TURN_GRACE_SECONDS) -> bool:
    """Close `turn` honestly if -- and only if -- its job row is gone, and
    report whether THIS call is what closed it.

    THE CONDITION is `_is_stranded` above; see it for the exact three
    facts read, including why `grace_seconds` -- default `STRANDED_TURN_
    GRACE_SECONDS`, which is what every existing call site still gets --
    is threaded through rather than fixed at the module constant.

    WHY IT EXISTS: `agents.runtime.jobs.on_turn_terminal`, the hook that
    closes a placeholder turn, is driven by the JOB ROW's own terminal
    write. A database crash that rolls the job row back leaves nothing to
    drive it, and the turn sits at "working" for ever -- recovery today
    is "delete the conversation and resend".

    OWNED BY THIS COLUMN, necessarily: the queue cannot know which turn
    points at which job, and may not import the agent layer at all.

    ONE CONDITIONAL UPDATE, filtered on the two non-terminal states --
    the exact shape `on_turn_terminal` uses, and for the same reason: a
    still-alive worker can be writing DONE in the window between this
    read and this write, and the `state__in` guard makes clobbering it
    impossible. Idempotent by construction, which is what makes it safe
    to call from a polled GET (owner decision 7).

    It also closes the turn's open invocation rows -- which closes rows
    where a job id was stamped, and has nothing to close on the
    null-`queue_job_id` half of the condition:
    `close_open_invocations` returns zero for a falsy job id BY DESIGN,
    since nothing was ever stamped with one.
    """
    if not _is_stranded(turn, grace_seconds):
        return False

    updated = Turn.objects.filter(
        pk=turn.pk, state__in=(Turn.State.QUEUED, Turn.State.RUNNING),
    ).update(state=Turn.State.FAILED, error=_STRANDED_ERROR)
    if updated:
        close_open_invocations(turn.queue_job_id, reason=_STRANDED_ERROR)
    return bool(updated)


def reconcile_stranded_turns(grace_seconds: int = STRANDED_TURN_GRACE_SECONDS) -> int:
    """Walk every non-terminal assistant turn older than `grace_seconds`
    and close the ones whose job row is gone, returning how many that
    was.

    ONE `get_job` PER CANDIDATE (via `reconcile_stranded_turn`), which is
    bounded because the condition is rare -- the poll path already
    reconciles the one row a live operator is actually watching, so this
    walk exists for the row nobody is polling, and the command that calls
    it (`manage.py reconcile_turns`) is an operator action, never a loop.

    NO PERIODIC BACKGROUND SWEEPER calls this. The condition is rare, the
    read surface already visits exactly the row that matters, and an
    always-on sweeper is machinery this evidence does not justify.
    """
    return sum(
        1 for candidate in _stranded_candidates(grace_seconds)
        if reconcile_stranded_turn(candidate, grace_seconds)
    )


def count_stranded_turns(grace_seconds: int = STRANDED_TURN_GRACE_SECONDS) -> int:
    """The read-only half of `reconcile_stranded_turns`: the exact count a
    real run would close, computed without writing anything --
    `manage.py reconcile_turns --dry-run`'s own answer.

    The PUBLIC counterpart to reaching for `_is_stranded`/`_stranded_
    candidates` directly: those two stay module-private, and a caller
    outside this module (the management command) gets this instead of an
    import of underscored names across the package boundary. Review
    round 1, finding 2.
    """
    return sum(
        1 for candidate in _stranded_candidates(grace_seconds)
        if _is_stranded(candidate, grace_seconds)
    )
