"""One tool call: validate, run, classify, audit.

EVERY tool call this platform makes goes through `invoke_tool` -- an
agent's own loop, an agent-as-tool delegate, and (once the MCP edge
lands, 2026-08-27 addendum) an external `tools/call`. That is why the
audit row it writes is keyed on a PRINCIPAL and not on a conversation,
and why this function never assumes a `Turn` exists.

IT NEVER RAISES FOR A TOOL FAILURE. A failure is a classified outcome,
because the loop's recovery policy is defined per class and a caller
that had to re-derive the class from an exception type would be a second
copy of the rule below.

THE EXCEPT ORDERING IS LOAD BEARING, and
`agents/contracts/README.md`'s "Two failure classes, and two more
outcomes beside them" section is its authority. `ToolRefused`
(`agents/contracts/tools.py:46`) and `ParamError`
(`models/contracts/operations.py:228`) are BOTH `ValueError` subclasses.
Catching `ValueError` before either narrower clause means never reaching
them: every honest refusal and every repairable argument error alike
falls into the wide branch. So:

    except ToolRefused -> refused
    except ParamError  -> param_error
    except ValueError  -> error
    except Exception   -> error        (never-500: a fixed sentence naming the
                                         invocation id to the model; str(exc)
                                         and a traceback to the operator only,
                                         via `ToolInvocation.error` and the log)

Reordering these is a silent behaviour change, not a style choice.

`degraded` is an OPT-IN a runner declares with `data["degraded"] = True`
-- it cannot be inferred from a `ToolResult` generically. `agents.
runtime.flow._degraded` is its first shipped producer, for
a flow that ran out of time partway through and whose completed steps'
work is worth keeping rather than discarding.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from django.utils import timezone

from agents.contracts.tools import (
    ToolContext, ToolRefused, ToolResult, ToolSpec, validate_tool_args,
)
from agents.contracts.toolschema import key_from_wire_name
from agents.models import ToolInvocation
from models.contracts.jobkinds import resolve_dotted_path
from models.contracts.operations import ParamError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolOutcome:
    """What one tool call produced, as a closed classification.

    `text` is what the model is told -- the ONLY part of this a prompt
    ever sees. `result` is the runner's own `ToolResult` on a
    non-failure and `None` otherwise, so the loop can read `.data` and
    `.artifacts` without a second lookup. `args` is the VALIDATED
    argument dict (the raw ones when validation itself failed), so the
    loop records in `Turn.tool_call["args"]` what actually ran rather
    than what was asked for -- that dict is replayed into a later
    prompt, and replaying a rejected argument would teach the model it
    was accepted.
    """

    outcome: str
    text: str
    args: dict
    result: ToolResult | None
    invocation_id: int | None

    @property
    def failed(self) -> bool:
        """`degraded` is deliberately NOT a failure: the call succeeded
        and what it has to report is a less-than-ideal state."""
        return self.outcome in (
            ToolInvocation.Outcome.REFUSED,
            ToolInvocation.Outcome.PARAM_ERROR,
            ToolInvocation.Outcome.ERROR,
        )

    @property
    def bars_retry(self) -> bool:
        """A refused tool is DROPPED from the rest of the turn, which is
        what makes "no retry" literal rather than hoped for: the model
        is never offered a tool it will be refused for using -- the same
        enforcement-by-omission spec section 6.4 uses for the depth cap.
        """
        return self.outcome == ToolInvocation.Outcome.REFUSED


def invoke_tool(spec: ToolSpec, args: dict, tool_ctx: ToolContext) -> ToolOutcome:
    """Validate `args` against `spec`, run its runner, classify, audit."""
    row = ToolInvocation.objects.create(
        principal_kind=tool_ctx.principal.kind,
        principal_key=tool_ctx.principal.key,
        tool_key=spec.key,
        args={},
        outcome=ToolInvocation.Outcome.ERROR,   # overwritten below; never left as a guess
        # Stamped at CREATION, not at finish: the whole point of this
        # column is to find a row whose call never reached `_finish`
        # (`agents.runtime.audit.close_open_invocations`). `getattr` rather
        # than `tool_ctx.job.job_id` because a `ToolContext` built by a
        # test double -- or by a future MCP edge with no job at all --
        # carries none, and an audit write must never be the thing that
        # raises.
        queue_job_id=getattr(tool_ctx.job, "job_id", None),
        # WHOSE TOOL DECLARATION WAS IN FORCE, as distinct from
        # `principal_kind`/`principal_key` above (WHO THIS WAS DONE
        # FOR). Blank for a call with no agent at all -- the future MCP
        # edge -- exactly like `tool_ctx.agent_slug` itself.
        agent_slug=tool_ctx.agent_slug,
    )

    # C-2: THE SAME QUESTION, AT THE SEAM EVERY CALLER PASSES THROUGH.
    # `granted_tools` filters an AGENT's keys and never sees a flow's
    # steps, so the flow loop re-implemented the MUTATING half of that
    # filter and nothing at all of the entitlement half -- which also
    # carries the workstream wall's tool half, since a walled context's
    # tool access is the intersection of held entitlements with the wall
    # (`agents.entitlements.tool_access_for`). Asking here, rather than
    # in each caller, is what stops the next caller forgetting:
    # `delegate.py` remembered (it reuses `ctx.tool_access` at the
    # delegation hop), `flow.py` did not.
    #
    # RETURNED, NOT RAISED, AND OUTSIDE THE `try`. `ToolRefused` is a
    # `ValueError` subclass (`agents/contracts/tools.py:56`), so a raise
    # inside the try below would be caught by this function's own
    # classification chain (`:20-30`) and converted -- making the guard a
    # no-op no test could see. `_finish(..., REFUSED, ...)` is the exact
    # shape the `except ToolRefused` branch below already produces --
    # NOT the `except ValueError` branch, which finishes with
    # `Outcome.ERROR`, the one thing this fix exists to get right -- so
    # the audit row and the outcome are indistinguishable from every
    # other refusal.
    if not tool_ctx.tool_access.allows(spec.key):
        # THE ATTEMPTED ARGS, NOT `{}` -- the same idiom the `ParamError`
        # branch below uses (`row.args = args if isinstance(args, dict)
        # else {}`, before its own `_finish`). `row.args` still holds
        # the CREATION-time `{}` at this point, because this check runs
        # before `validate_tool_args` ever sees `args`; recording the
        # raw attempted dict here is what makes a refused row as
        # investigable as a successful one -- an operator reading the
        # audit trail should see WHAT was asked for, not just that it
        # was refused.
        row.args = args if isinstance(args, dict) else {}
        message = f"{spec.key} is not available to you on this box."
        return _finish(row, ToolInvocation.Outcome.REFUSED,
                       text=message, error=message)

    try:
        clean = validate_tool_args(spec, args)
    except ParamError as exc:
        row.args = args if isinstance(args, dict) else {}
        return _finish(row, ToolInvocation.Outcome.PARAM_ERROR,
                       text=_param_error_text(spec, exc), error=str(exc))
    row.args = clean
    row.save(update_fields=["args"])

    # Resolved lazily, by dotted path: this module never imports
    # `tools.*` (import-law rule 3), which is what keeps `agents/` free
    # of the columns whose tools it runs. `models/registry/tests/
    # test_registry_paths.py` proves every REGISTERED path resolves, but
    # a `ToolSpec` built by hand (a test double, a future row-declared
    # tool) carries no such guarantee -- so a misconfigured `runner`
    # string is caught HERE, as one more classified outcome, rather than
    # left to raise `ImportError` out of a job handler.
    try:
        runner = resolve_dotted_path(spec.runner)
    except ImportError as exc:
        message = f"Tool {spec.key!r} is misconfigured: its runner could not be resolved."
        return _finish(row, ToolInvocation.Outcome.ERROR, text=message, error=str(exc))
    # `ToolContext` is frozen; build a replacement rather than reaching
    # into the caller's. `tool_key` is how a runner shared by several
    # specs learns which one invoked it (deviation D4). `supplied_keys`
    # is `args`' OWN keys, before `validate_tool_args` above filled in a
    # single default -- CQ-1 fix round 1: a union-schema runner
    # (`tools.vision.tools.run_generate`) needs the caller's true raw
    # keys to know what was actually supplied, and `clean` (passed to
    # the runner below) cannot answer that, since it fills every
    # declared key regardless of whether the caller sent it.
    ctx = replace(
        tool_ctx, tool_key=spec.key,
        supplied_keys=frozenset(args) if isinstance(args, dict) else frozenset(),
    )

    try:
        result = runner(clean, ctx)
    except ToolRefused as exc:                       # MUST precede ParamError/ValueError
        return _finish(row, ToolInvocation.Outcome.REFUSED, text=str(exc), error=str(exc))
    except ParamError as exc:                        # MUST precede ValueError
        return _finish(row, ToolInvocation.Outcome.PARAM_ERROR,
                       text=_param_error_text(spec, exc), error=str(exc))
    except ValueError as exc:
        return _finish(row, ToolInvocation.Outcome.ERROR, text=str(exc), error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- never-500: an unclassified runner failure
        logger.exception(
            "agents: tool %r raised an unclassified exception (invocation %s)",
            spec.key, row.pk,
        )
        # C-6 (security round 3, H38): THE MODEL AND THE PAGE GET A FIXED
        # SENTENCE; THE OPERATOR GETS THE STRING. `str(exc)` on this
        # branch is whatever a third-party library chose to say -- a
        # database error text, an OS error naming an absolute path, an
        # HTTP error naming the engine endpoint -- and it lands in the
        # turn, replays into every later prompt, and renders on the tool
        # card a share recipient can read (C-1, H25, makes that reader
        # reachable). The invocation id is what makes the two halves
        # joinable: an operator reads `error` (and the traceback logged
        # just above) off the `ToolInvocation` row this id names.
        return _finish(
            row, ToolInvocation.Outcome.ERROR,
            text=(f"{spec.key} failed unexpectedly ({type(exc).__name__}). "
                  f"The operator can look this up as invocation {row.pk}."),
            error=str(exc),
        )

    outcome = (
        ToolInvocation.Outcome.DEGRADED
        if isinstance(result.data, dict) and result.data.get("degraded") is True
        else ToolInvocation.Outcome.OK
    )
    return _finish(row, outcome, text=result.text, result=result)


def invoke_unknown_tool(wire: str, args: dict, tool_ctx: ToolContext) -> ToolOutcome:
    """A wire name the model emitted that maps to no registered tool.

    A tool error, not a crash (spec section 10.3): it produces a tool
    turn saying the tool does not exist, spends a step, and spends the
    recovery. NEVER guessed at, fuzzy-matched, or silently ignored -- a
    model that is quietly given a different tool than it asked for
    produces an answer nobody can audit.
    """
    key = key_from_wire_name(wire)
    row = ToolInvocation.objects.create(
        principal_kind=tool_ctx.principal.kind,
        principal_key=tool_ctx.principal.key,
        tool_key=key,
        args=args if isinstance(args, dict) else {},
        outcome=ToolInvocation.Outcome.ERROR,
        queue_job_id=getattr(tool_ctx.job, "job_id", None),
        # WHOSE TOOL DECLARATION WAS IN FORCE -- populated here too, not
        # just in `invoke_tool`. `tool_ctx.agent_slug` is already known
        # at this point (the model asked for a wire name FROM some
        # agent's own prompt); leaving it blank would contradict
        # `agents/models.py`'s own column doc ("blank... for a call with
        # no agent at all") for a row that plainly has one, and a
        # hallucinated-tool call is precisely the kind of row an
        # operator investigates by agent.
        agent_slug=tool_ctx.agent_slug,
    )
    message = (
        f"There is no tool called {key!r} on this system. Use one of the tools you "
        f"were given, or answer without one."
    )
    return _finish(row, ToolInvocation.Outcome.ERROR, text=message, error=message)


def _param_error_text(spec: ToolSpec, exc: ParamError) -> str:
    """A `ParamError` rendered as the per-arg reasons a model can act on.

    `.errors` maps a param key to a human-readable reason
    (`models/contracts/operations.py:228-236`). Handing that back
    verbatim is the whole reason this is the one failure class worth a
    repair attempt.
    """
    reasons = "; ".join(f"{key}: {reason}" for key, reason in sorted(exc.errors.items()))
    return f"The arguments for {spec.key} were not accepted -- {reasons}"


def _finish(row: ToolInvocation, outcome: str, *, text: str = "", error: str = "",
            result: ToolResult | None = None) -> ToolOutcome:
    row.outcome = outcome
    row.text = text if result is not None else ""
    row.error = error
    row.finished_at = timezone.now()
    row.save(update_fields=["args", "outcome", "text", "error", "finished_at"])
    return ToolOutcome(
        outcome=outcome, text=text, args=dict(row.args or {}),
        result=result, invocation_id=row.pk,
    )
