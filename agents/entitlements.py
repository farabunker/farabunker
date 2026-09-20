"""The Django-side builder for the pure `ToolAccess` value.

An `agents` module, so it may import `identity.access` (import-law rule
2's named seam) and its own `ToolEntitlement` -- neither of which
`agents/contracts/tools.py` may do, because that file is a rule-1 pure
leaf every column imports.

IT RUNS ONCE PER TURN, not once per tool: one query over
`ToolEntitlement` (a small table) and one over the principal's grants.
`agents/runtime/loop.py::_run_turn` and `::jobs.plan_turn` each build one
and thread it down; `delegate.py` reuses the root's rather than building
a second.
"""
from __future__ import annotations

from agents.contracts.tools import UNRESTRICTED_TOOL_ACCESS, ToolAccess
from agents.labels import tool_entitlement_ids
from identity.access import held_entitlement_ids, sees_all_content
from identity.access import settings_row as _identity_settings_row


def tool_access_for(principal, *, settings_row=None,
                    wall: frozenset[int] = frozenset()) -> ToolAccess:
    """What `principal` may call, as plain data.

    THE OPEN BRANCH IS FIRST: `sees_all_content` tests `accounts_on()`
    before it reads a second table, so an open box builds this without
    touching `ToolEntitlement` or any grant -- the structural half of
    "an open box never runs a permission query".

    ONE `IdentitySettings` READ, not two: when the caller leaves
    `settings_row` out, `identity.access.settings_row()` is fetched once
    HERE and threaded into both `sees_all_content` and
    `held_entitlement_ids` via their own `settings_row=` keyword -- the
    same per-call reuse norm `sees_all_content`'s own docstring states,
    applied across two calls instead of inside one.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only -- the same shape `models.registry.access.
    model_access_for` and `tools.rag.access.document_visibility` take.
    A caller building more than one access axis for the same turn
    (`agents.runtime.preflight.preflight_turn`,
    `agents.runtime.loop._run_turn`) fetches the row ONCE and passes it
    to every axis, so a turn pays one singleton read, not one per axis.

    `sees_all_content`, NOT `is_admin`: calling somebody's labelled tool
    is reading, not administering, so an administrator with the content
    setting off is filtered exactly like a member.

    `wall` is the WORKSTREAM SCOPE of the turn this access is being built
    for -- empty for a loose turn and for every non-stream caller, so
    every existing call site keeps its exact meaning. It narrows the
    HELD half by INTERSECTION and never by union: a wall can only remove
    entitlements from what the acting user already holds, and is never a
    grant (spec §6.1).

    `and not wall` IS THE WHOLE OF THE CHANGE TO THE UNRESTRICTED BRANCH,
    and it is the one subtle line here (author decision 8). Without it,
    an administrator with `admin_sees_content` on would get
    `UNRESTRICTED_TOOL_ACCESS` and the wall would silently do nothing for
    tools while doing something for documents. A wall is not a permission
    check on somebody else; it is a scope somebody CHOSE, and choosing it
    means choosing it for yourself as well. The alternative -- a wall
    that binds documents but not tools for privileged readers -- would
    make the wall's meaning depend on who is looking, which is exactly
    what a scope must not do.

    BOUNDED BY RULING A: on an open box `wall_for` returns the empty set
    without reading the wall table, so this takes the `sees_all_content
    and not wall` branch and runs zero permission queries, exactly as it
    did before this phase.
    """
    row = settings_row if settings_row is not None else _identity_settings_row()
    if sees_all_content(principal, settings_row=row) and not wall:
        return UNRESTRICTED_TOOL_ACCESS
    held = held_entitlement_ids(principal, settings_row=row)
    return ToolAccess(
        required=tool_entitlement_ids(),
        held=(held & wall) if wall else held,
        unrestricted=False,
    )


def wall_for(conversation) -> frozenset[int]:
    """This conversation's stream wall, or the empty set.

    HERE, BESIDE `tool_access_for` (author decision 9), because this
    module already owns "the one place a turn's access axes are built"
    and because the ruling-A posture branch must live in a reader of one
    table, in the runtime -- NOT in a `visible_*` function, whose bodies
    stay posture-free exactly as IA-2 left them (spec §13).

    NOT `_wall_for` (E8): it has four external importers --
    `agents.runtime.loop`, `agents.runtime.jobs`, `agents.runtime.
    preflight`, `agents.chat.views.thread` -- and the house rule
    `agents/workstreams.py` states for its own `wall_ids` applies here
    too: a name built for outside callers is not private, whatever its
    original spelling.

    ONE READ ON THE ADMITTED PATH (E5 correction to a docstring that used
    to claim "one indexed read per stream turn" unconditionally): that
    stopped being true once `agents.runtime.loop`'s turn runner learned
    to read `WorkstreamScope.wall` off the `workstream_scope` call it
    already makes for the SAME turn, rather than asking this function
    too -- on that path (a stream turn this principal is admitted to)
    THIS FUNCTION IS NEVER CALLED. CONSOLIDATION WAVE (audit A F10)
    TAUGHT `agents.chat.views.thread` THE IDENTICAL TRICK: it now reads
    `stream_scope.wall` off the `WorkstreamScope` it already resolved
    for the SAME render, and calls this function only on the fallback
    leg (`stream_scope is None`) -- so it moves from "no scope in hand"
    to the SAME bucket as `agents.runtime.loop`, below. It is still the
    one read for the two call sites of §6.3 that genuinely have no
    `WorkstreamScope` of their own in hand at all (`agents.runtime.
    preflight`, `agents.runtime.jobs`), and for BOTH `agents.runtime.
    loop` and `agents.chat.views.thread` on the one anomalous path where
    `workstream_scope` returned `None` (not admitted, or no stream at
    all) but the wall must still bind (spec §17.3). Zero queries for a
    loose turn, and zero on an open box, where `agents.workstreams.
    wall_ids` short-circuits on `accounts_on()` before touching the
    table (ruling A) either way.
    """
    if conversation.workstream_id is None:
        return frozenset()
    from agents.workstreams import wall_ids

    return wall_ids(conversation.workstream_id)
