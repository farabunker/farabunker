"""`/chat/access/` -- which entitlement an agent or a flow needs (spec
sections 6.11, 9.6).

ONE PAGE, TWO SECTIONS. An agent and a flow are two runnable row kinds
in one column, read by two functions (`agents.labels.agent_entitlement_ids`/
`flow_entitlement_ids`) that already differ only in which model they
filter, so a second page would be one rule rendered twice.

ONE QUERY PER SECTION FOR THE LISTING, the same discipline
`agents/chat/views/tools.py::tool_entitlements` documents for its own
`tool_entitlement_ids()` call: `agent_entitlement_ids()`/
`flow_entitlement_ids()` each read every row's labels in a single
`values_list`, so rendering N agents costs one query, not N. The POST
handler mirrors this on the write side with `agents.visibility.
labellable_agent`/`labellable_flow` -- a one-row `.filter(pk=...).first()`
lookup, not a full-table scan over `labellable_agents()`/
`labellable_flows()` for a single match.

IT LIVES HERE for the same reason `agents/chat/views/tools.py` does:
`identity/` may not import `agents/` (import-law rule 4) and `/chat/`
is this column's only URL mount.

CLASS S, and NO `@require_admin` HERE for the identical reason
`tools.py` gives: the route is classified `"S"` in `identity/routes.py`,
and `IdentityGateMiddleware` already 403s a non-admin before this view
ever runs -- the decorator would be a second, redundant belt, and
`identity.gate` is not one of the four seams `agents/` may import
(import-law rule 2).

THE LISTING GOES THROUGH `agents.visibility.labellable_agents`/
`labellable_flows`, never `Agent.objects`/`Flow.objects` directly --
the column-boundary rule `foundation/ops/tests/test_column_boundaries.
py` enforces on every module under `agents/chat/`. Those two functions
are UNFILTERED by principal (unlike `visible_agents`/`visible_flows`):
this route's own CLASS S already means every caller here is an
administrator, so asking `visible_agents(principal)` would wrongly hide
a MEMBER's own unlabelled agent from the very page that exists to label
it, whenever `admin_sees_content` is off.
"""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from agents.chat.service import (
    NO_ENTITLEMENTS_COPY, entitlement_change_flash, entitlement_panes,
    entitlement_row_url, entitlement_summary, parse_entitlement_diff,
)
from agents.labels import (
    agent_entitlement_ids, agent_label_ids, flow_entitlement_ids, flow_label_ids,
    set_agent_labels, set_flow_labels,
)
from agents.visibility import (
    labellable_agent, labellable_agents, labellable_flow, labellable_flows,
)
from foundation.settings_area import preserve_assistant_flag, settings_redirect
from identity.access import labelling_entitlements
from identity.request import principal_for_request, settings_row_for, user_for_request

_KIND_LOOKUPS = {
    "agent": labellable_agent,
    "flow": labellable_flow,
}


# R7 (audit 2, S22): the method vocabulary is DECLARED, not implied by
# a branch in the body. GET renders, POST mutates, and anything else is
# a 405 from Django before this function runs -- so the `request.method`
# test below chooses between methods this view really serves rather than
# silently treating a PUT or a DELETE as a read.
#
# HEAD IS LISTED EXPLICITLY (audit-2 confirm, observation 7). Django
# serves HEAD by running the GET path and dropping the body, which is
# what this view did before it declared anything; `require_http_methods`
# admits only what it is given, so omitting HEAD would have turned a
# previously-working request into a 405 for anything that HEADs a chat
# URL. Declaring the vocabulary was the point -- narrowing it was not.
@require_http_methods(["GET", "HEAD", "POST"])
def agent_entitlements(request):
    """GET renders every agent and flow, with its current labels; POST
    sets one row's labels to exactly what was submitted.

    ONE ROW PER POST, with `kind`/`pk` fields, the same shape
    `tool_entitlements` uses for its own `tool_key` -- a page-wide submit
    would make an operator's single change indistinguishable from an
    accidental wipe of every other row.
    """
    row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=row)
    if request.method == "POST":
        return _save(request, principal, row)

    choices = labelling_entitlements(principal, settings_row=row)
    # ONE QUERY per section for every row's labels -- see the module
    # docstring's own note -- rather than one query per row.
    agent_labels = agent_entitlement_ids()
    flow_labels = flow_entitlement_ids()
    # COLLAPSED SUMMARY ROWS SINCE PHASE 2, not a checkbox per
    # entitlement per row: the old shape rendered (agents + flows) x
    # entitlements checkboxes in one page, which is unreadable well
    # before fifty entitlements. The summary states the count and the
    # leading names; the transfer panel inside each `<details>` is where
    # the editing happens, and it is the SAME fragment the entitlement
    # page renders in the other direction.
    #
    # STILL NO PER-ROW QUERY: the two batch readers above and `choices`
    # are one query each for the whole page, and `_row` is a fold over
    # them.
    opened = request.GET.get("open", "")

    def _row(kind, obj, labels):
        held = labels.get(obj.pk, frozenset())
        available, active = entitlement_panes(choices, held)
        anchor = f"{kind}-{obj.pk}"
        return {
            "kind": kind, "pk": obj.pk, "slug": obj.slug, "name": obj.name,
            "held": held,
            "anchor": anchor,
            # See `agents/chat/views/tools.py` for why the panel carries
            # an id of its own rather than sharing the row's.
            "panel_anchor": f"panel-{anchor}",
            "fields": {"kind": kind, "pk": obj.pk},
            "summary": entitlement_summary(len(held), [row["name"] for row in active]),
            "available": available,
            "active": active,
            "available_count": len(available),
            "active_count": len(active),
            "total": len(choices),
            # RE-OPENED BY THE URL, never by a script or a cookie --
            # `agents/chat/service.py::entitlement_row_url` says why.
            "open": opened == anchor,
        }

    agent_rows = [_row("agent", agent, agent_labels) for agent in labellable_agents()]
    flow_rows = [_row("flow", flow, flow_labels) for flow in labellable_flows()]
    return render(request, "chat/agent_entitlements.html", {
        "agent_rows": agent_rows,
        "flow_rows": flow_rows,
        "choices": choices,
        # See `agents/chat/views/tools.py` for why both of these are
        # built in the view rather than in the template.
        "save_action": preserve_assistant_flag(
            request, reverse("chat-agent-entitlements")),
        "no_entitlements": NO_ENTITLEMENTS_COPY,
    })


def _save(request, principal, settings_row):
    kind = request.POST.get("kind", "")
    raw_pk = request.POST.get("pk", "")
    reader = _KIND_LOOKUPS.get(kind)
    if reader is None or not raw_pk.isdecimal():
        # F7 (Coherence Wave B): flash-and-redirect, not a raw 400 --
        # the house convention every other mutation on this platform
        # uses (`identity/views.py:317-321`, `agents/chat/views/
        # settings.py:74-79`), never a bare plain-text page with no
        # shell, no sidebar, no flash. Still not a silent no-op: the
        # flash message says exactly what the old 400 body said.
        messages.error(request, f"{kind!r} is not a labellable kind.")
        return settings_redirect(request, "chat-agent-entitlements")
    pk = int(raw_pk)
    target = reader(pk)
    if target is None:
        messages.error(request, f"No {kind} {pk} on this install.")
        return settings_redirect(request, "chat-agent-entitlements")

    # BUILT BEFORE THE PARSE, and used by both outcomes: a refusal lands
    # the operator back on the row they were editing, exactly as a
    # success does.
    back = entitlement_row_url(request, "chat-agent-entitlements",
                               row_anchor=f"{kind}-{pk}")
    # CONSOLIDATION WAVE (audit B, S15): the parse/validate half of this
    # POST -- the submitted ids, checked against `labelling_entitlements`
    # -- is the SAME twelve lines `agents.chat.views.tools._save` has;
    # both call the one shared helper rather than each keeping a copy.
    # Since phase 2 it parses the transfer panel's `op` + `add`/`remove`
    # rather than a whole `entitlements` set.
    parsed = parse_entitlement_diff(request, principal, settings_row,
                                    redirect_url=back)
    if not isinstance(parsed, tuple):
        return parsed
    operation, submitted = parsed

    # `user_for_request(request)`, NOT a bare `request.user` read here:
    # `agents/` is one of the three columns `test_no_view_outside_
    # identity_reads_request_user` scans. `identity.request.
    # user_for_request` is the one place that attribute is read, and it
    # answers the real `User` instance `AgentEntitlement.labelled_by`/
    # `FlowEntitlement.labelled_by` need -- not the `Principal` value
    # object used for the access checks above.
    labelled_by = user_for_request(request)
    # THE NEW SET IS DERIVED FROM WHAT IS THERE NOW, not from what the
    # form showed -- see `agents/chat/views/tools.py::_save` for the
    # same three lines and the reason. The single writers still take the
    # whole wanted set: they diff internally and write the audit rows.
    read_current, write = ((agent_label_ids, set_agent_labels) if kind == "agent"
                           else (flow_label_ids, set_flow_labels))
    before = set(read_current(target))
    wanted = before | submitted if operation == "add" else before - submitted
    write(principal, target, wanted, labelled_by=labelled_by)
    messages.info(request, entitlement_change_flash(target.slug, before, wanted))
    return redirect(back)
