"""Can this agent take a turn at all -- asked ONCE, answered the same
way for every caller.

Spec section 10.1's first three rows ("chat role unbound", "bound model
cannot call tools", and the picked connection that no longer exists)
are refusals that must happen BEFORE anything is written, so a turn
that cannot possibly run never becomes a queued job somebody has to go
and cancel. `manage.py agent_turn` has raised them since P2; `/chat/`
raises the same three, and a third hand-rolled copy of the rule in a
view is exactly the drift `agents.runtime.bindings.resolve_chat` was
extracted to prevent (P2 review, finding M7).

It does NOT replace `agents.runtime.loop._run_turn`'s own tool-calling
check. That one is the last-resort HONEST ENDING for a turn that got
queued anyway -- a binding can change between the enqueue and the run
(ADR 0013:205-213), and a queued job never trusts an enqueue-time
decision as its run-time truth. Two checks, two different jobs.

`supports_tool_calling` is reached through the MODULE
(`loop_module.supports_tool_calling(...)`), never a `from ... import`
bound at import time: the name is looked up at call time, which is what
lets a test patch it and have this call see it. A hoisted `from`-import
copies the function object into this namespace and the patch never
reaches it (P2 review, finding N4).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.contracts.tools import granted_tools
from agents.entitlements import tool_access_for, wall_for
from agents.runtime import loop as loop_module
from agents.runtime.bindings import resolve_chat
from models.registry.bindings import MODEL_FORBIDDEN_MESSAGE, model_access_for

logger = logging.getLogger(__name__)

UNBOUND = "unbound"
UNREGISTERED_CONNECTION = "unregistered_connection"
NO_TOOL_CALLING = "no_tool_calling"
MODEL_NOT_PERMITTED = "model_not_permitted"
AGENT_NOT_PERMITTED = "agent_not_permitted"

_UNREGISTERED_CONNECTION_MESSAGE = (
    "That model is no longer registered — pick another in the model console."
)

# `models.registry.bindings.MODEL_FORBIDDEN_MESSAGE` (whole-branch review
# item 3): this file used to carry its own copy, `_MODEL_NOT_PERMITTED_
# MESSAGE`, byte-identical to `tools/vision/views.py`'s and `tools/rag/
# views.py`'s own private copies under a third name.

_AGENT_NOT_PERMITTED_MESSAGE = (
    "This agent needs an entitlement this account does not hold. The conversation is "
    "still readable; new turns are not."
)

# `ModelConnection` uses `BigAutoField` (`settings.DEFAULT_AUTO_FIELD`), so
# Postgres's `bigint` range is the honest upper bound for a pk. NEVER-500
# reason this check exists at all: `int("9" * 25)` parses fine (Python
# ints are unbounded), and the ONLY reason it does not currently reach
# Postgres as a raw `DataError` ("integer out of range") is that Django's
# own integer-lookup machinery short-circuits an out-of-range `pk=` query
# to `EmptyResultSet` (`django/db/models/lookups.py`'s `IntegerFieldOverflow`
# mixin) -- an ORM implementation detail this module has no business
# depending on. Rejecting an implausible id HERE makes the boundary
# explicit and self-documenting rather than an accident of how Django's
# lookup compiler happens to behave today, and it is cheap enough that
# there is no reason not to.
_BIGINT_MAX = 9223372036854775807


@dataclass(frozen=True)
class Preflight:
    """Whether a turn may be queued, and why not.

    `reason` is a CLOSED vocabulary, not prose: the page maps it to
    copy and the command maps it to an exit, and a caller that had to
    match on `message` would break the first time the wording improved.
    `message` is the operator-readable sentence -- the platform's own,
    never a reworded copy.

    `dropped_tools` is the TOLERANT half (spec section 8.3 step 3): a
    granted tool that is not registered here, or whose role will not
    resolve, is reported as a NOTE and the turn still runs without it.
    A refusal and a note are different things and this type keeps them
    apart.
    """

    ok: bool
    reason: str
    message: str
    resolved: object | None
    answered_by: str
    dropped_tools: tuple[str, ...]
    # THE ACTING PRINCIPAL HOLDS NO ENTITLEMENT FOR THESE. A separate
    # tuple from `dropped_tools`, not a widening of it, because the two
    # have different operator-facing copy -- and this one's copy is
    # nothing at all. Defaulted so every existing construction, and every
    # test that builds one, is unchanged.
    unentitled_tools: tuple[str, ...] = ()


def dropped_tool_notes(check: Preflight) -> tuple[str, ...]:
    """One sentence per `check.dropped_tools` key, naming it and saying
    this turn runs without it (CQ-6).

    Both `agents.chat.service.start_turn` (a POST's 202 JSON body) and
    `agents.chat.views.thread.thread_context` (a GET's `notes`) call
    this, from the SAME `Preflight.dropped_tools`, so a POST's note and
    a GET's note for the same turn can never disagree -- a promise a
    copied f-string in each module could only assert, never keep.
    """
    return tuple(
        f"{key} is not available on this install, so this turn runs without it."
        for key in check.dropped_tools
    )


def _plausible_connection_id(connection) -> bool:
    """True when `connection` could conceivably name a `ModelConnection`
    row -- an integer, and one that fits the `bigint` pk column. See
    `_BIGINT_MAX`'s own comment for why this check exists at all."""
    try:
        value = int(connection)
    except (TypeError, ValueError):
        return False
    return 0 < value <= _BIGINT_MAX


def preflight_turn(agent, connection, *, actor, conversation=None, wall=None,
                   tool_access=None) -> Preflight:
    """Resolve `agent`'s chat model and check it can do what `agent`
    needs, without writing anything.

    `wall`, OPTIONAL and keyword-only (E4 follow-up): the already-read
    wall for `conversation`, when a caller has one in hand. `None` (the
    default) means "look it up here", exactly as before -- this function
    still computes it itself for `agents.chat.service.start_turn` and
    `manage.py agent_turn`, neither of which has a wall to hand over.
    `agents/chat/views/thread.py::thread_context` is the one caller that
    does: it also builds the picker's own wall-gated options for the
    SAME conversation on the SAME render, so it reads the wall once and
    passes it here AND there, rather than this function reading it again
    for a value the caller already has.

    `tool_access`, OPTIONAL and keyword-only (I1 follow-up, round 9's
    review): an already-built NO-WALL `ToolAccess` for `actor`, when a
    caller has one in hand -- `agents/chat/views/thread.py::thread_
    context` is that caller (its own `may_attach_files` gate needs the
    identical no-wall value `tools/rag/views.py::_may_upload` checks,
    computed once for both uses). Reused here ONLY when `wall` is
    itself empty: `tool_access_for`'s own `(held & wall) if wall else
    held` makes an EMPTY wall a no-op, so a no-wall value and a
    wall-scoped-by-nothing value are the SAME call with the SAME
    arguments -- provably safe to reuse. A NON-empty wall always
    recomputes fresh: `tool_access` may have taken the `sees_all_
    content`-and-no-wall shortcut (`UNRESTRICTED_TOOL_ACCESS`, built
    with no query at all), whose `.held`/`.required` are placeholder
    defaults, not real data -- narrowing THAT by a real wall would
    silently under-grant an administrator inside a scoped stream. `None`
    (the default) means "compute it here", exactly as before, for
    `agents.chat.service.start_turn` and `manage.py agent_turn`, neither
    of which has one to hand over.

    `actor` is REQUIRED and keyword-only, deliberately -- same rule as
    `agents.chat.service.start_turn`'s own `actor` keyword: the
    acting rule asks "what may THIS PRINCIPAL do", never "what
    may the agent do", and a default would let a forgetful caller
    silently ask the wrong question. Both callers of this function
    already hold one: `agents.chat.service.start_turn` has its own
    `actor` parameter, and `manage.py agent_turn` acts as the one
    `SERVICE_PRINCIPAL` every shell path uses.

    `conversation` is the row this turn will be posted to, or `None` for a
    caller that has none. IT DECIDES THE WALL: a turn in a workstream is
    narrowed at this seam as well as at the planner and the loop, because
    without it spec §17.3's *"refused at `preflight_turn`, before a turn
    row is written"* is impossible -- the turn row gets written and the
    refusal lands mid-run, which `agents/runtime/jobs.py` records as the
    wrong order to fail in.

    `None` MEANS NO WALL, not "look it up": this function is the render
    half for two of its three callers, and a default that queried would
    make a page render pay for a stream it may not even be on.

    CHECKED FIRST, before a model is even resolved (spec section 9.6,
    knock-on 3): there is no point resolving a model for a turn that
    cannot run at all.

    `label_permitted_q(actor)` ALONE, not `visible_agents(actor)`. This
    function is reached for a turn on a conversation the actor is
    ALREADY POSTING TO -- a `use`-level `Share` recipient among them --
    and `visible_agents` also encodes the ownership/resident/share OR
    that governs whether a principal may START a conversation with an
    agent from scratch, which is a different question from "did a LABEL
    just restrict this one". Asking the fuller function here would 403 a
    shared-conversation poster the moment their turn reached preflight,
    for an agent they were never meant to own or discover, breaking
    sharing for every unlabelled agent. `label_permitted_q` alone is
    exactly the label half: UNLABELLED always passes regardless of
    ownership, and a LABELLED row passes only for a holder of one of its
    entitlements -- which is what makes a `resident=True` row compose
    with the carve-out rather than bypass it, and what makes an OWNER's
    own labelled agent hide from them too (decision 35): ownership plays
    no part in this test either way.

    `sees_all_content(actor)` SHORT-CIRCUITS FIRST, exactly as it does
    inside `visible_agents` itself: an open box (accounts off) or an
    administrator with the content setting on reaches every row
    regardless of any label, and skipping the query entirely on an open
    box is what keeps this check from costing an open install anything.
    """
    from agents.models import Agent
    from agents.visibility import label_permitted_q
    from identity.access import sees_all_content, settings_row as identity_settings_row

    # ONE `IdentitySettings` READ FOR THE WHOLE FUNCTION (T14-style
    # follow-up, IA-2 whole-branch review item 2): threaded into
    # `sees_all_content` here AND into both `model_access_for` and
    # `tool_access_for` below via their own `settings_row=` keyword, so a
    # turn's preflight pays one singleton read rather than three.
    settings = identity_settings_row()
    if wall is None:
        wall = wall_for(conversation) if conversation is not None else frozenset()
    if not sees_all_content(actor, settings_row=settings) and not Agent.objects.filter(
        pk=agent.pk
    ).filter(label_permitted_q(actor)).exists():
        return Preflight(False, AGENT_NOT_PERMITTED, _AGENT_NOT_PERMITTED_MESSAGE,
                         None, "", ())
    if connection not in (None, "") and not _plausible_connection_id(connection):
        # Refused HERE, before `resolve_chat` ever reaches the database:
        # an implausible id is the same operator-facing story as a
        # legitimate but unknown one -- the picked model is not there --
        # and the alternative is a `DataError` traceback from a public
        # POST.
        logger.info("chat: picked connection %r is not a plausible id", connection)
        return Preflight(False, UNREGISTERED_CONNECTION,
                         _UNREGISTERED_CONNECTION_MESSAGE, None, "", ())
    access = model_access_for(actor, settings_row=settings, wall=wall)
    try:
        resolved, answered_by = resolve_chat(agent, connection, access=access)
    except (ValueError, TypeError) as exc:
        # THREE CAUSES NOW, THREE MESSAGES. A picked connection the actor
        # may not USE is a different fact from one that is gone, and
        # telling somebody "that model is no longer registered" when it is
        # registered and simply not theirs is a false sentence.
        #
        # Asked of the ACCESS VALUE, never by matching on `str(exc)`:
        # message-matching is how two refusals come to share one branch.
        if connection not in (None, "") and str(connection).isdigit() \
                and not access.allows(int(connection)):
            logger.info("chat: picked connection %r is not permitted for this actor",
                        connection)
            return Preflight(False, MODEL_NOT_PERMITTED, MODEL_FORBIDDEN_MESSAGE,
                             None, "", ())
        # TWO CAUSES, TWO MESSAGES. An operator who picked a model that
        # has since been deleted has a different problem from one who
        # never bound the role, and `tools/rag/views.py` keeps the same
        # two apart for the same reason.
        if connection not in (None, ""):
            logger.info("chat: picked connection %r no longer resolves (%s)",
                        connection, exc)
            return Preflight(False, UNREGISTERED_CONNECTION,
                             _UNREGISTERED_CONNECTION_MESSAGE, None, "", ())
        return Preflight(
            False, UNBOUND,
            f"No model is assigned to the {agent.llm_role!r} role yet, so "
            f"{agent.name!r} cannot answer. Assign one in the model console.",
            None, "", (),
        )

    # TWO CAUSES, TWO TUPLES. `dropped_tools` keeps its meaning
    # exactly: a key that is not registered here, or whose role will not
    # resolve. `unentitled_tools` is the new, DIFFERENT fact -- the
    # acting principal holds no entitlement for it -- and it is rendered
    # NOWHERE: enforcement by omission, the same mechanism
    # `available_tools` already uses for the depth cap, and the same
    # reason `granted_tools` logs this drop at DEBUG rather than INFO
    # ("on a labelled install it is the NORMAL case").
    #
    # ONE `granted_tools` CALL, not two: it is what walks `_TOOLS` and
    # logs an unregistered key, and calling it twice over the identical
    # `agent.tool_keys` would log every such key twice per preflight for
    # no second fact. `access.allows` is a pure, log-free re-check over
    # the ALREADY-registered `registered` list, which is exactly what a
    # second `granted_tools(..., access)` call would have computed --
    # everything it would additionally drop (`mutates=True`, absence
    # from the registry) was already dropped by the first call.
    # I1 (round 9 review): reuse the caller's own no-wall `tool_access`
    # when `wall` is empty -- see this function's own docstring for why
    # that combination alone is safe to skip re-querying for, and why a
    # non-empty wall never takes this shortcut.
    access = (tool_access if (tool_access is not None and not wall)
              else tool_access_for(actor, settings_row=settings, wall=wall))
    registered = granted_tools(actor, agent.tool_keys)
    granted = [key for key in registered if access.allows(key)]
    dropped = tuple(key for key in agent.tool_keys if key not in registered)
    unentitled = tuple(key for key in registered if key not in granted)
    if granted and loop_module.supports_tool_calling(resolved) is False:
        # `False` means the engine REPORTED it. `None` -- it does not
        # report the fact at all -- runs the turn
        # (`models/contracts/engines/base.py:450-458`). Gated on
        # `granted` (post-entitlement), not `registered`: a principal
        # with no entitled tool at all is never going to be offered one
        # regardless of what the bound model can do, so it would be
        # dishonest to refuse them over a capability that will never be
        # exercised.
        return Preflight(
            False, NO_TOOL_CALLING,
            f"The model assigned to {agent.llm_role!r} reports that it cannot call "
            f"tools, and {agent.name!r} needs them. Assign a tool-capable model to "
            f"that role, or use an agent that needs none.",
            resolved, answered_by, dropped, unentitled,
        )
    return Preflight(True, "", "", resolved, answered_by, dropped, unentitled)
