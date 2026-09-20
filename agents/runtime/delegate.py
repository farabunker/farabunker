"""Agent-as-tool: a nested agent loop, inline, inside the SAME turn job
and on the SAME budget (spec section 6.4, deviation D4).

THREE properties make this safe, and only one of them is the depth cap:

1. THE BUDGET IS SHARED, NOT NESTED. A delegate spends from the root
   turn's `StepBudget` -- the same mutable object, reached through
   `ToolContext`. Total LLM calls per turn stay bounded by
   `Agent.max_steps` however the delegation tree is shaped. This is the
   real guard.
2. THE DEPTH CAP IS ENFORCED BY OMISSION. At
   `depth == MAX_AGENT_DEPTH - 1` the `agent.*` specs are left out of the
   delegate's tool list, so the model is never offered a tool it would
   be refused for using. The `ToolRefused` branch below is
   belt-and-braces for a malformed call, not the mechanism.
3. A DELEGATE GETS A FRESH MESSAGE LIST. Its own system prompt plus the
   task string -- never the parent's history. A delegate is a subroutine
   with an assignment, not a second participant in the conversation;
   replaying the parent's history would hand it context nobody asked it
   about and make its budget spend unpredictable.

   THE ONE THING THAT FRESH LIST DOES INHERIT is the clock line (round
   21), through the SAME `agents.runtime.prompt.time_aware_now_line`
   helper `build_messages` calls, governed by the SAME operator toggle.
   Every other exemption on this path is about the PARENT'S context;
   what day it is is not the parent's context, it is a fact about the
   world, and a delegate's text reaches a user exactly like a root
   turn's does. See the comment on the message build itself for the
   full reasoning, including why the per-turn timestamps have no work
   to do here.

It runs INLINE, in the same job. It enqueues nothing and waits on
nothing -- the rule every tool runner obeys.
"""
from __future__ import annotations

from llama_index.core.llms import ChatMessage, MessageRole

from agents.contracts.tools import ToolRefused, ToolResult
from agents.limits import MAX_AGENT_DEPTH
from agents.models import Conversation
from agents.resident import AGENT_TOOL_PREFIX
from agents.runtime import loop as loop_module
from agents.runtime import prompt as prompt_module
from agents.runtime.bindings import resolve_chat
from agents.runtime.loop import available_tools, run_loop
from models.contracts import gateway
from models.registry.bindings import UNRESTRICTED_MODEL_ACCESS

# Module scope, not in-body: nothing here is heavy, nothing touches a
# database at import time, and `agents/apps.py::ready()` never imports
# this module at all (the runner is a dotted-path STRING). The lazy-import
# discipline exists to keep `ready()` light; it is not a house style to
# apply where it buys nothing. `agents.runtime.loop` does not import this
# module back, so there is no cycle. `prompt` is reached through the
# MODULE for the same reason `loop` is: a test patching `agents.runtime.
# prompt.time_aware_now_line` by attribute must actually reach the call
# below, which a name copied into this namespace at import time would
# not. `agents.runtime.prompt` imports neither this module nor `loop`,
# so this adds no cycle either.


def run_agent_tool(args: dict, ctx) -> ToolResult:
    """Run the agent named by `ctx.tool_key` on `args["task"]`.

    Refuses -- `ToolRefused`, no retry -- when the depth cap is reached,
    the budget is already spent, `ctx.tool_key` names no agent, or the
    named agent does not exist or is disabled. None of those is
    something the model can talk its way out of, which is exactly what
    separates a refusal from an error (section 10.1).
    """
    slug = (ctx.tool_key or "")[len(AGENT_TOOL_PREFIX):] if ctx.tool_key else ""
    if not slug:
        # `invoke_tool` sets `tool_key`. A blank one means this runner
        # was reached some other way, and guessing which agent was meant
        # would run the wrong one.
        raise ToolRefused(
            "This delegation tool was invoked without naming an agent, so nothing ran."
        )
    if ctx.depth >= MAX_AGENT_DEPTH:
        raise ToolRefused(
            f"Delegation is limited to {MAX_AGENT_DEPTH} levels and this turn is "
            f"already at that limit. Do the work yourself or answer with what you have."
        )
    if ctx.budget.exhausted or ctx.budget.expired:
        raise ToolRefused(
            "This turn has no steps or time left, so another agent cannot be started."
        )

    # THROUGH `visible_agents`, never `Agent.objects`. A delegate is the
    # one hop where "an agent is never a way around labels" has to be
    # true of the AGENT and not only of its tools -- otherwise a
    # restricted agent is one delegation away from anybody who can name
    # its slug. The refusal copy is unchanged: from the caller's side a
    # restricted agent and an absent one are the same fact, and telling
    # them apart would leak which slugs exist.
    from agents.visibility import visible_agents

    # `visible_agents` already filters `enabled=True`, so the `enabled`
    # term goes with the manager call it came from -- no second filter
    # needed here.
    agent = visible_agents(ctx.principal).filter(slug__iexact=slug).first()
    if agent is None:
        raise ToolRefused(f"There is no enabled agent called {slug!r} on this system.")

    depth = ctx.depth + 1
    # THE ACTING RULE: NO HOP WIDENS THE ACTING PRINCIPAL. A
    # delegate inherits `ctx.principal` -- the ROOT caller's -- verbatim;
    # it never mints one of its own from the agent it is about to run.
    # `run_loop` below is handed this SAME principal and stamps the
    # delegate's own `agent_slug` onto its `ToolContext`, which is the
    # whole of "an agent is never a way around labels": WHO this is
    # being done for never changes, only WHOSE tool declaration is in
    # force.
    principal = ctx.principal

    # THE ACTING RULE'S GRANT HALF (IA-2): `ctx.tool_access` is the
    # ROOT's own access, REUSED verbatim -- never rebuilt from this
    # delegate's own agent or principal. Computed here, before the
    # conversation is even required to exist, because what this
    # delegate may call depends only on the acting principal and its
    # own declared tools, never on the conversation it happens to run
    # in.
    available = available_tools(principal, agent, ctx.tool_access)
    if depth >= MAX_AGENT_DEPTH:
        # Property 2: omission, not refusal.
        available = {
            key: spec for key, spec in available.items()
            if not key.startswith(AGENT_TOOL_PREFIX)
        }

    if not ctx.conversation_id:
        # A blank id means this runner was reached without the
        # conversation the real loop always supplies (`run_loop` always
        # passes `str(conversation.id)`) -- guessing one, or letting a
        # blank string reach `Conversation.objects.get(id="")` and raise
        # `Conversation.DoesNotExist`, would surface as an unclassified
        # `error` instead of the honest refusal this state actually is.
        raise ToolRefused(
            "This delegation tool has no conversation to run in, so nothing ran."
        )
    conversation = Conversation.objects.get(id=ctx.conversation_id)

    # RULING: an unbound delegate chat
    # role is refusal-class, exactly like `tools.rag.tools.run_ask`'s
    # and `tools.vision.tools.run_generate`'s own unbound-role ->
    # `ToolRefused` translations -- nothing the model can say fixes it.
    # `resolve_chat(agent, None)` is the SAME one function the planner
    # and the root loop call, so a delegate cannot resolve its own role
    # by a different rule; `None` because a delegate never inherits the
    # caller's picker override, it binds its own role. `None` takes the
    # ROLE path, which spec section 9.5 exempts from `ModelAccess`
    # entirely -- a delegate never carries a picked connection, so
    # `UNRESTRICTED_MODEL_ACCESS` is passed EXPLICITLY, with this reason,
    # rather than defaulted, because the argument is required precisely
    # so nobody omits it by accident.
    # `gateway.get_llm_for` is reached through the MODULE (never a
    # hoisted `from`-import) so a test patching
    # `models.contracts.gateway.get_llm_for` actually sees this call
    # (see `patch_llm`), and it is wrapped in the SAME try as
    # `resolve_chat` because it can fail on an unresolvable connection
    # for the identical reason.
    #
    # `request_timeout=loop_module.resolved_turn_timeout(ctx.job)` (B2,
    # fix round 1): this delegate hop spends from the ROOT turn's SHARED
    # budget (`budget=ctx.budget` below, `job_ctx=ctx.job`) -- without
    # this, its own chat client kept the engine's own hidden 300s
    # default while the shared budget it spends from may now run up to
    # 7200s, exactly the competing-timeout incident this task exists to
    # close, surviving on this one hop. `loop_module.resolved_turn_
    # timeout` is the SAME resolver `_run_turn` calls for the root loop,
    # reached through the module (not a hoisted `from`-import) for the
    # same patchability reason every other `loop_module.*` reference on
    # this path already is. `max(loop_module.RESOLVED_TIMEOUT_FLOOR_
    # SECONDS, ...)` (MINOR 7, fix round 3): floored here, at the ACTUAL
    # engine-client boundary, for the identical reason `_run_turn`'s own
    # call floors it there and nowhere else -- see `resolved_turn_
    # timeout`'s own docstring.
    try:
        resolved, _name = resolve_chat(agent, None, access=UNRESTRICTED_MODEL_ACCESS)
        llm = gateway.get_llm_for(
            resolved,
            request_timeout=max(
                loop_module.RESOLVED_TIMEOUT_FLOOR_SECONDS,
                loop_module.resolved_turn_timeout(ctx.job),
            ),
        )
    except ValueError as exc:
        raise ToolRefused(
            f"The {agent.slug} agent's model role ({agent.llm_role!r}) is not bound "
            f"to a model on this install, so {agent.slug} cannot be started."
        ) from exc

    # Section 10.1's "bound model cannot call tools" row, reused from
    # the ROOT loop so a tool-incapable model is refused the SAME
    # honest way at every depth of delegation. Checked, and answered,
    # BEFORE `run_loop` so a model that cannot call tools never spends
    # the shared budget on a blind loop it cannot use tools in.
    #
    # Reached through the MODULE (`loop_module.supports_tool_calling`),
    # never a hoisted `from`-import, for the SAME reason `gateway.
    # get_llm_for` is above: `patch_llm` patches `agents.runtime.loop.
    # supports_tool_calling` by attribute, and a `from ... import
    # supports_tool_calling` here would have copied the reference at
    # import time -- the patch would rebind `loop`'s own attribute and
    # never reach a name already copied into this module's namespace.
    if available and loop_module.supports_tool_calling(resolved) is False:
        return ToolResult(
            text=loop_module.NO_TOOL_CALLING_NOTE,
            data={"agent": agent.slug, "steps_used": 0, "tool_calls": []},
        )

    # BUILDS ITS OWN LIST, deliberately, on the same terms `agents.
    # runtime.prompt._instructions_block`'s own docstring already states
    # for the stream's instructions prose: a delegate gets no
    # attachments block either (round 11 review, minor 7) -- consistent
    # with that precedent, and unrecorded until now for the identical
    # reason. If a delegate ever needs to know what its ROOT turn's
    # conversation has attached, the fix is one call to `agents.
    # attachments.attached_documents` here, not a parallel copy.
    #
    # THE CLOCK IS THE ONE THING IT DOES INHERIT (round 21 fix round 1,
    # owner's own word: the model "should ALWAYS have time stamps").
    # Every exemption above is about the PARENT'S context -- prose the
    # delegate was not addressed by, files it was not handed. What day
    # it is is not the parent's context; it is a fact about the world
    # this agent is answering in, and a delegate's text reaches a user
    # exactly like a root turn's does, so a time-blind delegate
    # reproduces the owner's original symptom verbatim ("2026 must be a
    # future date") through the one path nobody was watching.
    #
    # ONE CALL TO THE SHARED HELPER (`agents.runtime.prompt.time_aware_
    # now_line`), never a re-typed line: the wording, the timezone rule
    # and the `ChatSettings.time_aware` toggle all live there, so this
    # path and `build_messages` cannot drift, and the operator's one
    # switch governs both. `""` when the toggle is off -- and the JOIN
    # is shared too now (`append_paragraph`, audit 2 F10), which is
    # what absorbs that empty case: no truthiness check here, and no
    # second copy of "append this as its own paragraph".
    #
    # THE LINE ONLY, not per-turn timestamps -- and that is honest
    # rather than partial: the list below is TWO fresh messages built
    # here, with no replayed history to stamp. A delegate's own turns
    # are written at depth >= 1 and never replay into anything
    # (`agents.runtime.prompt.history_messages`' own `_ROOT_DEPTH`
    # rule), so there is no second half of the feature for this path to
    # be missing.
    system = agent.system_prompt
    system = prompt_module.append_paragraph(
        system, prompt_module.time_aware_now_line())
    messages = []
    if system:
        messages.append(ChatMessage(role=MessageRole.SYSTEM, content=system))
    messages.append(ChatMessage(role=MessageRole.USER, content=args["task"]))

    result = run_loop(
        agent=agent,
        conversation=conversation,
        messages=messages,
        llm=llm,
        budget=ctx.budget,          # SHARED, not a fresh one
        principal=principal,
        job_ctx=ctx.job,
        depth=depth,
        available=available,
        access=ctx.tool_access,    # REUSED, never rebuilt -- see above
        # THE ACTING RULE'S STREAM HALF: `ctx.stream` is the ROOT turn's
        # own scope, inherited verbatim -- a delegate is not a way around
        # a wall any more than it is a way around a label (spec §24
        # concern 2).
        stream=ctx.stream,
    )
    return ToolResult(
        text=result.text,
        data={"agent": agent.slug, "steps_used": result.steps_used,
              "tool_calls": list(result.tool_calls)},
        artifacts=result.artifacts,
    )
