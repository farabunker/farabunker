"""This column's three entitlement AXES -- tools, agents and flows --
read and written from the entitlement's own side.

`agents/labels.py` answers the resource question ("which entitlements
label this tool"), which is what `/chat/tools/` and `/chat/access/` ask.
This module answers the same tables' OTHER question ("which tools carry
this entitlement"), which is what the entitlement pages ask -- and it
answers it without `identity/` importing anything here, because
`agents/apps.py::ready()` registers three dotted-path descriptors
against `identity.contracts.axes` and `identity/axes.py` resolves them.

IT LIVES AT THE COLUMN ROOT, beside `agents/labels.py`, for the reason
that module's own docstring gives: the entitlement pages are an
`identity/` surface today, and a future management command needs the
same answers without being one.

EVERY WRITE GOES THROUGH `agents/labels.py`'s EXISTING single writers.
Nothing here touches a join table directly on the write path: a second
write path would be a second place for the audit rows (`tool.labelled`
and its five siblings) to be forgotten, and `/chat/access/` reads that
trail. What this module adds is the TRANSPOSE -- one entitlement across
many resources -- computed as one `set_*_labels` call per resource that
actually changes, which is the only shape those writers accept and the
only shape that keeps their own per-resource diffing honest.
"""
from __future__ import annotations

from django.db.models import Count

from identity.contracts.axes import AxisRow

# The one qualifier the tools catalogue needs, DECLARED ONCE here rather
# than typed into the panel template: a tool no agent can be granted
# (ADR 0010 keeps every `mutates=True` spec out of `grantable_tools()`)
# can still have a page of its own, and labelling it narrows who reaches
# that page -- see `agents/chat/views/tools.py`'s own "LABELLABLE IS NOT
# GRANTABLE" note, which this is the short form of.
PAGE_ONLY_NOTE = "page only"

TOOLS_HINT = (f'A tool marked "{PAGE_ONLY_NOTE}" can be labelled but never granted to '
              f'an agent: the label narrows only the people who reach its own page.')


# --- tools -----------------------------------------------------------------

def tool_rows() -> list[AxisRow]:
    """Every registered tool, in registration order -- the SAME
    catalogue `/chat/tools/` lists, page-only ones included."""
    from agents.contracts.tools import all_tools, grantable_tools

    grantable = {spec.key for spec in grantable_tools()}
    return [
        AxisRow(spec.key, spec.label,
                note="" if spec.key in grantable else PAGE_ONLY_NOTE)
        for spec in all_tools()
    ]


def tool_ids_for(entitlement_id: int) -> set[str]:
    """The tool keys this entitlement labels. ONE query."""
    from agents.models import ToolEntitlement

    return set(ToolEntitlement.objects.filter(entitlement_id=entitlement_id)
               .values_list("tool_key", flat=True))


def tool_counts() -> dict[int, int]:
    """`{entitlement_id: labelled tools}` for EVERY entitlement, in ONE
    aggregate -- the shape that keeps the entitlements list's query
    count flat however many entitlements the box holds."""
    from agents.models import ToolEntitlement

    return _counts(ToolEntitlement.objects)


def set_tool_axis(entitlement_id: int, *, add, remove, actor, actor_user=None
                  ) -> dict[str, int]:
    """Add/remove this entitlement on the named tool keys, one
    `set_tool_labels` call per key that really changes."""
    from agents.labels import labels_for, set_tool_labels

    return _apply(
        entitlement_id, add=add, remove=remove,
        current=lambda key: set(labels_for(key)),
        write=lambda key, wanted: set_tool_labels(actor, key, wanted,
                                                  labelled_by=actor_user),
    )


# --- agents and flows ------------------------------------------------------

def agent_rows() -> list[AxisRow]:
    """Every `Agent` row, slug-ordered -- the same unfiltered catalogue
    `/chat/access/` lists, disabled and shipped rows included, for the
    reason `agents.visibility.labellable_agents` states."""
    from agents.visibility import labellable_agents

    return [AxisRow(str(agent.pk), agent.name, note=agent.slug)
            for agent in labellable_agents()]


def agent_ids_for(entitlement_id: int) -> set[str]:
    from agents.models import AgentEntitlement

    return {str(pk) for pk in AgentEntitlement.objects
            .filter(entitlement_id=entitlement_id).values_list("agent_id", flat=True)}


def agent_counts() -> dict[int, int]:
    from agents.models import AgentEntitlement

    return _counts(AgentEntitlement.objects)


def set_agent_axis(entitlement_id: int, *, add, remove, actor, actor_user=None
                   ) -> dict[str, int]:
    from agents.labels import agent_label_ids, set_agent_labels
    from agents.visibility import labellable_agent

    return _apply_by_pk(
        entitlement_id, add=add, remove=remove, lookup=labellable_agent,
        current=agent_label_ids,
        write=lambda row, wanted: set_agent_labels(actor, row, wanted,
                                                   labelled_by=actor_user),
    )


def flow_rows() -> list[AxisRow]:
    from agents.visibility import labellable_flows

    return [AxisRow(str(flow.pk), flow.name, note=flow.slug)
            for flow in labellable_flows()]


def flow_ids_for(entitlement_id: int) -> set[str]:
    from agents.models import FlowEntitlement

    return {str(pk) for pk in FlowEntitlement.objects
            .filter(entitlement_id=entitlement_id).values_list("flow_id", flat=True)}


def flow_counts() -> dict[int, int]:
    from agents.models import FlowEntitlement

    return _counts(FlowEntitlement.objects)


def set_flow_axis(entitlement_id: int, *, add, remove, actor, actor_user=None
                  ) -> dict[str, int]:
    from agents.labels import flow_label_ids, set_flow_labels
    from agents.visibility import labellable_flow

    return _apply_by_pk(
        entitlement_id, add=add, remove=remove, lookup=labellable_flow,
        current=flow_label_ids,
        write=lambda row, wanted: set_flow_labels(actor, row, wanted,
                                                  labelled_by=actor_user),
    )


# --- the three shapes all six functions above share ------------------------

def _counts(manager) -> dict[int, int]:
    """`{entitlement_id: rows}` over one whole join table, in ONE query.

    The body `tool_counts`, `agent_counts` and `flow_counts` each carried
    -- the same consolidation `agents/labels.py::_entitlement_ids_by_key`
    already made for the three readers beside them. Each keeps its own
    public function and its own queryset; what they share is the fold.
    """
    return {
        row["entitlement_id"]: row["n"]
        for row in manager.values("entitlement_id").annotate(n=Count("id"))
    }


def _apply(entitlement_id, *, add, remove, current, write) -> dict[str, int]:
    """One `write` per resource whose label set really changes.

    THE NO-OP SKIP IS NOT AN OPTIMISATION. `set_*_labels` writes the
    DIFFERENCE and audits it, so re-adding a label already present is
    already silent there -- but calling it anyway would cost a read and a
    transaction per unchanged row, and would make this function's own
    returned summary ("2 added") a count of submissions rather than of
    changes. An operator reads that line to learn what happened.

    N TRANSACTIONS, NOT ONE, and deliberately so: `write` is the
    column's own single writer, which opens its own
    `transaction.atomic()` per resource. Twenty ticked rows are twenty
    transactions, applied in the stable sorted order below, and a crash
    part-way leaves a PARTIAL change with a truthful audit trail for the
    part that landed. Wrapping the loop in one transaction here would
    mean writing past those writers, which is the one thing this module
    exists not to do -- ADR 0016's 2026-09-16 amendment records the
    trade, and `docs/EXTENDING.md` repeats it for the next axis.
    """
    added = removed = 0
    for key in sorted(add):
        held = current(key)
        if entitlement_id in held:
            continue
        write(key, held | {entitlement_id})
        added += 1
    for key in sorted(remove):
        held = current(key)
        if entitlement_id not in held:
            continue
        write(key, held - {entitlement_id})
        removed += 1
    return {"added": added, "removed": removed}


def _apply_by_pk(entitlement_id, *, add, remove, lookup, current, write
                 ) -> dict[str, int]:
    """`_apply` for an axis keyed by a primary key rather than a string.

    The row is fetched ONCE per key and then used for both the read and
    the write -- `set_agent_labels` takes the instance, not the pk. A
    `lookup` that answers `None` is skipped rather than raised on: the
    caller (`identity.axes.apply_axis`) has already refused every id
    outside the catalogue, so `None` here means the row was deleted
    between the render and the submit, which is a no-op and not an error
    an operator can act on.
    """
    def resolve(raw):
        return lookup(int(raw))

    added = removed = 0
    for raw in sorted(add):
        row = resolve(raw)
        if row is None:
            continue
        held = set(current(row))
        if entitlement_id in held:
            continue
        write(row, held | {entitlement_id})
        added += 1
    for raw in sorted(remove):
        row = resolve(raw)
        if row is None:
            continue
        held = set(current(row))
        if entitlement_id not in held:
            continue
        write(row, held - {entitlement_id})
        removed += 1
    return {"added": added, "removed": removed}
