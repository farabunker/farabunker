"""Which model answers this turn.

ONE function, asked once and answered the same way for every caller.
`resolve_chat` is called by BOTH `agents.runtime.jobs.plan_turn`
(at enqueue time) and `agents.runtime.loop.run_turn` (at run time). They
ask the same question and must not be able to answer it differently.

This deliberately does NOT copy `tools/rag/jobs.py`'s two-pass shape.
That module keeps `plan_ask` and `_precheck` apart because its run-time
pass genuinely does something extra -- it HEALTH-CHECKS each endpoint
and folds failures into a user-facing message (`jobs.py:151-209`). P2's
loop does no health check, so the two calls here are the same call.
`tools.rag.jobs._resolve_answer` (`jobs.py:109-130`) is the shape this
mirrors: a single helper both passes use.

`models.registry.bindings` is the ONE module of that package an
`agents/*` app may import (import-law rule 2), and this is the only
place in the column that imports it.

`principal_for(agent)` USED TO LIVE HERE -- "the principal a turn by
`agent` runs as." Identity & Auth's acting rule (spec section 5.3)
deletes it outright rather than moving it: the question itself
is wrong. A turn runs as the USER named in the payload
(`identity.contracts.principals.principal_from_payload`), never as the
agent, and a delegate inherits that SAME principal rather than one
minted from the agent it is about to run. `principal_for`'s four
callers -- `loop._run_turn`, `jobs._tool_roles`, `delegate.
run_agent_tool`, and `preflight_turn` -- all now read the acting
principal from the payload (or, for `_tool_roles`/`preflight_turn`,
take it as an argument) instead.
"""
from __future__ import annotations

from models.contracts.bindings import resolve
from models.registry.bindings import resolve_connection_named


def resolve_chat(agent, connection, *, access) -> tuple:
    """`(resolved, display_name)` for `agent`'s chat model.

    The picked connection when `connection` is a non-blank pk, else the
    agent's own `llm_role`. NEVER `get_llm()`-by-default: the picker
    override is explicit.

    `resolve_connection_named` returns a `tuple[ResolvedModel, str]`
    (`models/registry/bindings.py:317`), not a bare `ResolvedModel`. The
    name is what the assistant turn's "answered by" line records, and
    it is `""` on the role path -- the role path's own label is only
    meaningful once the turn has actually been answered, exactly as
    `tools.rag.jobs._resolve_answer` documents.

    `access` is consulted only when `connection` is not `None` -- the
    role fallback below it is the exempt path (spec section 9.5/22.32): a
    set holding the embedding connection must not break ingestion for the
    whole box.

    Raises `ValueError` for an unbound role, an unknown/non-chat pk, or a
    pk `access` does not allow. Both callers WANT that: the command
    preflights it before enqueuing, and the planner treats it as the one
    non-tolerant resolution.
    """
    if connection not in (None, ""):
        return resolve_connection_named(int(connection), "chat", access=access)
    return resolve(agent.llm_role), ""
