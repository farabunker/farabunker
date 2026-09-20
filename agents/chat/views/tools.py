"""`/chat/tools/` -- which entitlement each registered tool needs.

IT LIVES HERE because `identity/` may not import `agents/` (import-law
rule 4) and `/chat/` is this column's only URL mount. A label belongs
beside the thing it protects, which is the same reason the document-label
page lives in `tools/rag`.

CLASS S. A tool label is library-wide operator policy. Unlike a document
label it has no entitlement owner: spec section 7.4 names an owner's
three capabilities and all three are about grants and documents.

NO `@require_admin` HERE. The route is classified `"S"` in
`identity/routes.py`, and `IdentityGateMiddleware` already 403s a
non-admin before this view ever runs -- the decorator would be a second,
redundant belt for a route the mechanism (not the belt) already covers,
and `identity.gate` is not one of the four seams `agents/` may import
(import-law rule 2: `identity.contracts`, `identity.access`,
`identity.request`, `identity.audit`).
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
from agents.contracts.tools import all_tools, grantable_tools
from agents.labels import labels_for, set_tool_labels, tool_entitlement_ids
from agents.visibility import resident_agent_tool_keys
from foundation.settings_area import preserve_assistant_flag, settings_redirect
from identity.access import labelling_entitlements
from identity.request import principal_for_request, settings_row_for, user_for_request


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
def tool_entitlements(request):
    """GET renders every registered tool, mutating ones marked page-only,
    with its current labels; POST sets one tool's labels to exactly what
    was submitted.

    ONE TOOL PER POST, with a `tool_key` field, rather than one giant
    form for the whole page: a page-wide submit would make an operator's
    single change indistinguishable from an accidental wipe of every
    other row, and a partial render (a feature-gated tool absent from
    THIS install) would silently unlabel it.

    ONE `IdentitySettings` READ for the whole request, via
    `settings_row_for`, threaded through `principal_for_request` and
    `labelling_entitlements` -- the same per-request-reuse norm every
    other `settings_row`-accepting function in `identity/access.py`
    documents, not a singleton read per call.
    """
    row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=row)
    if request.method == "POST":
        return _save(request, principal, row)

    # ONE QUERY for every key's labels (`tool_entitlement_ids`), not one
    # per grantable tool: `agents/labels.py` grew that function in Task 4
    # for exactly this shape, and `labels_for` is the single-key reader
    # the label WRITE path uses.
    labels = tool_entitlement_ids()
    shell = resident_agent_tool_keys()
    choices = labelling_entitlements(principal, settings_row=row)
    # LABELLABLE IS NOT GRANTABLE, and the page says which is which.
    # `grantable_tools()` answers "what an Agent row may name in
    # `tool_keys`" -- ADR 0010 keeps every `mutates=True` spec out of it,
    # and `granted_tools` drops one regardless of any label. But a
    # mutating tool can still have a PAGE of its own (`rag.ingest` is the
    # library's upload door), and a label there narrows which people
    # reach that page. Listing only grantable tools would leave that door
    # unlabellable -- spec section 22.35.
    grantable = {spec.key for spec in grantable_tools()}
    # THE ROW THIS PAGE RENDERS SINCE PHASE 2 IS A COLLAPSED SUMMARY,
    # not a checkbox per entitlement. The old shape put one checkbox on
    # every row for every entitlement in the box -- tools x entitlements
    # of them in one render -- which is unreadable at fifty entitlements
    # and was the page's own half of the two-projections problem. The
    # summary states the count and the leading names; the transfer panel
    # inside the `<details>` is where the editing happens, and it is the
    # SAME fragment the entitlement page renders in the other direction.
    #
    # STILL NO PER-ROW QUERY: `labels` and `choices` above are one query
    # each for the whole page, and everything below is a fold over them.
    opened = request.GET.get("open", "")
    rows = []
    for spec in all_tools():
        held = labels.get(spec.key, frozenset())
        available, active = entitlement_panes(choices, held)
        anchor = f"tool-{spec.key}"
        rows.append({
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "held": held,
            "shell_agents": shell.get(spec.key, []),
            "page_only": spec.key not in grantable,
            "anchor": anchor,
            # THE PANEL'S OWN ID, DISTINCT FROM THE ROW'S. Both the
            # `section.row` card and the panel inside it need an id, and
            # two elements cannot share one: `#tool-rag.search` would be
            # ambiguous, and a save anchors at the ROW so its heading is
            # on screen rather than at the panel buried under it.
            "panel_anchor": f"panel-{anchor}",
            "fields": {"tool_key": spec.key},
            "summary": entitlement_summary(len(held), [row["name"] for row in active]),
            "available": available,
            "active": active,
            "available_count": len(available),
            "active_count": len(active),
            "total": len(choices),
            # RE-OPENED BY THE URL, never by a script or a cookie: a
            # `<details>` closes on every reload, so a save that landed
            # on this row's anchor with the row shut would be half a
            # return. `entitlement_row_url` writes the parameter.
            "open": opened == anchor,
        })
    return render(request, "chat/tool_entitlements.html", {
        "rows": rows,
        "choices": choices,
        # THE FORM ACTION, BUILT HERE because only Python can call
        # `preserve_assistant_flag` -- every settings form must carry
        # `?assistant=1` when the panel is open and nothing when it is
        # shut, and `identity/tests/test_route_matrix.py` sweeps both
        # halves. One value for every row's form: they all post here.
        "save_action": preserve_assistant_flag(
            request, reverse("chat-tool-entitlements")),
        "no_entitlements": NO_ENTITLEMENTS_COPY,
    })


def _save(request, principal, settings_row):
    key = request.POST.get("tool_key", "")
    if key not in {spec.key for spec in all_tools()}:
        # F7 (Coherence Wave B): flash-and-redirect, not a raw 400 --
        # the house convention every other mutation on this platform
        # uses (`identity/views.py:317-321`, `agents/chat/views/
        # settings.py:74-79`), never a bare plain-text page with no
        # shell, no sidebar, no flash. Still not a silent no-op: the
        # flash message says exactly what the old 400 body said, so an
        # operator who tampered with the form still sees why nothing
        # was saved -- it just does not dump them out of the chrome to
        # read it.
        #
        # THE PAGE TOP, not a row anchor: there is no row to return to,
        # and a fragment assembled from whatever was posted would point
        # at an id this page never emits.
        messages.error(request, f"{key!r} is not a tool on this install.")
        return settings_redirect(request, "chat-tool-entitlements")
    # BUILT BEFORE THE PARSE, and used by both outcomes: a refusal lands
    # the operator back on the row they were editing, exactly as a
    # success does.
    back = entitlement_row_url(request, "chat-tool-entitlements",
                               row_anchor=f"tool-{key}")
    # CONSOLIDATION WAVE (audit B, S15): the parse/validate half of this
    # POST -- the submitted ids, checked against `labelling_entitlements`
    # -- is the SAME twelve lines `agents.chat.views.access._save` has;
    # both call the one shared helper rather than each keeping a copy.
    # Since phase 2 it parses the transfer panel's `op` + `add`/`remove`
    # rather than a whole `entitlements` set -- `parse_entitlement_diff`
    # says why that is not the same thing.
    parsed = parse_entitlement_diff(request, principal, settings_row,
                                    redirect_url=back)
    if not isinstance(parsed, tuple):
        return parsed
    operation, submitted = parsed
    # THE NEW SET IS DERIVED FROM WHAT IS THERE NOW, not from what the
    # form showed: two administrators editing different entitlements on
    # the same tool do not clobber each other, because neither submits
    # the other's pane. `set_tool_labels` still takes the whole wanted
    # set -- it is the single writer, it diffs internally, and it writes
    # the audit rows `/chat/access/` reads.
    before = set(labels_for(key))
    wanted = before | submitted if operation == "add" else before - submitted
    # `user_for_request(request)`, NOT a bare `request.user` read here:
    # `agents/` is one of the three columns `test_no_view_outside_
    # identity_reads_request_user` scans, and its allowlist is empty by
    # design. `identity.request.user_for_request` is the one place that
    # attribute is read, on `identity/`'s own side of the seam, and it
    # answers the real `User` instance `ToolEntitlement.labelled_by`
    # needs -- not the `Principal` value object used for the access
    # checks above.
    set_tool_labels(principal, key, wanted, labelled_by=user_for_request(request))
    messages.info(request, entitlement_change_flash(key, before, wanted))
    return redirect(back)
