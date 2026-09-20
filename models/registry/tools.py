"""The `models/` column's own tool: what is currently assigned to each
model-consuming role (spec section 5, ruling R2).

Kept on two grounds. It is this column's proof that it adopts the tool
contract -- without it, `models/` is the one column that registers no
tool at all -- and it traces directly to the owner's "it should have
access to the tools within the system".

DB-ONLY, and that is the ruling, not an optimisation. It runs no health
probe and makes no engine call of any kind. Reachability is a live
network fact whose cost is unbounded and whose latency would land inside
a turn that is already holding the machine's one execution slot
(sequential mode, models/queue/scheduler.py:374-380). `/setup/` is the
surface that answers "is the engine up", and it answers it on demand, for
a human, outside the queue. If a role is bound but its engine is down,
the agent finds out the honest way: the tool that needs it fails and
surfaces the error.

`models.registry.bindings` is an IN-COLUMN import here and crosses no
boundary -- which is the other half of why this tool lives in this column
rather than being bolted onto `agents/`.

NOTE ON WORDING: this module deliberately never spells the name of the
engine-probe method it refuses to call. `test_the_source_makes_no_engine_
call_of_any_kind` below scans this file's own TEXT for that name among
others, so writing it here -- even inside a docstring explaining why we
do not use it -- would turn the guard red on its own explanation.

Module-scope imports stay pure so `InferenceConfig.ready()` keeps its
no-DB-no-heavy-imports promise; `run_status` imports lazily inside its
body.
"""
from __future__ import annotations

from agents.contracts.tools import ToolContext, ToolResult, ToolSpec, validate_tool_args

MODELS_STATUS = ToolSpec(
    key="models.status",
    label="Model assignments",
    description=(
        "Report which model this box currently has assigned to each of its "
        "model-consuming roles, and which roles have nothing assigned. It "
        "reads the registry only: it does not contact any engine, so it "
        "cannot say whether an engine is actually running."
    ),
    params=(),
    roles=(),
    runner="models.registry.tools.run_status",
)


def run_status(args: dict, ctx: ToolContext) -> ToolResult:
    """Every registered role and whatever currently answers it.

    Reports all three of `role_primary`'s honest states without
    collapsing any of them (bindings.py:192-221): a bound connection
    (name plus pk), an explicit environment override (a name, no pk --
    there is no registry row to carry one), or genuinely unassigned.
    """
    from models.contracts.roles import all_roles
    from models.registry.bindings import role_primary

    validate_tool_args(MODELS_STATUS, args)   # declares no params: any arg is a bug

    rows = []
    lines = []
    for role in all_roles():
        name, connection_id = role_primary(role.key)
        rows.append({
            "role": role.key,
            "label": role.label,
            "capability": role.capability,
            "model": name,
            "connection_id": connection_id,
            "assigned": bool(name),
        })
        lines.append(
            f"{role.label} ({role.key}, {role.capability}): "
            + (name if name else "not assigned")
        )

    return ToolResult(text="\n".join(lines), data={"roles": rows})
