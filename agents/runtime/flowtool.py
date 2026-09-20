"""The ONE registered flow tool, and the per-turn narrowing that gives
it real options (ruling 1, deviation P3-D11).

ONE spec, not one per flow. A flow is a ROW, and rows cannot become
registered specs: `AppConfig.ready()` may touch no database. What makes
a single generic tool a good prompt anyway is that its `flow` param is
a CHOICE whose options are filled per turn from the enabled rows -- so
the model reads an `enum` of real slugs rather than guessing at a free
string, which is strictly better than the `flow.<slug>` shape this plan
originally proposed, and works for rows nobody declared in code.

`choices=()` at registration is not a placeholder: it is the documented
shape for a choice whose options something else supplies
(`models/contracts/operations.py:45-47`), and `_input_schema` emits no
`enum` for one (`agents/contracts/toolschema.py:70-71`) -- so an
un-narrowed spec is honest about knowing nothing rather than claiming
an empty set of flows.

`required=True` on `flow` is forced, not stylistic: `validate_params`
errors on a BLANK choice whether or not it is required
(`operations.py:324-330`, the spec's own correction section 2), so an
optional flow key would be unusable rather than merely odd.

`flow_row_roles`, below, is this module's SECOND job: `flow.run`
declares `roles=()` at registration (a flow's roles live in ROWS, not
in the spec), so `agents.runtime.jobs._tool_roles` calls this at
enqueue time to supply them -- the same way a delegate's roles are
supplied by walking the agent-as-tool closure rather than declared on
`agent.<slug>`'s own spec.

IMPORT DISCIPLINE: `agents.contracts.tools` and `models.contracts.
operations` are the two pure leaves `AppConfig.ready()` already pays
for, so they are imported at MODULE SCOPE. `agents.visibility` touches
the database (`Flow.objects`), so it is imported INSIDE
`narrowed_flow_spec` and `flow_row_roles` -- never at module scope --
which is what lets `agents/apps.py::ready()` import this module while
still touching no database. Same lazy-service-import discipline every
`tools/*/tools.py` registration module takes.
"""
from __future__ import annotations

from dataclasses import replace

from agents.contracts.tools import ToolSpec
from models.contracts.operations import Param

FLOW_RUN_KEY = "flow.run"

FLOW_RUN = ToolSpec(
    key=FLOW_RUN_KEY,
    label="Run a flow",
    description=(
        "Run one of this box's saved flows: a fixed sequence of tool steps that "
        "always runs the same way, with no decisions in between. Pick the flow by "
        "name from the list you were given, and pass its inputs as an object. Use "
        "one when the shape of the work is already known; do the steps yourself "
        "when it is not."
    ),
    params=(
        Param("flow", "choice", "Flow", required=True, choices=(),
              description="Which saved flow to run, by name."),
        Param("input", "text", "Inputs", required=False,
              description=(
                  "The flow's own inputs, as a JSON object written as a string -- "
                  'for example {"topic": "attention"}. The names each flow takes '
                  "are listed with it."
              )),
    ),
    roles=(),
    runner="agents.runtime.flow.run_flow",
)


def narrowed_flow_spec(spec: ToolSpec, principal) -> ToolSpec | None:
    """`spec` with `flow`'s real options, or `None` to drop the tool.

    `None` when this principal has no enabled flow: offering a tool
    whose only argument has no legal value is worse than not offering
    it -- the model will call it, fail validation, and burn the turn's
    one recovery on a tool that could never have worked.

    The description gains a line PER FLOW (name, and its input keys),
    because the `enum` alone tells the model which slugs exist and
    nothing about what they take. That is the whole prompt surface of a
    flow, so it is built from the rows rather than from anything static.

    `dataclasses.replace`, never a mutation: `ToolSpec` is frozen and
    the registered constant is shared by every turn on the box.
    """
    from agents.visibility import visible_flows

    flows = list(visible_flows(principal))
    if not flows:
        return None
    slugs = tuple(f.slug for f in flows)
    lines = "\n".join(
        f"- {f.slug}: {f.description or f.name} "
        f"(inputs: {', '.join(i.get('key', '') for i in (f.inputs or [])) or 'none'})"
        for f in flows
    )
    params = tuple(
        replace(p, choices=slugs) if p.key == "flow" else p for p in spec.params
    )
    return replace(spec, params=params,
                   description=f"{spec.description}\n\nAvailable flows:\n{lines}")


def flow_row_roles(principal) -> set[str]:
    """Every role reachable from a step of a flow this `principal` may
    run, unioned across every visible, enabled `Flow` row.

    `flow.run` declares `roles=()` at registration -- a flow's roles
    are its STEPS' roles, and they live in ROWS, so `agents.runtime.
    jobs._tool_roles` calls this at enqueue time to supply them, exactly
    as a delegate's own roles are supplied by walking the agent-as-tool
    closure rather than declared on `agent.<slug>`'s spec.

    A step naming a tool not registered on this install is SKIPPED, not
    a fault (ruling R1's tolerance): a vision step on a box with the
    vision flag off is a not-here, and `plan_turn` already tolerates the
    identical case for a granted tool's own role.
    """
    from agents.contracts.tools import get_tool
    from agents.visibility import visible_flows

    roles: set[str] = set()
    for flow in visible_flows(principal):
        for step in flow.steps or ():
            tool_key = step.get("tool") if isinstance(step, dict) else None
            if not tool_key:
                continue
            try:
                spec = get_tool(tool_key)
            except ValueError:
                continue
            roles.update(spec.roles)
    return roles
