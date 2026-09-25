"""The `agent.turn` job kind's planner, summarizer, and terminal hook.

The HANDLER is `agents.runtime.loop.run_turn` and is not re-exported
here: two functions with one name, one calling the other, is a name
nobody can grep for.

Registered by `agents/apps.py::AgentsConfig.ready()`, which imports none
of this -- every field is a dotted-path STRING, resolved lazily by
whoever calls it (`models/contracts/jobkinds.py:16-26`).
"""
from __future__ import annotations

import logging

from agents.contracts.tools import ToolAccess, get_tool, granted_tools
from agents.limits import MAX_AGENT_DEPTH
from agents.models import Agent, Turn
from agents.resident import AGENT_TOOL_PREFIX
from agents.runtime.audit import close_open_invocations
from agents.runtime.bindings import resolve_chat
from agents.runtime.flowtool import FLOW_RUN_KEY, flow_row_roles
from identity.contracts.principals import principal_from_payload
from models.contracts.bindings import resolve
from models.contracts.jobkinds import ModelRef
from models.contracts.roles import RAG_EXTRACT_ROLE, VISION_GENERATE_ROLE
from models.registry.bindings import model_access_for

logger = logging.getLogger(__name__)

_SUMMARY_MAX = 120

_INTERRUPTED = (
    "The turn running this tool call ended before the call reported an outcome."
)


def plan_turn(payload: dict) -> tuple[list[ModelRef], bool]:
    """The models a turn may load, resolved fresh at ENQUEUE time -- the
    admission snapshot `ModelRef`'s own docstring describes
    (`models/contracts/jobkinds.py:40-52`).

    The agent's own chat role, plus the UNION of the roles of every tool
    it may call, taken over the transitive closure of agent-as-tool
    delegation, bounded by MAX_AGENT_DEPTH and de-duped by agent slug so
    a delegation cycle terminates. An agent-as-tool spec AND `flow.run`
    both declare EMPTY `roles` -- a delegate's roles are not knowable at
    registration time and a flow's roles live in ROWS, not in the one
    registered spec every flow shares, so this planner supplies both by
    walking the closure (agent-as-tool) and the visible flow rows
    (`agents.runtime.flowtool.flow_row_roles`).

    THREE TOLERANT DROPS, producing exactly the same filtered set
    `agents.runtime.loop.available_tools` later builds its schemas
    from: a granted key absent from the registry is dropped
    (`granted_tools`); a `mutates=True` key is dropped (`granted_tools`);
    and a tool whose declared role will not resolve is dropped here. The
    same tolerant shape `plan_ingest` uses for its extract role
    (`tools/rag/jobs.py:408-424`). A tool the planner dropped is a tool
    the model is never offered -- which is what makes ruling R1's
    permissive save safe.

    THE AGENT'S OWN CHAT ROLE IS NOT TOLERANT. An unresolvable chat role
    raises, because the enqueuing caller preflights it and refuses
    before this planner runs; reaching here without one means the
    preflight was skipped, and a turn that queued anyway would fail
    later and less legibly.

    `exclusive=True`: a turn may load a chat model, then an embedding
    model, then an image checkpoint, sequentially, inside one job.
    Declaring that honestly is better than being folded into it by
    `effectively_exclusive`'s unmeasured-footprint branch, which is
    where an undeclared turn would land anyway.

    ONLY THE CHAT ROLE IS `synchronous` (2026-09-24). This handler drives
    the chat model itself, in-process, every run; every other ref here is
    a TOOL's role (a delegate's own chat role included -- it is reached
    through an agent-as-tool call, not by this handler), declared so the
    queue protects and accounts for it, and tagged `synchronous=False` so
    the barrier does not treat its endpoint as one this turn is about to
    load at. The case that made it matter: an agent granted the
    image-generation tool put the image endpoint in every turn's own-set,
    and the barrier waited there for a release to settle -- a ~22s stall
    guarding a load this run never makes, since that tool enqueues its own
    job and submits on its own path. See `ModelRef.synchronous` and ADR
    0013's 2026-09-24 amendment.

    Footprints are left `None`, per `models/queue/scheduler.py`'s
    provenance contract: claim-time code fills them in from a FRESH
    lookup, never from this snapshot.

    A FOURTH, NARROWER TOLERANT ADDITION (vision-describes-its-own-output
    task, ruling 3): when `vision.generate`'s own ROLE (`models.
    contracts.roles.VISION_GENERATE_ROLE` -- the same string the tool's
    `ToolSpec.roles` declares, so its presence in the walked role set
    below IS "the image tool is granted") turns up in the walk, `rag.
    extract` is declared TOO, resolved tolerantly (unbound -> declare
    nothing, exactly the drop every OTHER role above already gets) and
    `synchronous=False` -- the SAME flag `vision.generate` itself
    carries, and for the identical reason: this handler never drives
    either model in-process itself, a TOOL (`tools.vision.tools.
    run_generate`) does, on its own path, so the barrier must not wait
    for either endpoint to settle before a turn that may not even call
    the tool this run.

    DELIBERATELY NOT ADDED TO THE TOOL'S OWN `ToolSpec.roles` -- that was
    investigated and rejected: `_roles_resolve` (`agents.runtime.loop`)
    drops a tool from the turn ENTIRELY when any of its declared roles
    fails to resolve, so an unbound `rag.extract` would make image
    generation itself vanish from every turn that granted it -- a far
    worse regression than the missing description this task fixes.
    Declaring it here, conditionally, alongside the walk's own tolerant
    drops, is what keeps the turn's own admission honest about a SECOND
    model the granted tool may now use, without EVER gating the tool's
    own availability on it.

    VERIFIED (never assumed) THAT THIS CANNOT REFUSE A TURN under memory
    pressure: this function already returns `exclusive=True`
    UNCONDITIONALLY, above -- the same declaration `tools.vision.jobs.
    plan_generate` makes, and for the same reason `models/queue/
    scheduler.py`'s rule 2(a) makes it moot: an exclusive candidate is
    admitted ONLY into a genuinely idle machine (rule 5) or held for a
    later round while something else runs (rule 3) -- an OVERSIZE
    exclusive candidate STILL gets that same "admit alone when idle"
    treatment, never a permanent refusal (rule 6's own words: "refusing
    it forever would deadlock the owner's own job forever, which is
    worse than one job running over the operator's stated budget"). A
    turn was already exclusive, with or without this ref -- adding one
    more `ModelRef` cannot change whether the machine admits it, only
    what stays protected/swept while it runs. Whether the describing
    model actually loads is therefore a genuinely runtime question,
    answered honestly by `services.describe_output`'s own non-fatal
    contract, never an admission-time refusal.
    """
    turn = Turn.objects.select_related(
        "conversation__agent", "conversation__workstream").get(pk=payload["turn"])
    agent = turn.conversation.agent

    # THE ACTING RULE: the same principal `_run_turn` will
    # derive from this same payload at run time -- so the admission
    # snapshot the queue sees is never computed against a wider tool
    # set than the turn is actually offered.
    actor = principal_from_payload(payload)
    # ONE READ, TWO AXES (spec §13's "the wall value is threaded from
    # here through all four call sites rather than re-read at each").
    from agents.entitlements import tool_access_for, wall_for
    wall = wall_for(turn.conversation)
    # THE MODEL HALF (IA-2 T14): built beside `tool_access_for` below, so
    # a connection the actor may not USE is refused HERE, at enqueue --
    # half of the directive's "refuses at preflight/enqueue, not mid-run"
    # (`preflight_turn` is the other half, ahead of this on the same
    # payload). `model_access_for` is imported at MODULE level (T14
    # review finding 4) -- `agents.runtime.preflight` already imports it
    # the same way with no cycle, since `models.registry.bindings` does
    # not import anything under `agents/`.
    model_access = model_access_for(actor, wall=wall)
    chat_resolved, chat_name = resolve_chat(agent, payload.get("connection"),
                                            access=model_access)
    # `synchronous=True` (the default, said out loud beside the tool
    # loop's `False`): this handler drives the chat model itself.
    refs = [_ref(agent.llm_role, chat_resolved, chat_name, synchronous=True)]

    # THE ACTING RULE'S GRANT HALF (IA-2): built ONCE, here, and threaded
    # through the whole walk below -- an agent is never a way around
    # labels, so every hop of the closure asks the SAME acting
    # principal's access, never a wider one recomputed per agent visited.
    access = tool_access_for(actor, wall=wall)
    seen_roles = {agent.llm_role}
    tool_roles = sorted(_tool_roles(agent, actor, access))
    for role in tool_roles:
        if role in seen_roles:
            continue
        try:
            # `synchronous=False`: a TOOL (or a delegate) may use this
            # model, and this handler never drives it in-process. The
            # image-generation role is the case that made this matter --
            # the vision tool enqueues its own job and submits there on
            # its own path, so the turn never loads at that endpoint
            # during this run. The ref is still declared, so the model
            # stays protected and accounted for; it is simply not one of
            # the turn's OWN endpoints for the barrier's wait decision
            # (`ModelRef.synchronous`, and ADR 0013's 2026-09-24
            # amendment: an image endpoint counted as "own" put a ~22s
            # settle poll in front of every chat turn).
            refs.append(_ref(role, resolve(role), synchronous=False))
            seen_roles.add(role)
        except Exception:  # noqa: BLE001 -- tolerant drop, see docstring
            logger.info("agent.turn: role %r does not resolve; its tool is dropped", role)

    # vision-describes-its-own-output task, ruling 3 -- see this
    # function's own docstring for the full reasoning. `VISION_GENERATE_
    # ROLE in tool_roles` IS "the image-generation tool is granted (and
    # resolves)": that role enters `tool_roles` from nowhere but that
    # tool's own `ToolSpec.roles`. Conditional and tolerant, exactly like
    # every role in the loop above -- an unbound `rag.extract` declares
    # nothing here, never an error, and never touches whether the image
    # tool itself is offered.
    if VISION_GENERATE_ROLE in tool_roles and RAG_EXTRACT_ROLE not in seen_roles:
        try:
            refs.append(_ref(RAG_EXTRACT_ROLE, resolve(RAG_EXTRACT_ROLE), synchronous=False))
        except Exception:  # noqa: BLE001 -- tolerant drop, see docstring
            logger.info(
                "agent.turn: rag.extract does not resolve; the image tool's own "
                "description step will simply be skipped this turn"
            )
    return refs, True


def _tool_roles(agent, actor, access: ToolAccess) -> set[str]:
    """Every role reachable from `agent`, walking agent-as-tool to
    MAX_AGENT_DEPTH, asked with `actor` at every hop. De-duped by slug,
    so a cycle terminates.

    `actor` is the SAME principal at every step of the walk -- the
    acting rule means a delegate never widens who is asking,
    so the closure is walked as the one acting principal throughout,
    never re-minted per agent the walk visits. `access` is the SAME
    `ToolAccess` at every hop too, for the identical reason (IA-2's
    grant half): a tool the acting principal may not call must not
    contribute its role to the admission snapshot either.

    IA-2 T15: an `agent.<slug>` key whose slug `actor` may not run
    (`visible_agent_slugs`) is dropped from the walk too, the same
    enforcement-by-omission `agents.runtime.loop.available_tools` uses --
    otherwise the queue would be told to admit a role for a delegation
    the turn can never actually make.
    """
    # LAZY, and `None` (not an empty frozenset) is the "not fetched yet"
    # sentinel -- see `agents.runtime.loop.available_tools`'s identical
    # guard. Fetched at most once across the WHOLE walk, on the FIRST
    # `agent.*` key seen at any hop, not once per hop.
    visible_slugs = None
    roles: set[str] = set()
    frontier = [(agent, 0)]
    visited = {agent.slug.lower()}
    while frontier:
        current, depth = frontier.pop()
        for key in granted_tools(actor, current.tool_keys, access):
            if key.startswith(AGENT_TOOL_PREFIX):
                if visible_slugs is None:
                    from agents.visibility import visible_agent_slugs

                    visible_slugs = visible_agent_slugs(actor)
                if key[len(AGENT_TOOL_PREFIX):].lower() not in visible_slugs:
                    # OMISSION, not refusal -- see `agents.runtime.loop.
                    # available_tools`'s identical guard.
                    continue
            spec = get_tool(key)
            roles.update(spec.roles)
            if key == FLOW_RUN_KEY:
                # `flow.run` declares `roles=()` -- a flow's roles are
                # its STEPS' roles, and they live in ROWS, so they are
                # supplied HERE, at enqueue time, exactly as a
                # delegate's are. `plan_turn` already touches the
                # database (it loads the turn and its agent), so
                # reading the flows costs one more query and no new
                # constraint. Without this the queue is never told a
                # flow needs the embedding model, and the admission
                # snapshot is simply wrong.
                roles.update(flow_row_roles(actor))
                continue
            if not key.startswith(AGENT_TOOL_PREFIX) or depth >= MAX_AGENT_DEPTH:
                continue
            slug = key[len(AGENT_TOOL_PREFIX):]
            if slug.lower() in visited:
                continue
            visited.add(slug.lower())
            delegate = Agent.objects.filter(slug__iexact=slug, enabled=True).first()
            if delegate is not None:
                # `agent_tool_specs()` promises `plan_turn` supplies a
                # delegate's roles at enqueue time -- and a delegate's OWN
                # `llm_role` is one of those roles, not just the roles of
                # the tools IT was granted. Without this, a delegate whose
                # chat role differs from the root's would run under a
                # model `plan_turn` never told the queue to admit.
                roles.add(delegate.llm_role)
                frontier.append((delegate, depth + 1))
    return roles


def _ref(role: str, resolved, connection_name: str = "", *,
         synchronous: bool = True) -> ModelRef:
    return ModelRef(
        role=role,
        engine=resolved.engine,
        endpoint=resolved.endpoint,
        model_id=resolved.model_id,
        connection_name=connection_name,
        synchronous=synchronous,
    )


def summarize_turn(payload: dict) -> str:
    """A one-line row summary for `/queue/`.

    TOUCHES NO DATABASE and raises nothing. `/queue/` renders many rows
    and calls this for each; a summarizer that queried would turn one
    page render into N, and `models/queue/views.py::_summarize` already
    treats a summarizer failure as something to degrade around rather
    than surface. Everything it needs is in the payload, which is why
    the payload carries `agent` and `text` at all.
    """
    agent = str(payload.get("agent") or "an agent")
    text = " ".join(str(payload.get("text") or "").split())
    if len(text) > _SUMMARY_MAX:
        text = text[:_SUMMARY_MAX].rstrip() + "..."
    return f"{agent}: {text}" if text else f"{agent}: a turn"


def on_turn_terminal(payload: dict, state: str) -> None:
    """Fix up the placeholder assistant turn of a job that reached a
    terminal state WITHOUT its handler ever running.

    THREE paths, all of them handler-never-started: cancelled while
    still queued (`models/queue/backend.py::cancel_job`), permanently
    failed by a SECOND orphaning (`models/queue/claim.py::
    _sweep_orphans`), and a handler that could not be resolved at all
    (`models/queue/worker.py::Worker._execute`'s `handler_started`
    guard). It does NOT fire when the handler ran and raised -- that
    case is `run_turn`'s own `except`, and two writers for one row's
    final state is exactly what the `handler_started` gate exists to
    prevent.

    ONE conditional UPDATE, filtered on the states a stranded turn can
    be in -- never a read-then-save. This runs AFTER the job row's
    terminal write commits (all three call sites schedule it with
    `transaction.on_commit`), so a still-alive "stale" worker -- one the
    orphan sweep gave up on but that had not actually died -- can be
    writing DONE in the window between a read and a write. The
    `state__in` guard makes clobbering that impossible: it only ever
    matches a row still queued or running at the instant of the write
    itself.

    Raises nothing. `invoke_on_terminal` catches and logs
    (`models/contracts/jobkinds.py:291-320`), but a hook that relies on
    its caller's tolerance is a hook that will one day be called by
    something less tolerant.

    IT ALSO CLOSES THE TURN'S OPEN TOOL-INVOCATION ROWS. A job
    cancelled while still queued has none; a job orphaned twice may.
    Leaving one open makes it read as "still running" forever
    (`agents.runtime.audit.invocation_state`), which is the one reading
    worse than an honest failure.
    """
    turn_id = payload.get("turn")
    if not turn_id:
        return
    Turn.objects.filter(
        pk=turn_id, state__in=(Turn.State.QUEUED, Turn.State.RUNNING),
    ).update(
        state=Turn.State.CANCELLED if state == "cancelled" else Turn.State.FAILED,
        error=(
            "Cancelled from the queue before it ran."
            if state == "cancelled"
            else "The worker running this turn stopped responding; it was not retried again."
        ),
    )
    # The turn's OWN audit rows, closed on the same terminal event.
    # This reads one column before it writes, which the paragraph above
    # rules out for the TURN's state -- and the distinction is real:
    # the read here is for CORRELATION (`queue_job_id` is immutable
    # once stamped), not for a state this hook then conditionally
    # overwrites. The write itself is still one conditional UPDATE,
    # filtered on `finished_at IS NULL`.
    job_id = (
        Turn.objects.filter(pk=turn_id)
        .values_list("queue_job_id", flat=True).first()
    )
    close_open_invocations(job_id, reason=_INTERRUPTED)
