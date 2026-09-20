"""Tool labels: reading them for `tool_access_for`, writing them from
the tool-label page, and giving them up when an entitlement dies.

IT LIVES AT THE COLUMN ROOT, not inside `agents/chat`, for the same
reason `agents/visibility.py` does: the tool-label page is a chat view
today, and a future MCP edge and a future management command need the
same answers without either of them being one.
"""
from __future__ import annotations

from django.db import transaction

from agents.models import ToolEntitlement
from identity import audit
from identity.contracts import actions


def _entitlement_ids_by_key(queryset, key_field: str) -> dict:
    """{`key_field` value: frozenset of entitlement ids} in ONE query.

    The body `tool_entitlement_ids`, `agent_entitlement_ids` and
    `flow_entitlement_ids` each carried (C-28). Each of the three keeps
    its own public function, its own docstring and its own queryset --
    what they share is the fold, not the question.
    """
    grouped: dict = {}
    for key, entitlement_id in queryset.values_list(key_field, "entitlement_id"):
        grouped.setdefault(key, set()).add(entitlement_id)
    return {key: frozenset(ids) for key, ids in grouped.items()}


def _label_ids(queryset) -> frozenset[int]:
    """The entitlement ids in one already-filtered queryset's
    `entitlement_id` column.

    The single-row shape `labels_for`, `agent_label_ids` and
    `flow_label_ids` each carried (C-28): one queryset filtered to a
    single key/agent/flow, folded to a frozenset of entitlement ids.
    """
    return frozenset(queryset.values_list("entitlement_id", flat=True))


def _set_labels(*, read_current, wanted, add, remove, audit_row, labelled, unlabelled):
    """Apply the DIFFERENCE between the CURRENT labels and `wanted`,
    auditing each direction, inside ONE transaction (C-28).

    `read_current` is a zero-argument callable returning the current
    label set -- it is called as the FIRST statement inside the
    `transaction.atomic()` block below, not before it, so the read and
    the writes it diffs against share one transaction (as all three
    writers did before Task 18 folded them together; a read taken
    outside the block could race a concurrent writer between the read
    and the `atomic()` entry).

    THE DIFF IS THE POINT, not an optimisation: `/chat/access/` reads this
    audit trail, and a trail that recorded "labelled" for a label already
    present is a trail whose interesting lines are invisible. Every one of
    the three writers said so in its own docstring; they now say it once.

    `add`/`remove` are the callers' own row writers (the join model
    differs per kind). `audit_row(entitlement_id, action)` is the
    caller's own audit vocabulary -- `target_type`, `target_key` and
    `target_label` are spelled differently per kind and are what an
    operator reads, so they are supplied, never derived here. `labelled`/
    `unlabelled` are the caller's own action constants (`TOOL_LABELLED`/
    `TOOL_UNLABELLED` for the tool writer, and so on) -- the three
    writers do not share a vocabulary either.
    """
    with transaction.atomic():
        current = read_current()
        for entitlement_id in sorted(wanted - current):
            add(entitlement_id)
            audit_row(entitlement_id, labelled)
        for entitlement_id in sorted(current - wanted):
            remove(entitlement_id)
            audit_row(entitlement_id, unlabelled)


def tool_entitlement_ids() -> dict[str, frozenset[int]]:
    """`{tool_key: {entitlement ids}}` for every LABELLED key. ONE QUERY.

    A key ABSENT from this mapping is unlabelled, and therefore callable
    by everybody signed in -- which is why this returns only the labelled
    ones and `ToolAccess.allows` reads an absent key as permitted.
    """
    return _entitlement_ids_by_key(ToolEntitlement.objects.all(), "tool_key")


def labels_for(tool_key: str) -> frozenset[int]:
    """The entitlement ids labelling one key."""
    return _label_ids(ToolEntitlement.objects.filter(tool_key=tool_key))


def set_tool_labels(actor, tool_key: str, entitlement_ids, *, labelled_by=None) -> None:
    """Make `tool_key`'s labels exactly `entitlement_ids`.

    WRITES THE DIFFERENCE, not the whole set: an audit trail that
    recorded "labelled" for a label that was already there is a trail
    whose interesting lines are invisible.

    THE CALLER CHECKS THE PREDICATE. `agents.chat.views.tools` refuses
    unless every entitlement being ADDED is one `labelling_entitlements`
    offered the actor -- the same predicate `identity.services.
    may_administer_entitlement` spells out on the identity side. It does
    NOT separately check entitlements being REMOVED, because the route is
    Class S: every caller who reaches it is already an administrator, for
    whom `labelling_entitlements` offers everything, so the removal case
    can never fail the check the addition case already passed. A future
    class loosening (an owner reaching this without being an
    administrator) would need to add that removal check explicitly
    rather than inherit this one's coverage. This function is the write,
    not the guard, exactly as `identity.audit.record` is the writer and
    not the policy.

    `labelled_by` is an `identity` `User` INSTANCE, or `None` --
    this module never imports `identity.models` to get one; the caller
    passes it and this function only assigns it to the FK. Provenance
    also lives in the audit rows written below; this column fills only
    when the caller holds a real `User` (the label page will; a shell
    or the entitlement-delete cascade does not).
    """
    wanted = {int(i) for i in entitlement_ids}

    def add(entitlement_id):
        ToolEntitlement.objects.create(tool_key=tool_key, entitlement_id=entitlement_id,
                                       labelled_by=labelled_by)

    def remove(entitlement_id):
        ToolEntitlement.objects.filter(tool_key=tool_key,
                                       entitlement_id=entitlement_id).delete()

    def audit_row(entitlement_id, action):
        audit.record(actor, action, target_type="tool", target_key=tool_key,
                     target_label=tool_key, entitlement_id=entitlement_id)

    _set_labels(read_current=lambda: set(labels_for(tool_key)), wanted=wanted, add=add,
                remove=remove, audit_row=audit_row, labelled=actions.TOOL_LABELLED,
                unlabelled=actions.TOOL_UNLABELLED)


def agent_entitlement_ids() -> dict[int, frozenset[int]]:
    """`{agent_id: {entitlement ids}}` for every LABELLED agent. ONE QUERY --
    the same shape `tool_entitlement_ids` uses, for the identical reason:
    `/chat/access/` renders one row per agent, and asking `agent_label_ids`
    once per row would be an N+1 the tool-label page's own ONE-QUERY note
    already warns against.

    An agent ABSENT from this mapping is unlabelled.
    """
    from agents.models import AgentEntitlement

    return _entitlement_ids_by_key(AgentEntitlement.objects.all(), "agent_id")


def flow_entitlement_ids() -> dict[int, frozenset[int]]:
    """The same, for flows."""
    from agents.models import FlowEntitlement

    return _entitlement_ids_by_key(FlowEntitlement.objects.all(), "flow_id")


def agent_label_ids(agent) -> frozenset[int]:
    """The entitlement ids labelling one agent.

    A SINGLE-ROW READER, for the write path (`set_agent_labels` diffs
    against exactly this one agent's current labels) -- `/chat/access/`'s
    own listing uses `agent_entitlement_ids()` instead, in one query for
    every row rather than one query per row.
    """
    from agents.models import AgentEntitlement

    return _label_ids(AgentEntitlement.objects.filter(agent=agent))


def flow_label_ids(flow) -> frozenset[int]:
    """The entitlement ids labelling one flow. Same single-row shape as
    `agent_label_ids` -- see its docstring."""
    from agents.models import FlowEntitlement

    return _label_ids(FlowEntitlement.objects.filter(flow=flow))


def set_agent_labels(actor, agent, entitlement_ids, *, labelled_by=None) -> None:
    """Make `agent`'s labels exactly `entitlement_ids`.

    Same shape as `set_tool_labels` -- writes the difference, audits both
    directions -- for the identical reason: an audit trail that recorded
    "labelled" for a label already there is a trail whose interesting
    lines are invisible.
    """
    from agents.models import AgentEntitlement

    wanted = {int(i) for i in entitlement_ids}

    def add(entitlement_id):
        AgentEntitlement.objects.create(agent=agent, entitlement_id=entitlement_id,
                                        labelled_by=labelled_by)

    def remove(entitlement_id):
        AgentEntitlement.objects.filter(agent=agent,
                                        entitlement_id=entitlement_id).delete()

    def audit_row(entitlement_id, action):
        audit.record(actor, action, target_type="agent", target_key=str(agent.pk),
                     target_label=agent.slug, entitlement_id=entitlement_id)

    _set_labels(read_current=lambda: set(agent_label_ids(agent)), wanted=wanted, add=add,
                remove=remove, audit_row=audit_row, labelled=actions.AGENT_LABELLED,
                unlabelled=actions.AGENT_UNLABELLED)


def set_flow_labels(actor, flow, entitlement_ids, *, labelled_by=None) -> None:
    """The same, for a flow."""
    from agents.models import FlowEntitlement

    wanted = {int(i) for i in entitlement_ids}

    def add(entitlement_id):
        FlowEntitlement.objects.create(flow=flow, entitlement_id=entitlement_id,
                                       labelled_by=labelled_by)

    def remove(entitlement_id):
        FlowEntitlement.objects.filter(flow=flow,
                                       entitlement_id=entitlement_id).delete()

    def audit_row(entitlement_id, action):
        audit.record(actor, action, target_type="flow", target_key=str(flow.pk),
                     target_label=flow.slug, entitlement_id=entitlement_id)

    _set_labels(read_current=lambda: set(flow_label_ids(flow)), wanted=wanted, add=add,
                remove=remove, audit_row=audit_row, labelled=actions.FLOW_LABELLED,
                unlabelled=actions.FLOW_UNLABELLED)


def agent_flow_label_cascade(entitlement_id: int, *, commit: bool) -> int:
    """This column's SECOND answer to "this entitlement is going away" --
    agent and flow labels, counted and removed together.

    ONE cascade for two tables rather than two registrations, because the
    delete confirmation reads better as "Agent and flow labels: 3" than as
    two lines an operator has to add up, and because the two tables are
    always edited from the same page.
    """
    from agents.models import AgentEntitlement, FlowEntitlement

    agent_rows = AgentEntitlement.objects.filter(entitlement_id=entitlement_id)
    flow_rows = FlowEntitlement.objects.filter(entitlement_id=entitlement_id)
    count = agent_rows.count() + flow_rows.count()
    if commit:
        agent_rows.delete()
        flow_rows.delete()
    return count


def tool_labels_cascade(entitlement_id: int, *, commit: bool) -> int:
    """This column's answer to "this entitlement is going away".

    Registered from `agents/apps.py::ready()` as a DOTTED-PATH STRING, so
    `identity/` reaches it without importing `agents/` (import-law rule
    4). `commit=False` counts; `commit=True` removes. There is no cache
    to repair on this side -- a tool label is read live, per turn, by
    `agents.entitlements.tool_access_for` -- which is exactly why the
    document side of the same registry does more than this one.
    """
    rows = ToolEntitlement.objects.filter(entitlement_id=entitlement_id)
    count = rows.count()
    if commit:
        rows.delete()
    return count
