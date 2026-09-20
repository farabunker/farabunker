"""One agent turn, bounded, inline (spec section 6.2).

THE TURN IS THE JOB. Tools run INLINE inside it -- a tool may enqueue a
queue job and return its id, and it must never wait on one. On a default
install `JobSettings.memory_budget_bytes` is null, which
`models/queue/scheduler.py:375` reads as sequential mode -- at most one
job on the whole machine -- so a turn that blocked on a job it enqueued
would hold the machine's one slot while the job it waits for can never
be admitted, and the orphan sweep would then fail it permanently.
Deadlock, then data loss. Certain, not probable.

POST-DEADLINE LATENCY IS REAL AND IS NOT FIXED HERE. `budget.expired` is
checked at the top of every iteration and before every tool call, NEVER
during one. A tool that enters a long wait just under the deadline can
return well after it -- `tools.vision.services.wait_for` polls the image
engine for up to its own timeout, which is longer than a turn's
remaining time can be. The turn then ends immediately with the honest
deadline sentence PLUS that tool's actual result. Killing a tool
mid-flight would strand a submitted generation and throw away work that
really was done; ending late and saying so is the better failure.

EVERY ENDING IS HONEST. A turn ends by producing an answer, by running
out of steps, by running out of time, by failing twice, or by being
bound to a model that cannot call tools. In every case the assistant
turn says which, and carries whatever the last tool actually returned.
It never fabricates an answer and never silently truncates.

NO PROMPT-HACKING, EVER. When the bound engine reports it cannot call
tools, this loop says so and stops. It does not inject a hand-rolled
tool syntax into the system prompt, does not nudge with "please respond
in JSON", and does not parse a tool call out of prose --
`models/contracts/engines/base.py:450-458` forbids all three by name.

THIS MODULE IMPORTS NO `tools.*` MODULE. Every runner is reached through
`agents.runtime.invoke.invoke_tool`, which resolves a dotted-path string
(import-law rule 3).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import transaction

from agents.contracts.tools import (
    StepBudget, ToolAccess, ToolContext, UNRESTRICTED_TOOL_ACCESS, VISION_GENERATE_KEY,
    granted_tools,
)
from agents.contracts.toolschema import key_from_wire_name, openai_tool_dict
from agents.limits import (
    MAX_STEPS_DEFAULT, RESOLVED_TIMEOUT_FLOOR_SECONDS, TURN_DEADLINE_SECONDS,
    TURN_TIMEOUT_ADMIN_HINT, TURN_TIMEOUT_ERROR,
)
from agents.models import Turn
from agents.runtime.audit import close_open_invocations
from agents.runtime.bindings import resolve_chat
from agents.runtime.invoke import invoke_tool, invoke_unknown_tool
from agents.runtime.prompt import build_messages, tool_turn_messages
from agents.runtime.taint import stamp_turn_taint
from identity.contracts.principals import Principal, principal_from_payload
from models.contracts import gateway
from models.contracts.bindings import resolve
from models.contracts.jobkinds import JobContext, resolve_dotted_path
from models.registry.bindings import model_access_for

if TYPE_CHECKING:  # pragma: no cover -- annotation only
    from agents.contracts.workstreams import WorkstreamScope

logger = logging.getLogger(__name__)

# Aliased from the column's one home (packet finding B) -- `loop` and
# `prompt` both need this key and `loop` imports `prompt`, so the
# constant cannot live in either.
_VISION_GENERATE_KEY = VISION_GENERATE_KEY

_OUT_OF_STEPS = "I ran out of steps for this turn before reaching an answer."
# I2 (fix round 1): THE SPLIT, NAMED EXPLICITLY, because ONE ending
# (a `budget.expired` deadline) now writes BOTH of the two constants
# below into two DIFFERENT columns of the SAME row, and a reader fixing
# one without the other would silently un-sync them.
#
# `_OUT_OF_TIME` -> `Turn.text` (via `_honest_ending`, below): the
# loop's own COMPOSED text -- this sentence alone when no tool ran yet,
# or this sentence plus the last tool's actual raw result when one did.
# PRESERVED, not dead copy (I1, fix round 1): `_turn_card.html`'s FAILED
# branch now renders `card.text`/`card.text_html` above the error line
# whenever `_finish` wrote one -- a timed-out turn's own honest content
# is shown to a human reader, it is simply never REPLAYED into a later
# turn's prompt (`agents.runtime.prompt._REPLAYABLE_STATES` stays
# `("done",)`, unchanged; see that constant's own comment for why).
_OUT_OF_TIME = "This turn hit its time limit before I reached an answer."
# `TURN_TIMEOUT_ERROR` -> `Turn.error` (via `_finish`, below): the
# STORED FAILURE SENTENCE -- generic, admin-info-free, and the one thing
# `_turn_card.html`'s FAILED branch actually keys its admin hint off of.
# Written verbatim into `Turn.error` by `_finish` below whenever
# `run_loop` reports `timed_out=True` (the `budget.expired` branches,
# never the STEPS-exhausted one: `_OUT_OF_STEPS` above stays an honest
# DONE ending, unchanged).
#
# IMPORTED FROM `agents.limits`, NOT DECLARED HERE (MINOR 6, fix round
# 1) -- see that module's own comment: `TURN_TIMEOUT_ERROR` and its
# sibling `TURN_TIMEOUT_ADMIN_HINT` both live there now, so
# `agents.chat.rendering` and `agents.chat.views.turns._failed_body` can
# read them without pulling in `models.contracts.gateway`/the engine
# registry/`llama_index` just to compare one string. This module's own
# `from agents.limits import ...` at the top of the file re-exports both
# names, so `agents.runtime.loop.TURN_TIMEOUT_ERROR` stays valid and
# every reference below (and every test importing it from here) keeps
# working unchanged.
# Appended by `_honest_ending` ONLY when a tool actually ran this turn --
# see that function's docstring. Never claim a tool returned something
# when none has.
_LAST_TOOL_TRAILER = "Here is what the last tool returned."
# Named `..._NOTE`, not bare `NO_TOOL_CALLING` (fix round 1, F5): this
# module's sibling `agents.runtime.preflight` already declares a PUBLIC
# `NO_TOOL_CALLING` of its own -- a different thing entirely (a closed-
# vocabulary status string, `"no_tool_calling"`, not a sentence). CQ-5's
# own rationale ("a name two sibling modules import is part of the
# package's interface") argues against two same-named public constants
# meaning different things in the same package.
NO_TOOL_CALLING_NOTE = (
    "The model currently assigned to this agent's role reports that it cannot call "
    "tools, so I cannot use the tools this agent was granted. Assign a tool-capable "
    "model to that role, or use an agent that needs no tools."
)
# Same operator-facing sentence `agents.runtime.preflight`'s own
# `_AGENT_NOT_PERMITTED_MESSAGE` uses for `AGENT_NOT_PERMITTED`, and named
# `..._NOTE` for the identical reason `NO_TOOL_CALLING_NOTE` above is:
# this module cannot import `agents.runtime.preflight` (that module
# imports THIS one, as `loop_module`, at call time -- the other
# direction would be a cycle), so the words live here as their own copy
# rather than a shared constant.
AGENT_NOT_PERMITTED_NOTE = (
    "This agent needs an entitlement this account does not hold. The conversation is "
    "still readable; new turns are not."
)
_NO_ANSWER = "The model returned no answer."


def resolved_turn_timeout(job_ctx: JobContext) -> float:
    """The turn's operative wall clock (one-timeout task, 2026-09-17,
    fix round 1 B2): `job_ctx.response_timeout_seconds` when the worker
    stamped one, else `TURN_DEADLINE_SECONDS` -- the ONE place this
    fallback rule is declared, so every caller that needs "the turn's own
    timeout, right now" asks here rather than re-typing the ternary.

    TWO CALLERS, ONE SHARED BUDGET (B2): `_run_turn` uses this for the
    ROOT loop's own `StepBudget` and `gateway.get_llm_for`'s
    `request_timeout`; `agents.runtime.delegate.run_agent_tool` -- an
    agent-as-tool hop spending from that SAME shared budget
    (`ctx.budget`, `job_ctx=ctx.job`) -- uses it identically for its own
    `gateway.get_llm_for` call. Before this existed, the delegate path
    built its chat client with NO `request_timeout` at all, so it kept
    the engine's own hidden default (300s) while spending a budget that
    may now run up to 7200s -- the exact competing-timeout incident this
    whole task exists to close, surviving on the one path that forgot to
    ask. A single resolver closes it for both callers at once and for
    every future one: nobody re-derives the fallback by hand again.

    DELIBERATELY NOT FLOORED HERE (final review MINOR 7, fix round 3 --
    revised after this floor's first attempt broke this suite's own
    deadline-simulation technique, caught by the full `agents` gate, not
    assumed safe). This raw value feeds TWO different uses with two
    different tolerances for a degenerate (`<= 0`) input:

    1. `_run_turn`'s own `StepBudget.deadline_monotonic` -- where a
       non-positive value is not a bug at all, it is exactly how this
       suite's OWN tests simulate "already past the deadline"
       (`monkeypatch.setattr("agents.runtime.loop.TURN_DEADLINE_
       SECONDS", -1.0)`, `make_job_ctx(response_timeout_seconds=-1.0)`,
       used throughout `agents/runtime/tests/test_loop.py`). Flooring
       THIS value at a positive number breaks every one of those tests
       by pushing the deadline into the future instead of the past --
       confirmed the hard way: the first version of this function
       floored its own return value and five existing tests silently
       started ending turns DONE instead of honestly timed-out. A
       genuinely degenerate PRODUCTION value here (an operator's row
       stored as `0`, unreachable through the settings UI, which
       refuses it at the write path) is honestly, gracefully handled
       already: the turn ends via the existing `budget.expired` branch,
       precisely as if the deadline had already passed -- which, at
       `0`, it effectively has. That is correct, not a foot-gun.
    2. `gateway.get_llm_for`'s `request_timeout=` argument -- where a
       `<= 0` value IS a real hazard (undefined/dangerous behaviour
       against a REAL httpx client), and where a floor costs nothing:
       every test that reaches this argument at all mocks `get_llm_for`
       outright (`patch_llm`), so flooring only THIS argument affects
       no test's own deadline-simulation technique. See `_run_turn`'s
       and `agents.runtime.delegate.run_agent_tool`'s own call sites,
       both of which floor with `agents.limits.RESOLVED_TIMEOUT_FLOOR_
       SECONDS` at the point they build the argument, never here.
    """
    return (
        job_ctx.response_timeout_seconds if job_ctx.response_timeout_seconds is not None
        else TURN_DEADLINE_SECONDS
    )


def _tool_turn_author_id(principal) -> int | None:
    """C-1 (H25): the TOOL turn's own `author_id` -- `principal`'s user
    id, but ONLY when that id PARSES and names an account that still
    exists. `principal` here is `principal_from_payload(payload)` (line
    ~220), reconstructed from a STORED job payload rather than a live
    request, and that function's own contract is "NEVER RAISES": a
    payload enqueued a while ago may name an account since deleted, be
    hand-edited (`agents/runtime/tests/test_acting_rule.py`'s own
    junk-`actor_kind` fixtures use the identical shape), or -- review
    round 1 -- carry a non-decimal `actor_key` that would raise
    `ValueError` reaching either `int()` or a `pk=` filter. The
    parseability guard is `identity.access._user_pk`'s own idiom
    (`identity/access.py`, "not a str or not `isdecimal()` -> `None`"),
    checked BEFORE the query so a junk key costs no lookup at all.
    Writing an unparseable or nonexistent id straight into `Turn.
    author`'s FK would turn any of these into a raised `IntegrityError`
    (or a raised `ValueError` for the non-decimal case) inside the same
    atomic block that also writes taint tags -- exactly what the
    acting-rule module exists to never do. `None` in every case:
    `Turn.author`'s own docstring already means it as "not
    attributable". ONE extra query, only for a "user" principal whose
    key parses -- `start_turn`'s own USER-turn stamp skips this check,
    since its `actor` is always the live request's, never a stored
    payload's.
    """
    if principal.kind != "user":
        return None
    key = principal.key
    if not isinstance(key, str) or not key.isdecimal():
        return None
    from django.contrib.auth import get_user_model

    if not get_user_model().objects.filter(pk=key).exists():
        return None
    return int(key)


@dataclass(frozen=True)
class LoopResult:
    """What one call to `run_loop` produced.

    Frozen, plain data -- `run_loop`'s only caller-visible surface.
    `_run_turn` folds this into the `Turn` row it owns; `agents.runtime.
    delegate.run_agent_tool` folds it into a `ToolResult` instead. Neither
    caller reaches into `run_loop`'s locals, which is the whole point of
    giving it a return type rather than an out-parameter.

    `appended_tool_result` (H5 review round 2, finding 1): the PROVENANCE
    of whatever raw tool text `_honest_ending`/`_two_failures_text` baked
    into `text` -- never the fenced text itself, and never a fencing
    DECISION. `_run_turn` writes this verbatim onto the ASSISTANT turn's
    own `Turn.data["appended_tool_result"]`; `agents.runtime.prompt`
    reads it back at REPLAY time to fence exactly the portion that needs
    it, through the SAME `fence_tool_result_if_third_party` a TOOL turn's
    own message already goes through. `()` (default) for every ending
    that never baked a raw tool result into its text at all -- a plain
    answer, `NO_TOOL_CALLING_NOTE`, `AGENT_NOT_PERMITTED_NOTE` -- so a
    caller with nothing to record pays nothing.
    """

    text: str
    artifacts: tuple[str, ...]
    tool_calls: tuple[dict, ...]
    steps_used: int
    appended_tool_result: tuple[dict, ...] = ()
    # `True` only for the two `budget.expired` (wall-clock deadline)
    # endings below -- never for `budget.exhausted` (steps), which stays
    # an honest, non-failed DONE ending (`TestHonestEndings`'s own pinned
    # "honest, not failed" comment). `_run_turn` reads this to decide
    # whether `_finish` writes `TURN_TIMEOUT_ERROR` into `Turn.error` and
    # marks the turn FAILED, one-timeout task, 2026-09-17.
    timed_out: bool = False


def run_turn(payload: dict, models: list, ctx: JobContext) -> dict:
    """Run one agent turn to completion, inline.

    Wraps the whole body in its own try/except and writes the failure
    ITSELF before re-raising. That is required, not defensive:
    `on_terminal` does NOT fire when the handler ran and raised.
    `Worker._execute` gates the hook on a `handler_started` flag
    (`models/queue/worker.py:618,623,670`) whose docstring says so
    outright, and `JobKind.on_terminal`'s own contract restricts it to a
    job that reaches a terminal state WITHOUT ever running its handler
    (`models/contracts/jobkinds.py:207-215`). `on_turn_terminal`
    (`agents/runtime/jobs.py`) covers the three paths where the handler
    never started; this `except` covers the one where it did.

    `str(exc)`, never a traceback: this text is shown to a person.
    """
    try:
        return _run_turn(payload, models, ctx)
    except Exception as exc:
        # The writeback itself must not be able to mask the ORIGINAL
        # exception: a DB outage (or anything else) raised HERE would
        # otherwise propagate in place of `exc`, and whoever is debugging
        # a failed turn would see "could not write to the database"
        # instead of the real reason the turn failed. Logged, not
        # swallowed silently, and the original `exc` is re-raised either
        # way.
        try:
            Turn.objects.filter(pk=payload["turn"]).update(
                state=Turn.State.FAILED, error=str(exc),
            )
            # The handler RAN, so `on_turn_terminal` will not fire
            # (`Worker._execute`'s `handler_started` gate) -- which
            # makes this the only place a mid-call crash's open audit
            # row can be closed. `ctx.job_id`, not the turn's stamped
            # column: the handler is holding the live JobContext.
            close_open_invocations(
                ctx.job_id,
                reason="The turn running this tool call failed before the call "
                       "reported an outcome.",
            )
        except Exception:
            logger.exception(
                "agent.turn: could not write the FAILED state back for turn %r "
                "after the exception below; re-raising the original exception.",
                payload.get("turn"),
            )
        raise


def _run_turn(payload: dict, models: list, ctx: JobContext) -> dict:
    turn = Turn.objects.select_related(
        "conversation__agent", "conversation__workstream").get(pk=payload["turn"])
    conversation = turn.conversation
    agent = conversation.agent

    Turn.objects.filter(pk=turn.pk).update(
        state=Turn.State.RUNNING, queue_job_id=ctx.job_id,
    )

    # Hoisted once: both the budget's own `steps` and every later
    # `steps_used` computation (`resolved_max_steps - budget.steps_left`)
    # must agree on what "the max" was, or a delegate spending from this
    # SAME shared budget would make a locally-incremented
    # counter drift from what the budget itself thinks happened.
    resolved_max_steps = agent.max_steps or MAX_STEPS_DEFAULT
    # ONE-TIMEOUT TASK (2026-09-17, B2 fix round 1): `resolved_turn_
    # timeout(ctx)` -- stamped by `models.queue.worker.Worker.
    # _build_job_context` from the operator-editable `JobSettings.
    # response_timeout_seconds`, or `TURN_DEADLINE_SECONDS` for an
    # unstamped context -- is the OPERATIVE turn deadline. Resolved ONCE,
    # here, and reused below to build the chat engine's own request
    # timeout too (`gateway.get_llm_for`'s `request_timeout=`) -- the
    # SAME number for both, so nothing competes. The SAME function is
    # also `agents.runtime.delegate.run_agent_tool`'s own call, so an
    # agent-as-tool hop spending from this SAME shared budget builds ITS
    # chat client with the identical value rather than the engine's
    # hidden default.
    resolved_timeout_seconds = resolved_turn_timeout(ctx)
    budget = StepBudget(
        steps=resolved_max_steps,
        deadline_monotonic=time.monotonic() + resolved_timeout_seconds,
    )
    # THE ACTING RULE (spec section 5.3): a turn runs as the
    # USER named in the payload, never as the agent. `agent.resident`/
    # `agent.slug` used to be minted into a `Principal` right here
    # (`principal_for`, now deleted) -- that answered "who is this AGENT
    # acting as", which is the wrong question once a payload carries a
    # real actor. `principal_from_payload` NEVER RAISES, so a
    # turn enqueued before this phase (no actor keys) or a hand-edited
    # row (a junk `actor_kind`) both still run, honestly, as
    # `OPEN_PRINCIPAL`.
    principal = principal_from_payload(payload)
    # ONE `IdentitySettings` READ for every axis this function rebuilds
    # below (whole-branch review item 2, the same ruling
    # `agents.runtime.preflight.preflight_turn`'s own `settings` local
    # states) -- threaded into `sees_all_content`, `tool_access_for`, and
    # `model_access_for` all three, rather than a singleton read apiece.
    from identity.access import sees_all_content, settings_row as identity_settings_row
    settings = identity_settings_row()
    # RUNTIME RECHECK SYMMETRY (whole-branch review item 4): the label
    # question is re-asked HERE, at the same defence-in-depth spot the
    # tool and model axes are rebuilt just below, for the same reason --
    # an agent labelled AFTER `plan_turn`'s enqueue-time check but BEFORE
    # a worker picks this job up must still refuse, exactly as a revoked
    # tool or model entitlement already does. `label_permitted_q
    # (principal)` ALONE, not `visible_agents`, for the reason `agents.
    # runtime.preflight.preflight_turn`'s own docstring gives: a turn on
    # a conversation this principal is already posting to is a different
    # question from starting one from scratch, and the fuller function
    # would 403 a shared-conversation poster for an agent they were never
    # meant to own or discover. `sees_all_content` short-circuits first,
    # exactly as it does everywhere else this predicate is asked.
    from agents.models import Agent
    from agents.visibility import label_permitted_q
    if not sees_all_content(principal, settings_row=settings) and not Agent.objects.filter(
        pk=agent.pk
    ).filter(label_permitted_q(principal)).exists():
        return _finish(turn, conversation, AGENT_NOT_PERMITTED_NOTE, [], 0, "")
    # THE TURN'S STREAM SCOPE (spec §12.4), computed FIRST NOW (E5) --
    # reconciliation R2 / author decision 3: the spec says `invoke_tool`
    # sets it, but `invoke_tool` has only a `conversation_id` string and
    # no row. `conversation` is in scope right here, exactly where
    # `agent_slug` and `tool_access` are already built once per turn and
    # threaded down rather than re-derived per tool call. `None` for a
    # loose conversation (`conversation.workstream_id` unset).
    from agents.workstreams import scope_for_conversation

    stream = scope_for_conversation(principal, conversation)

    # THE ACTING RULE'S GRANT HALF (IA-2): built ONCE, here, and threaded
    # through both `available_tools` (the prompt's own filter) and
    # `run_loop` (which stamps it onto every `ToolContext` it builds) --
    # never recomputed at a delegation hop, which is the whole of "an
    # agent is never a way around labels".
    #
    # ONE READ ON THE ADMITTED PATH (E5): `stream.wall` IS this
    # conversation's wall already -- `workstream_scope` -> `wall_ids`
    # read the same table just above to build it, so asking `wall_for`
    # again here would be the SAME table read twice for the SAME turn.
    # The fallback is for the ANOMALOUS not-visible case alone
    # (`conversation.workstream_id` set but this principal's admission
    # to it just failed, or there is no stream at all) -- `stream` is
    # `None` either way, and the wall STILL BINDS (spec §17.3: falling
    # out of admission is not a way to fall out of the wall too), so
    # this is the one path that still pays its own `wall_for` read;
    # byte-identical semantics to what this function computed before,
    # just no longer a second read on the common, admitted one.
    from agents.entitlements import tool_access_for, wall_for
    wall = stream.wall if stream is not None else wall_for(conversation)
    access = tool_access_for(principal, settings_row=settings, wall=wall)

    available = available_tools(principal, agent, access)
    # DEFENCE IN DEPTH (IA-2 T14): re-derived here, at the one place the
    # model is actually built, rather than trusted from `plan_turn`'s
    # enqueue-time snapshot -- the seam a run-time re-check exists for
    # (an entitlement revoked between enqueue and run). `model_access_for`
    # is imported at MODULE level (T14 review finding 4) -- this module
    # already imports `agents.runtime.bindings`, which itself imports
    # `models.registry.bindings` at module level with no cycle, so there
    # is none here either.
    model_access = model_access_for(principal, settings_row=settings, wall=wall)
    resolved, answered_by = resolve_chat(agent, payload.get("connection"),
                                         access=model_access)

    # Section 10.1's "bound model cannot call tools" row, reached from
    # inside the loop because P2 has no view. `False` means the engine
    # REPORTED the fact; `None` means it does not report it at all, and
    # the turn runs (`models/contracts/engines/base.py:450-458`).
    if available and supports_tool_calling(resolved) is False:
        return _finish(turn, conversation, NO_TOOL_CALLING_NOTE, [], 0, answered_by)

    # `gateway.get_llm_for(...)`, never a `from ... import get_llm_for`
    # bound at import time: the name is looked up on the MODULE at call
    # time, which is what lets a test patch
    # `models.contracts.gateway.get_llm_for` and have this call see it.
    # A hoisted `from`-import copies the function object into this
    # module's namespace and the patch never reaches it.
    # `request_timeout=` (one-timeout task): the SAME value the budget
    # above was built from, so the engine's own inner client can never
    # time out before the loop's own deadline check does -- built ONCE
    # per turn, here, never rebuilt per step to chase the remaining
    # budget (`models.contracts.gateway.get_llm_for`'s own docstring).
    # `max(RESOLVED_TIMEOUT_FLOOR_SECONDS, ...)` (MINOR 7, fix round 3):
    # floored ONLY at this boundary, never on `resolved_timeout_seconds`
    # itself -- see `resolved_turn_timeout`'s own docstring for why a
    # shared floor there broke this suite's own deadline-simulation
    # tests. A real httpx client is the one place a `<= 0` value is
    # actually a hazard; the budget above is unaffected by this line.
    llm = gateway.get_llm_for(
        resolved,
        request_timeout=max(RESOLVED_TIMEOUT_FLOOR_SECONDS, resolved_timeout_seconds),
    )
    # `principal=principal` (round 11): lets `build_messages` append the
    # attachments block for THIS turn's own acting principal. `stream_
    # scope=stream` and `available=available` (round 11 review I-3 /
    # minor 2): the SAME `WorkstreamScope` and granted-tool dict this
    # function already built above, just above, for the turn's own tool
    # access -- reused rather than re-derived, so the block is filtered
    # by the exact corpus this turn's own retrieval calls would use and
    # never names a tool this turn was not actually offered.
    # OWNER ADDENDUM (2026-09-08): the USER turn this ASSISTANT
    # placeholder is answering -- the SAME query `agents.chat.rendering.
    # turn_group_cards` already uses to find the identical row for a
    # different reason (grouping it into a poll fragment), reused here
    # to tell `build_messages` which turn's own attachments (if any)
    # should be inlined in full rather than merely named in the
    # steering block. `None` on a conversation with no user turn before
    # this one at all (should not happen for a real `agent.turn` job,
    # but `build_messages` treats `None` as "nothing to inline" rather
    # than raising, the same defensive posture every other optional
    # keyword here takes).
    carrying_turn = conversation.turns.filter(
        role=Turn.Role.USER, index__lt=turn.index,
    ).order_by("-index").first()
    messages = build_messages(agent, conversation, principal=principal,
                              stream_scope=stream, available=available,
                              settings_row=settings, before_index=turn.index,
                              carrying_turn_id=(carrying_turn.pk if carrying_turn else None),
                              # Task 3 (native image input): the SAME `resolved`
                              # already picked for `gateway.get_llm_for` above,
                              # reused rather than re-resolved -- `build_messages`'
                              # own docstring has the full gate reasoning.
                              resolved=resolved)

    result = run_loop(
        agent=agent,
        conversation=conversation,
        messages=messages,
        llm=llm,
        budget=budget,
        principal=principal,
        job_ctx=ctx,
        depth=turn.depth,
        available=available,
        access=access,
        stream=stream,
    )
    return _finish(turn, conversation, result.text, list(result.artifacts),
                   result.steps_used, answered_by, tool_calls=list(result.tool_calls),
                   appended_tool_result=list(result.appended_tool_result),
                   error=(TURN_TIMEOUT_ERROR if result.timed_out else ""))


def run_loop(*, agent, conversation, messages: list, llm, budget: StepBudget,
             principal: Principal, job_ctx: JobContext, depth: int,
             available: dict, access: ToolAccess = UNRESTRICTED_TOOL_ACCESS,
             stream: "WorkstreamScope | None" = None) -> LoopResult:
    """Run one bounded agent loop to completion and return its `LoopResult`.

    Everything the loop body reads, and nothing it does not: no `Turn`
    row of its own to update (the caller owns that -- `_run_turn`'s
    placeholder assistant row, or nothing at all for a delegate), no
    RUNNING-state write, no `_finish`. The tool-turn writes stay INSIDE
    this function: they are what a loop does, at whatever `depth` it
    runs, and a delegate's tool turns must land in the database exactly
    like a root turn's do (spec section 6.4) for an operator to audit
    them.

    `messages` is mutated in place (each tool round appends to it) and
    also read from -- callers that need their own copy preserved should
    pass one they do not mind being extended.

    RULING: only the ROOT call (`depth == 0`) reports
    progress. `job_ctx` is the SAME `JobContext` all the way down a
    delegation tree -- there is one job, one progress bar -- so a
    delegate calling `report_progress` would report against a window
    that is only ITS OWN slice of the shared budget, which regresses the
    number the root already reported. The root's `total` is exactly
    `steps_at_start`, which for depth 0 equals `resolved_max_steps`
    (`_run_turn` starts the budget at that value and calls `run_loop`
    exactly once per turn); a delegate's `steps_at_start` is whatever
    the shared budget happened to have left when it was invoked, which
    is not a total anything should be reported against.
    """
    steps_at_start = budget.steps_left
    artifacts: list[str] = []
    tool_calls: list[dict] = []
    # H5 review round 2, finding 1 / round 3, finding 2: `failure_
    # provenance` is now the ONLY record of a failing call's own text --
    # the RAW text and dotted tool key, RECORDED here and CLASSIFIED
    # nowhere near here (see `LoopResult.appended_tool_result`'s own
    # docstring). `_two_failures_text` (below) composes the human-facing
    # "- {tool}: {text}" line shape DIRECTLY from these entries, so there
    # is no separate `f"{key}: {text}"` list to keep byte-for-byte in
    # sync with them any more.
    failure_provenance: list[dict] = []
    last_tool_text = ""
    last_tool_provenance: dict | None = None
    final_text = ""
    appended_tool_result: list[dict] = []
    # One-timeout task (2026-09-17): `True` only for a `budget.expired`
    # ending -- never `budget.exhausted` (steps), which stays the
    # existing honest, non-failed DONE ending. See `LoopResult.timed_out`'s
    # own docstring for what a caller does with this.
    timed_out = False

    while True:
        if budget.expired:
            timed_out = True
            final_text, last_offset = _honest_ending(_OUT_OF_TIME, last_tool_text)
            appended_tool_result = _honest_ending_provenance(
                last_tool_text, last_tool_provenance, last_offset)
            break
        if budget.exhausted:
            final_text, last_offset = _honest_ending(_OUT_OF_STEPS, last_tool_text)
            appended_tool_result = _honest_ending_provenance(
                last_tool_text, last_tool_provenance, last_offset)
            break

        if depth == 0:
            job_ctx.report_progress(
                steps_at_start - budget.steps_left, total=steps_at_start,
                unit="items", label="thinking",
            )
        tool_dicts = [openai_tool_dict(spec) for spec in available.values()] or None
        try:
            response = llm.chat(messages, tools=tool_dicts)
        except Exception:
            # Final review I-2 (fix round 3): a call already IN FLIGHT
            # when the deadline passes is the one place `budget.expired`
            # cannot be checked before the call itself returns or raises
            # -- every other check in this loop runs BETWEEN steps. The
            # engine's own inner client timeout is now set TO the turn's
            # value (`models.contracts.gateway.get_llm_for`), so a hang
            # here raises right around when the deadline itself passes --
            # the incident this whole task exists to close (a chat-model
            # reload under memory pressure). Rather than sniff the
            # exception's TYPE (`httpx.ReadTimeout` for ollama today,
            # something else for a future engine -- coupling `agents/
            # runtime` to an engine's own exception hierarchy, which
            # `models.contracts.gateway`'s own docstring rules out: "the
            # engine ... [is] swapped purely by config/binding, without
            # touching module code"), this asks
            # the one question that actually decides it: had the
            # deadline already passed? If so, the ending IS the turn
            # timeout, composed EXACTLY like the two `budget.expired`
            # breaks above and below -- same `_OUT_OF_TIME` text, same
            # `last_tool_text` trailer, same `timed_out=True` ->
            # `TURN_TIMEOUT_ERROR` in `Turn.error` via `_finish`. An
            # exception raised BEFORE the deadline (a bad request, an
            # engine genuinely unreachable) is a real bug, not a
            # timeout, and is re-raised unchanged -- `run_turn`'s own
            # `except` still writes `str(exc)` for that case, exactly as
            # it did before this fix.
            if not budget.expired:
                raise
            timed_out = True
            final_text, last_offset = _honest_ending(_OUT_OF_TIME, last_tool_text)
            appended_tool_result = _honest_ending_provenance(
                last_tool_text, last_tool_provenance, last_offset)
            break
        calls = llm.get_tool_calls_from_response(response, error_on_no_tool_call=False)
        budget.spend(1)

        if not calls:
            final_text = _response_text(response)
            if not final_text:
                # An empty response with no tool call either: honestly
                # indistinguishable from a model that had nothing to say,
                # but never a blank DONE turn -- that reads as a bug, not
                # an answer.
                logger.info(
                    "agents: agent %r's model returned an empty response with no "
                    "tool call; ending the loop honestly instead of writing a "
                    "blank answer.", agent.slug,
                )
                final_text = _NO_ANSWER
            break

        # TAKE calls[0], DISCARD THE REST. This is this platform's
        # policy, not the library's behaviour: plain `Ollama.chat`
        # (`base.py:400-445`) passes the model's tool calls through
        # uncapped -- `force_single_tool_call` (`base.py:63`) is only
        # reached through `chat_with_tools`, which this design does not
        # use. A step is the unit the budget is denominated in, and
        # running N tools per step makes the budget mean N times less;
        # and the recovery policy is defined per failing call.
        chosen, discarded = calls[0], calls[1:]
        key = key_from_wire_name(chosen.tool_name)
        spec = available.get(key)

        # Checked AGAIN, immediately before the tool actually runs -- not
        # just at the top of the iteration. The LLM call above already
        # spent its step and is accounted for either way; what this
        # guards is running a NEW tool call after the deadline has
        # already passed, rather than letting a step budget with time
        # left keep the turn going past its wall clock.
        if budget.expired:
            timed_out = True
            final_text, last_offset = _honest_ending(_OUT_OF_TIME, last_tool_text)
            appended_tool_result = _honest_ending_provenance(
                last_tool_text, last_tool_provenance, last_offset)
            break

        tool_ctx = ToolContext(
            conversation_id=str(conversation.id),
            principal=principal,
            depth=depth,
            budget=budget,
            job=job_ctx,
            # WHOSE TOOL DECLARATION IS IN FORCE -- `agent`
            # is THIS loop's own agent, so a root call carries the
            # conversation's agent slug and a delegate's own nested
            # `run_loop` call (`delegate.py::run_agent_tool`) carries
            # the DELEGATE's, while `principal` above stays whatever the
            # caller passed in -- the acting rule's whole point.
            agent_slug=agent.slug,
            # THE ROOT ACTOR'S TOOL ACCESS (IA-2's grant half),
            # UNCHANGED at every hop: a delegate reuses `ctx.tool_access`
            # rather than rebuilding one, which is what makes an agent
            # never a way around labels.
            tool_access=access,
            # THE TURN'S STREAM SCOPE (spec §12.4), built ONCE per turn
            # by the caller and threaded here, never re-derived per tool
            # call. `None` for a loose conversation. A delegate's nested
            # `run_loop` receives the same value, so the wall travels
            # down every hop exactly as `principal` and `tool_access`
            # already do.
            stream=stream,
        )
        raw_args = dict(chosen.tool_kwargs or {})
        if spec is None:
            outcome = invoke_unknown_tool(chosen.tool_name, raw_args, tool_ctx)
        else:
            outcome = invoke_tool(spec, raw_args, tool_ctx)

        tool_text = _tool_message_text(outcome.text, discarded, key)
        # H5 review round 2, finding 1: `last_tool_text` is what `_
        # honest_ending` bakes DIRECTLY into the ASSISTANT turn's own
        # PERSISTED `Turn.text` below (out-of-time/out-of-steps) -- and
        # that text is also exactly what a human reader sees, since there
        # is no render-time step for an ASSISTANT turn the way `_tool_
        # content` is one for a TOOL turn. Round 1 fenced it RIGHT HERE,
        # at write time -- which meant the DATA header and the BEGIN/END
        # marker lines rendered to the reader too. `last_tool_text` is
        # therefore the RAW outcome text, unfenced, exactly as it was
        # before H5 -- and `last_tool_provenance` records the dotted tool
        # key and this same raw text (plus a flow's own step list, when
        # relevant) so `agents.runtime.prompt`'s history replay can fence
        # exactly this portion, LATER, the moment it is ever replayed
        # into a following turn's own prompt -- never a moment before.
        last_tool_text = outcome.text
        last_tool_provenance = (
            _tool_result_provenance(
                key, outcome.result.data if outcome.result is not None else None, outcome.text,
            )
            if outcome.text else None
        )
        # `"id": ""` ALWAYS on this engine: `Ollama.chat` builds its
        # ToolCallBlocks with no tool_call_id, and `ToolSelection.
        # tool_id` is the tool NAME, not an id (`base.py:390-394`).
        # Recording that would put a name in an id field.
        call_record = {
            # From the outcome, never from `chosen.tool_kwargs`: the
            # VALIDATED args on an ok/refused/error/degraded outcome,
            # and the RAW dict on a param_error (validation is exactly
            # what did not happen there, so there is no validated form).
            # `Turn.tool_call["args"]` is replayed into a later prompt,
            # so it must be what actually ran.
            "tool": key,
            "args": outcome.args,
            "agent": agent.slug,
            "id": "",
            "discarded": [
                {"tool": key_from_wire_name(d.tool_name), "args": dict(d.tool_kwargs or {})}
                for d in discarded
            ],
        }
        # THE STAMP RIDES A WRITE THAT ALREADY HAPPENS (spec §7.2,
        # author decision 3). `Turn.artifacts` on the TOOL TURN is the
        # taint source of truth: it is already deduped on the bare
        # `document:<id>`, already guarded against a non-decimal id by
        # `_document_artifacts`, and already written here.
        #
        # THE `atomic()` BLOCK IS NEW. This create was bare before the
        # workstreams phase; the tag rows and the turn that caused them
        # must land together or not at all, or a crash between them
        # leaves either a turn whose material no gate knows about or a
        # tag naming a turn that does not exist.
        with transaction.atomic():
            tool_turn = Turn.objects.create(
                conversation=conversation,
                index=Turn.next_index(conversation),
                role=Turn.Role.TOOL,
                text=tool_text,
                tool_call=call_record,
                data=(outcome.result.data if outcome.result is not None else None),
                artifacts=list(outcome.result.artifacts) if outcome.result is not None else [],
                depth=depth,
                state=Turn.State.DONE,
                invocation_id=outcome.invocation_id,
                queue_job_id=job_ctx.job_id,
                # C-1 (H25): the ACTING principal (line ~220's own
                # comment: a turn runs as the USER named in the payload,
                # never as the agent), not the agent that chose to call
                # the tool -- an audit fact, matching the "who ran this"
                # question `Turn.author`'s own docstring answers for a
                # USER turn's "who wrote this" one. `_tool_turn_author_id`
                # (above), not a bare `int(principal.key)`: the acting
                # principal here is reconstructed from a stored payload,
                # which may be stale.
                author_id=_tool_turn_author_id(principal),
            )
            stamp_turn_taint(tool_turn, conversation, tool_turn.artifacts,
                             actor=principal)
        tool_calls.append(call_record)
        if outcome.result is not None:
            artifacts.extend(outcome.result.artifacts)
        messages.extend(tool_turn_messages(tool_turn))

        if outcome.failed:
            # Same reasoning as `last_tool_text` above (H5 review round
            # 2, finding 1): `_two_failures_text` composes the ASSISTANT
            # turn's own persisted text DIRECTLY from `failure_
            # provenance` when a second recovery is spent -- a
            # REFUSED/ERROR outcome can still carry real document bytes
            # (e.g. a `rag.ask` that failed after retrieving, answering
            # with what it found before the failure) -- so `failure_
            # provenance` records each entry's own dotted tool key and
            # raw text, one-for-one, for the SAME later-replay fencing
            # `last_tool_provenance` above exists for.
            failure_provenance.append(
                _tool_result_provenance(
                    key, outcome.result.data if outcome.result is not None else None,
                    outcome.text,
                )
            )
            if outcome.bars_retry:
                # Enforcement by omission: the model is never offered a
                # tool it will be refused for using (section 6.4's own
                # mechanism for the depth cap).
                available.pop(key, None)
            if budget.recoveries_left <= 0:
                final_text, appended_tool_result = _two_failures_text(failure_provenance)
                break
            budget.recoveries_left -= 1

    return LoopResult(
        text=final_text,
        artifacts=tuple(artifacts),
        tool_calls=tuple(tool_calls),
        steps_used=steps_at_start - budget.steps_left,
        appended_tool_result=tuple(appended_tool_result),
        timed_out=timed_out,
    )


def available_tools(principal: Principal, agent,
                    access: ToolAccess = UNRESTRICTED_TOOL_ACCESS) -> dict:
    """The granted, registered, non-mutating, role-resolvable, ENTITLED,
    VISIBLE tools, in the agent row's own order.

    The SAME filter `plan_turn` applies (`agents/runtime/jobs.py`), so
    `get_tool` is only ever called on a key that is present and every
    tool the model is offered had its roles declared to the queue at
    enqueue time. `access` defaults to unrestricted so every direct
    caller and test that does not care keeps working unchanged.

    VISIBLE (IA-2 T15): an `agent.<slug>` key whose slug the principal
    may not run (`visible_agent_slugs`) is dropped too -- enforcement by
    omission, so the model is never offered a delegation that would
    always refuse.
    """
    from agents.contracts.tools import get_tool
    from agents.resident import AGENT_TOOL_PREFIX
    from agents.runtime.flowtool import FLOW_RUN_KEY, narrowed_flow_spec

    # LAZY, and `None` (not an empty frozenset) is the "not fetched yet"
    # sentinel: an agent with no `agent.*` key at all -- the common case
    # for a leaf agent -- never pays `visible_agent_slugs`'s one query,
    # which is the one-query promise this function's own docstring
    # makes. Fetched at most once, on the FIRST `agent.*` key seen.
    visible_slugs = None
    out = {}
    for key in granted_tools(principal, agent.tool_keys, access):
        if key.startswith(AGENT_TOOL_PREFIX):
            if visible_slugs is None:
                from agents.visibility import visible_agent_slugs

                visible_slugs = visible_agent_slugs(principal)
            if key[len(AGENT_TOOL_PREFIX):].lower() not in visible_slugs:
                # OMISSION, not refusal -- the same mechanism the
                # delegation depth cap uses, and the same reason
                # `granted_tools` logs its entitlement drop at debug: on
                # a labelled install this is the normal case.
                continue
        spec = get_tool(key)
        if key == FLOW_RUN_KEY:
            # Filled from the ROWS this principal may run, once per
            # turn. A dropped `None` means there are no flows to offer,
            # and the model is simply never told the tool exists --
            # enforcement by omission, the same mechanism section 6.4
            # uses for the depth cap.
            spec = narrowed_flow_spec(spec, principal)
            if spec is None:
                continue
        elif key == _VISION_GENERATE_KEY:
            # Fix 1b (image-model-trace.md, Follow-up 3), cleared for
            # this exact callsite by the agents-column owner: mirrors
            # `narrowed_flow_spec`'s own shape and placement above,
            # keyed to `vision.generate` rather than `flow.run`. Reached
            # through a dotted-path STRING (`resolve_dotted_path`, the
            # same mechanism `invoke_tool` resolves every runner
            # through), never an import -- `tools.vision.tools.
            # narrowed_generate_spec` NEVER returns `None` (unlike
            # `narrowed_flow_spec` above), so there is no matching
            # `continue` here.
            #
            # Review round 1, finding 2: a SECOND, independent guard
            # around the call itself, layered on top of `narrowed_
            # generate_spec`'s own internal try/except -- different
            # jobs. That one cannot see `resolve_dotted_path` itself
            # failing to resolve (a typo'd path, a column the box
            # never installed); THIS one can. FAIL OPEN to the full
            # union spec, LOUD warning: a silent swallow here would
            # invisibly recreate the incident this fix exists for --
            # a model offered the wrong operations with no one able to
            # tell why.
            try:
                narrow_generate = resolve_dotted_path("tools.vision.tools.narrowed_generate_spec")
                spec = narrow_generate(spec)
            except Exception:  # noqa: BLE001 -- fail open to the union spec, never silently
                logger.warning(
                    "vision.generate narrowing failed; offering the full union spec",
                    exc_info=True,
                )
        if _roles_resolve(spec):
            out[key] = spec
    return out


def _roles_resolve(spec) -> bool:
    for role in spec.roles:
        try:
            resolve(role)
        except Exception:  # noqa: BLE001 -- an unresolvable role is a DROP, not a fault
            logger.info(
                "agents: tool %r needs role %r, which does not resolve here; dropped "
                "from this turn's tool list.", spec.key, role,
            )
            return False
    return True


def supports_tool_calling(resolved) -> bool | None:
    """Three-valued, and each value means something different
    (`models/contracts/engines/base.py:439-460`).

    Reached through `getattr`, so an adapter predating the method
    degrades to `None` rather than `AttributeError`. Any failure asking
    is also `None`: "we could not find out" and "the engine does not
    report it" lead to the same honest behaviour -- attempt the turn.
    """
    from models.contracts.engines import get_engine

    try:
        engine = get_engine(resolved.engine)
    except Exception:  # noqa: BLE001 -- an unknown engine is not a reason to refuse
        return None
    probe = getattr(engine, "supports_tool_calling", None)
    if probe is None:
        return None
    try:
        return probe(resolved.model_id, resolved.endpoint)
    except Exception:  # noqa: BLE001 -- a probe failure is "unknown", never "no"
        logger.info("agents: could not ask %r whether it can call tools", resolved.engine)
        return None


def _tool_message_text(text: str, discarded, executed_key: str) -> str:
    """What the model is told a tool returned.

    When calls were discarded, the message SAYS which one ran. The
    discard is recorded, never silent: a model reasoning about three
    calls it thinks it made would be reasoning about a turn that did not
    happen.
    """
    if not discarded:
        return text
    names = ", ".join(key_from_wire_name(d.tool_name) for d in discarded)
    return (
        f"{text}\n\n(Only {executed_key} was run this step. These were not run: "
        f"{names}. Call one of them on your next step if you still need it.)"
    )


def _honest_ending(sentence: str, last_tool_text: str) -> tuple[str, int]:
    """`sentence` alone when no tool has actually run yet this turn --
    the deadline-before-the-first-call case -- or `sentence` plus the
    trailer clause plus the LAST tool's real text when one has. The
    trailer is never appended over a blank `last_tool_text`: claiming a
    tool "returned" something when none ran would itself be the
    fabrication this whole module exists to avoid.

    RAW, deliberately (H5 review round 2, finding 1): `last_tool_text` is
    never fenced here, whatever its provenance -- this is the text a
    human reader sees verbatim in the chat bubble, and round 1's mistake
    was fencing it AT THIS POINT, which baked the DATA header and the
    BEGIN/END marker lines into exactly what a person reads. See
    `LoopResult.appended_tool_result`'s own docstring for where the fence
    actually happens now.

    Returns `(text, offset)` (H5 review round 3, finding 2): `offset` is
    the EXACT index of `last_tool_text` within `text`, computed directly
    from the fixed template pieces above -- `len(sentence) + 1 +
    len(_LAST_TOOL_TRAILER) + 2` -- never re-found by searching a copy of
    `text` later, which is what `agents.runtime.prompt._replay_assistant_
    text` used to do. `-1` when no tool result was appended at all (the
    `not last_tool_text` case), so a caller never mistakes "no offset" for
    "offset zero".
    """
    if not last_tool_text:
        return sentence, -1
    prefix = f"{sentence} {_LAST_TOOL_TRAILER}\n\n"
    return prefix + last_tool_text, len(prefix)


def _honest_ending_provenance(last_tool_text: str, last_tool_provenance: dict | None,
                              offset: int) -> list[dict]:
    """The `appended_tool_result` entries an honest ending's own
    persisted `Turn.data` should carry: `last_tool_provenance` restated
    as the single-entry list `agents.runtime.prompt`'s replay-time
    fencing expects, or `[]` when no tool has run yet this turn -- the
    SAME guard `_honest_ending` itself applies to `last_tool_text`, so
    the two can never disagree about whether a tool result was actually
    baked into this text.

    `offset` (H5 review round 3, finding 2): `_honest_ending`'s own
    return value, restated onto the entry as `"offset"`/`"length"` --
    the EXACT position of `last_tool_text` within the composed `Turn.
    text`, computed once here at write time rather than re-found by a
    later search over a copy of the text.
    """
    if not last_tool_text or last_tool_provenance is None:
        return []
    return [{**last_tool_provenance, "offset": offset, "length": len(last_tool_text)}]


def _tool_result_provenance(tool_key: str, data: dict | None, text: str) -> dict:
    """One entry for the ASSISTANT turn's `Turn.data["appended_tool_
    result"]` list (H5 review round 2, finding 1): the dotted tool key
    and the EXACT raw text `_honest_ending`/`_two_failures_text` bake
    into `Turn.text`, plus -- only when present -- the flow step list
    `agents.runtime.prompt._is_third_party_tool_result` needs to classify
    a `flow.run` result by its LAST step. Never the full `data` dict
    verbatim: most of it (citations, per-step args, ...) plays no part in
    that classification and has no business being duplicated onto a turn
    it does not belong to.

    THIS MODULE RECORDS; IT DOES NOT DECIDE. Whether `text` actually
    turns out to be third-party is `agents.runtime.prompt`'s own call
    (`_is_third_party_tool_result`, `fence_tool_result_if_third_party`),
    made at REPLAY time from exactly this recorded fact -- never here,
    and never again at write time. A key added to the fenced allowlist
    tomorrow then fences every ASSISTANT turn already on disk that
    happened to run it, with no data migration, because the provenance
    was there all along.
    """
    entry = {"tool": tool_key, "text": text}
    steps = (data or {}).get("steps")
    if steps:
        entry["steps"] = steps
    return entry


def _two_failures_text(failure_provenance: list[dict]) -> tuple[str, list[dict]]:
    """Compose the "two tool calls failed" ending DIRECTLY from
    `failure_provenance` (`_tool_result_provenance`'s own entries, one
    per failing call, in the order they failed) -- the SAME dicts `Turn.
    data["appended_tool_result"]` ends up holding, here completed with
    each entry's own `offset`/`length` within the returned text. There is
    no separate `f"{key}: {text}"` list to keep in sync with this one any
    more (H5 review round 3, finding 2): the `"- {tool}: {text}"` line
    shape is built HERE, once, from the same recorded tool key and raw
    text the provenance already carries, byte-identical to what this
    function produced before this change.

    Returns `(text, entries)`: `entries[i]["offset"]` is the EXACT index
    within `text` where `entries[i]["text"]` (== `failure_provenance[i][
    "text"]`) begins -- computed directly while assembling the string,
    never re-found by searching a copy of it afterward.
    """
    header = (
        "Two tool calls failed in this turn, so I stopped rather than guess at an "
        "answer:\n"
    )
    chunks = [header]
    cursor = len(header)
    entries: list[dict] = []
    for i, entry in enumerate(failure_provenance):
        if i:
            chunks.append("\n")
            cursor += 1
        key, text = entry["tool"], entry["text"]
        line_prefix = f"- {key}: "
        chunks.append(line_prefix)
        cursor += len(line_prefix)
        offset = cursor
        chunks.append(text)
        cursor += len(text)
        entries.append({**entry, "offset": offset, "length": len(text)})
    return "".join(chunks), entries


def _response_text(response) -> str:
    """The assistant text out of a `ChatResponse`. `str(response.message)`
    would include the role prefix llama-index renders; `.content` is the
    concatenated text blocks."""
    return response.message.content or ""


def _finish(turn, conversation, text, artifacts, steps_used, answered_by,
            tool_calls=None, appended_tool_result=None, error: str = "") -> dict:
    """Move the assistant turn to the end, write it, and return.

    The placeholder was created before this turn's tool turns existed,
    so it currently sorts BEFORE them. One UPDATE moves it. This leaves
    a gap at its original index, which is deliberate: `ordering =
    ["index"]` and `uniq_turn_index` are both indifferent to gaps, and
    reserving indices up front would mean guessing how many tools a turn
    would call.

    `appended_tool_result` (H5 review round 2, finding 1): `LoopResult`'s
    own field, verbatim -- written onto `turn.data["appended_tool_
    result"]` when non-empty, `None` otherwise (an ordinary answer, or
    any other ending that never baked a raw tool result into `text`).
    `agents.runtime.prompt`'s history replay reads this back to fence
    exactly the portion of a REPLAYED copy of this text that needs it;
    `text` itself, persisted below, stays exactly what `_honest_ending`/
    `_two_failures_text` composed -- unfenced, the same string a human
    reader sees.

    `error` (one-timeout task, 2026-09-17): non-blank ONLY for a
    `budget.expired` ending (`_run_turn` passes `TURN_TIMEOUT_ERROR` when
    `LoopResult.timed_out`) -- every other caller passes the default `""`
    and gets the unchanged DONE ending. A non-blank `error` marks the
    turn FAILED instead, matching this codebase's own convention that "a
    failure's words are in `error`, not `text`" (`agents.chat.rendering`'s
    own docstring) -- `text` is left exactly as composed above either
    way, so nothing here changes what `_honest_ending` already decided to
    write there.
    """
    if Turn.objects.filter(conversation=conversation, index__gt=turn.index).exists():
        turn.index = Turn.next_index(conversation)
    turn.text = text
    turn.artifacts = list(artifacts)
    turn.data = {"appended_tool_result": list(appended_tool_result)} if appended_tool_result else None
    update_fields = ["index", "text", "artifacts", "data", "state"]
    if error:
        turn.state = Turn.State.FAILED
        turn.error = error
        update_fields.append("error")
    else:
        turn.state = Turn.State.DONE
    turn.save(update_fields=update_fields)
    return {
        "turn_id": turn.pk,
        "text": text,
        "artifacts": list(artifacts),
        "tool_calls": tool_calls or [],
        "steps_used": steps_used,
        "answered_by": answered_by,
        "summary": f"{conversation.agent.slug}: {steps_used} step(s)",
    }
