"""The agent pages: this principal's own list, creation, and the ONE
edit route both mounts share.

THE EDIT ROUTE IS CLASS O, NOT S -- admitted at the middleware, 404 in
the view for a row `may_manage_agent` refuses, which is the definition
of the class. An S route would refuse a non-admin at the middleware,
which is exactly the person this feature exists for.

THE LISTING GOES THROUGH `agents.visibility`, never `Agent.objects` --
`foundation/ops/tests/test_column_boundaries.py` fails the build
otherwise, and the two readers here (`editable_agents`,
`box_wide_agents_owned_by`) are the ones that know the posture rules.

`?next=` FOLLOWS THE HOUSE PATTERN EXACTLY. `agents.chat.service.
validated_next_url` reads `request.POST` and nothing else, so a `?next=`
arriving on a GET link is NOT validated by it and a naive
`request.GET["next"]` redirect would be an open redirect off this box.
The list link carries `?next=`, the GET ECHOES IT INTO A HIDDEN FIELD
and does nothing else with it, and the POST validates through
`validated_next_url`, falling back to this list.

THE ROLE VOCABULARY IS CHECKED HERE, NOT IN THE WRITER (review round
addition). `agents.visibility::_validated_agent_fields` gates `llm_role`
on ADMIN alone and writes whatever string an administrator sends;
WHICH roles this surface offers is the FORM's question (spec 4.4), and
`agents.chat.agentform.chat_role_options` is the one answer to it. So
both FIELD-WRITING POST paths below refuse a role that is not among the
options the select really rendered, rather than letting a stale or
hand-made body stamp an agent with a role no chat turn can resolve.

THE AUDIENCE CONTROLS ARE THE EDIT ROUTE'S SECOND POST PATH, and every
label mutation on this surface goes through ONE seam:
`agents.chat.service.parse_entitlement_diff` (the gate) and then
`agents.labels.set_agent_labels` (a raw writer that enforces nothing).
`_save_labels` below is the same four steps `/chat/access/` already
takes, and the reason there are two controls rather than one is spec
4.3.1's: REACH answers to the actor's own authority, while a LABEL
answers to whether the actor may label with THAT entitlement. A single
control writing both would either clobber a label its actor may not
touch -- the hole spec review M1 found -- or refuse an edit it should
allow.
"""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods

from agents.chat.agentform import ROLE_NOT_OFFERED, agent_form_context, chat_role_options
from agents.chat.service import (
    entitlement_change_flash, parse_entitlement_diff, validated_next_link,
    validated_next_url,
)
from agents.chat.sidebar import sidebar_context
from agents.labels import agent_entitlement_ids, agent_label_ids, set_agent_labels
from agents.visibility import (
    box_wide_agents_owned_by, create_agent, editable_agents, labellable_agent,
    may_manage_agent, update_agent,
)
from identity.access import accounts_on, is_admin, labelling_entitlements
from identity.request import principal_for_request, settings_row_for, user_for_request

# TRUE, AND NOTHING ELSE ON THIS PAGE SAYS IT.
BOX_WIDE_SECTION_TITLE = "Agents everyone on this box can use"
# THE TWO SENTENCES THIS SECTION CAN SAY, and which one a reader gets is
# `may_manage_agent`'s answer for that ROW, never the section's (fix round,
# review I2). The note below used to be the section's ONLY sentence, which
# made it a FALSE statement for an administrator -- who IS the "administrator"
# it points at -- and on an open box, where `sees_all_content` answers True
# for everybody, that administrator is the DEFAULT reader.
BOX_WIDE_SECTION_NOTE = (
    "You installed this from the shipped catalogue, so it is available to everyone "
    "here. An administrator can change it."
)
BOX_WIDE_SECTION_ADMIN_NOTE = (
    "You administer this box, so this one is yours to change — and an edit here "
    "changes it for everyone."
)

# `agent_edit`'s WHOLE ACTION VOCABULARY, and anything outside it is refused
# rather than silently handled as a field save, which is what a branchless POST
# handler did (Task 8 fix round, review M2). The two names are the page's two
# CONTROLS: the field form writes the row through `update_agent`, the
# entitlement panel writes its labels through the gated diff, and neither can
# be reached by the other's body.
#
# EACH CONTROL SHIPS ITS OWN SPELLING OF ITS OWN NAME -- `chat/_agent_form.
# html`'s hidden `value="fields"`, and `agents.chat.agentform.
# agent_form_context`'s `entitlement_panel.fields["action"]` -- because a
# template cannot read a view constant and the form module sits BELOW this one
# (it is what this module imports, so it cannot import back). The two spellings
# are pinned together by a test rather than left to agree by luck:
# `test_agent_pages.py::TestTheAudienceWriteInBothDirections::
# test_the_panels_own_hidden_action_is_the_one_this_view_branches_on`.
FIELDS_ACTION = "fields"
LABELS_ACTION = "labels"
AGENT_UNKNOWN_ACTION = "That is not something this page can do."


def _restriction_fold(principal, settings_row):
    """A `rows -> [{"agent": row, "restrictions": n}, ...]` fold: each
    row beside how many labels it carries that this principal cannot
    manage.

    A LIST OF DICTS, not a `{agent_id: n}` mapping beside a list of
    rows: Django's template language has no `get_item` filter, so a
    parallel mapping keyed by pk is unreadable from a `{% for %}` at
    all.

    A CLOSURE OVER THE TWO BATCH READS, so the page's TWO sections
    share them instead of each paying for its own pair.

    THE BARE COUNT ONLY, never `name_for_viewer`: naming one here would
    be the non-disclosure gate, and a per-row `held_entitlement_ids`
    read would be the sidebar N+1 all over again. TWO BATCH READS for
    the whole page -- `agent_entitlement_ids()` reads every row's labels
    in one `values_list` (the discipline `/chat/access/` documents) and
    `labelling_entitlements` is one more -- and the per-row number is a
    FOLD over them rather than a query of its own.

    `agent_entitlement_ids()` READS EVERY LABELLED AGENT ON THE BOX, not
    only the rows being listed (review N1). That is FLAT in the number of
    rows this page renders -- which is what `test_the_list_costs_the_same
    _at_one_agent_and_at_twenty_five` pins -- and UNBOUNDED in the size of
    the `AgentEntitlement` table. It is the same trade `/chat/access/`
    takes for the same reason (one `values_list` beats one query per row);
    if that table ever outgrows the page, the repair is a pk-narrowed
    reader in `agents/labels.py`, never a per-row read here.

    AND IT IS NOT READ AT ALL ON AN OPEN BOX (Task 10 review I1), gated
    on `accounts_on()` alone -- the same ruling-A shape
    `views/workstreams.py`'s own setup context takes, and the reason
    `chat-workstream-new` is in `identity/tests/test_zero_queries.py::
    _MOUNTS`. `labelling_entitlements` ALREADY returns `()` before
    touching its table when accounts are off, so this one gate is what
    makes the whole page query-free there.

    THE GATE IS A CORRECTNESS FIX, NOT AN ECONOMY. With accounts off
    `mine` is empty, so `len(labels - mine)` becomes "every label on this
    row" -- rendered under a fold whose documented meaning is "how many
    labels it carries that THIS PRINCIPAL CANNOT MANAGE" and printed as
    "N restrictions". Both halves are false there: a label restricts
    nobody on an open box (`visible_agents` returns at its
    `sees_all_content` short-circuit before the label clause is
    evaluated, spec 4.3.1), and the single operator is the administrator
    who could change every one of them the moment accounts are on. `0`
    is the honest value for a question that was not asked, and the two
    templates HIDE the number rather than printing it.

    `manageable` IS `may_manage_agent`'s ANSWER FOR THAT ROW, threaded
    with the `settings_row` this request already holds so the predicate
    adds no query per row (fix round, review I2). ONE ROW SHAPE, not two:
    for the editable section it is True by construction -- `editable_
    agents` IS the rows this principal may edit -- and only the box-wide
    section reads it, where an administrator and a member get genuinely
    different answers. The one read this does not thread lives inside
    `identity.access.may_read_owned_row` and is reached only for a
    `service`-owned row.
    """
    labels = (agent_entitlement_ids()
              if accounts_on(settings_row=settings_row) else {})
    mine = {pk for pk, _name in labelling_entitlements(principal,
                                                       settings_row=settings_row)}

    def fold(rows):
        return [{"agent": row,
                 "restrictions": len(labels.get(row.pk, frozenset()) - mine),
                 "manageable": may_manage_agent(principal, row,
                                                settings_row=settings_row)}
                for row in rows]

    return fold


def _cancel_url(request, next_value):
    """Where `Cancel` lands: the `next` this page arrived with, validated
    for an href -- or this principal's own agent list.

    TASK 8 REVIEW N3. The link used to go to `chat-agents`
    unconditionally, which was the right call while `/chat/agents/` was
    the only mount: an UNVALIDATED GET `next` must never become a
    clickable href. `/settings/agents/` is the second mount, and from
    there an administrator who cancels landed on the member-facing list
    -- a page that does not even contain the row they were just looking
    at. `agents.chat.service.validated_next_link` is what makes the
    value safe enough to render: same-origin, same-scheme, AND a path on
    this box. A hostile or foreign value falls back to the list, exactly
    as it did before.

    THE HREF AND THE HIDDEN FIELD ARE STILL DIFFERENT THINGS. The hidden
    `next` is echoed raw (it is escaped by the template and validated
    again at POST time by `validated_next_url`); only what a reader can
    CLICK goes through the stricter guard, and only ever as the whole
    href.
    """
    return validated_next_link(request, next_value) or reverse("chat-agents")


def _edit_page(request, principal, settings_row, *, agent=None, next_value="",
               posted=None, errors=None, page_error=None, status=200):
    """The one `chat/agent_edit.html` render, for every path this
    module reaches it from: `form_action` is a pure function of `agent`
    (new when `agent is None`, the row route otherwise), and the other
    three context keys -- `next_value`, `cancel_url`,
    `sidebar_context(...)` -- are identical on all five call sites this
    replaces. `page_error` is the one key that is not: it is passed
    only on the unknown-action 400, and left out (rather than set to
    `None`) everywhere else, so the other four contexts keep exactly
    the keys they had before this helper existed.

    NO `next_value=` INTO THE BUILDER ON THE CREATE-GET PATH used to be
    a deliberate omission (the create route has no row to label yet, so
    `agent_form_context` returns no entitlement panel and there is no
    second form to carry a `next` for) -- passing it uniformly here is
    inert: `agentform.py` reads `next_value` only inside `if choices:`
    inside `if agent is not None and accounts_on(...)`, so with
    `agent=None` nothing reads it and the rendered panel is unchanged.
    """
    form_action = (reverse("chat-agent-new") if agent is None
                  else reverse("chat-agent-edit", args=[agent.pk]))
    context = {
        **agent_form_context(principal, agent=agent, posted=posted,
                             errors=errors, next_value=next_value,
                             settings_row=settings_row),
        "form_action": form_action,
        "next_value": next_value,
        "cancel_url": _cancel_url(request, next_value),
        **sidebar_context(principal, settings_row=settings_row),
    }
    if page_error is not None:
        context["page_error"] = page_error
    return render(request, "chat/agent_edit.html", context, status=status)


def _unoffered_role_errors(principal, posted, settings_row):
    """`{"llm_role": ROLE_NOT_OFFERED}` when this body names a role the
    select never offered -- `{}` otherwise.

    ONLY AN ADMINISTRATOR'S `llm_role` IS READ AT ALL (the writer drops
    the field for anybody else, silently, because it was never offered
    to them), so only an administrator's can be wrong here. An absent
    or empty field is not a forgery: the writer leaves the row's
    existing role alone, which is what a form with no select POSTs.
    """
    role = (posted.get("llm_role") or "").strip()
    if not role or not is_admin(principal, settings_row=settings_row):
        return {}
    if role in {key for key, _label in chat_role_options()}:
        return {}
    return {"llm_role": ROLE_NOT_OFFERED}


@require_http_methods(["GET", "HEAD"])
def agent_list(request):
    """GET `/chat/agents/` -- the agents this principal may edit, plus
    the box-wide rows they own, read-only."""
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    rows = editable_agents(principal, settings_row=settings_row)
    box_wide = box_wide_agents_owned_by(principal, settings_row=settings_row)
    counted = _restriction_fold(principal, settings_row)
    return render(request, "chat/agents.html", {
        "rows": counted(rows),
        "box_wide_rows": counted(box_wide),
        "box_wide_section_title": BOX_WIDE_SECTION_TITLE,
        "box_wide_section_note": BOX_WIDE_SECTION_NOTE,
        "box_wide_section_admin_note": BOX_WIDE_SECTION_ADMIN_NOTE,
        "next_url": request.get_full_path(),
        **sidebar_context(principal, settings_row=settings_row),
    })


@require_http_methods(["GET", "HEAD", "POST"])
def agent_new(request):
    """GET renders the same fragment with an empty agent; POST creates.

    CLASS A -- creation needs an authenticated principal and no row.
    """
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    if request.method != "POST":
        next_value = request.GET.get("next", "")
        return _edit_page(request, principal, settings_row,
                          next_value=next_value)
    fields = request.POST.dict()
    errors = _unoffered_role_errors(principal, fields, settings_row)
    row = None
    if not errors:
        row, errors = create_agent(principal, fields, settings_row=settings_row)
    if errors:
        next_value = request.POST.get("next", "")
        return _edit_page(request, principal, settings_row,
                          next_value=next_value, posted=fields, errors=errors)
    messages.info(request, f"Created {row.name}.")
    return redirect(validated_next_url(request) or reverse("chat-agents"))


@require_http_methods(["GET", "HEAD", "POST"])
def agent_edit(request, pk: int):
    """One agent, edited. CLASS O: 404 unless `may_manage_agent`.

    TWO POST PATHS, told apart by an `action` field: `fields` writes the
    row through `update_agent`, and `labels` goes through
    `parse_entitlement_diff` -> `set_agent_labels`. They are two forms on
    one page for the reason spec 4.3.1 gives: reach answers to the
    actor's own authority and a label answers to whether the actor may
    label with THAT entitlement, and one control writing both would
    either clobber a label it may not touch or refuse an edit it should
    allow.
    """
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    # `labellable_agent`, THE READER THAT ALREADY EXISTS -- not a second
    # name for `Agent.objects.filter(pk=pk).first()` in the same module.
    # It is a READER, never a gate: `may_manage_agent` below is the gate,
    # and the pair becomes this route's house 404.
    agent = labellable_agent(pk)
    if agent is None or not may_manage_agent(principal, agent,
                                             settings_row=settings_row):
        raise Http404(f"No agent {pk} you may change.")
    if request.method == "POST":
        # TWO ACTIONS, NAMED RATHER THAN ASSUMED, and everything else
        # refused (fix round, review M2). Both controls ship their own
        # hidden `action` field, so a body naming neither is a stale form
        # or a hand-made request -- and handling THAT as a field save is
        # how a labels-shaped body would silently blank a row's prompt.
        # Refused with the page re-rendered and a declared sentence, the
        # house 400 shape (`views/workstreams.py`,
        # `views/conversations.py`), never a save.
        action = request.POST.get("action", "")
        if action == LABELS_ACTION:
            return _save_labels(request, principal, agent, settings_row)
        if action != FIELDS_ACTION:
            next_value = request.POST.get("next", "")
            return _edit_page(request, principal, settings_row, agent=agent,
                              next_value=next_value,
                              page_error=AGENT_UNKNOWN_ACTION, status=400)
        return _save_fields(request, principal, agent, settings_row)
    # THE GET ECHOES `?next=` AND DOES NOTHING ELSE WITH IT -- see this
    # module's docstring. The ONE exception is the Cancel link, which is
    # built through `validated_next_link`'s stricter same-box-path guard
    # (`_cancel_url` above, review N3); the raw value still reaches only
    # the hidden fields.
    next_value = request.GET.get("next", "")
    return _edit_page(request, principal, settings_row, agent=agent,
                      next_value=next_value)


def _save_fields(request, principal, agent, settings_row):
    fields = request.POST.dict()
    errors = (_unoffered_role_errors(principal, fields, settings_row)
              or update_agent(principal, agent, fields, settings_row=settings_row))
    if errors:
        next_value = request.POST.get("next", "")
        return _edit_page(request, principal, settings_row, agent=agent,
                          next_value=next_value, posted=fields, errors=errors)
    messages.info(request, f"Saved {agent.name}.")
    return redirect(validated_next_url(request) or reverse("chat-agents"))


def _save_labels(request, principal, agent, settings_row):
    """The entitlement panel's save -- the SAME four steps
    `agents/chat/views/access.py::_save` takes, reached from this page.

    `parse_entitlement_diff` IS THE GATE, and `set_agent_labels` is not.
    That writer is raw: it takes `actor` only to stamp the audit row,
    never consults `labelling_entitlements`, and its `remove` closure
    deletes any row the diff names. The check that a submitted id is one
    this principal may label with lives in the parser, over the SAME
    predicate the form rendered from, so a stale form or a hand-made
    request gets the identical honest refusal.

    THE NEW SET IS DERIVED FROM WHAT IS THERE NOW, never from what the
    form showed: a submitted whole set clobbers, and a label this actor
    may not label with is never in `submitted`, so `before | submitted`
    cannot add it and `before - submitted` cannot remove it. That is why
    an administrator's label stands whatever a member does with their
    own, by construction rather than by a check -- which is the whole of
    spec review M1's fix.

    THE REFUSAL URL IS THIS ROW'S OWN ROUTE, not
    `agents.chat.service.entitlement_row_url`: that helper builds
    `reverse(route_name)` with NO arguments plus an `?open=` anchor, for
    the two access pages whose rows all live on one URL, and
    `chat-agent-edit` is row-addressed -- it cannot name this route at
    all. `reverse("chat-agent-edit", args=[agent.pk])` already IS the
    row, and there is no `<details>` state to restore on a page whose
    panel is the only one it renders.

    AND THE REFUSAL CARRIES THE MOUNT'S `next` TOO (Task 10 review M3),
    which is the other half of review N3. A refusal is a REDIRECT to the
    editor, so without the parameter the re-rendered page has forgotten
    where it came from and its own Cancel link drops back to
    `/chat/agents/` -- exactly the strand N3 was about, reached by
    mistyping rather than by cancelling. Built from `validated_next_url`,
    the same guard the success path uses, so an off-box value is dropped
    here as well as there.
    """
    row_url = reverse("chat-agent-edit", args=[agent.pk])
    onward = validated_next_url(request)
    back = f"{row_url}?{urlencode({'next': onward})}" if onward else row_url
    parsed = parse_entitlement_diff(request, principal, settings_row,
                                    redirect_url=back)
    if not isinstance(parsed, tuple):
        return parsed
    operation, submitted = parsed
    # `user_for_request`, NOT a bare `request.user` read: `agents/` is one
    # of the three columns `test_no_view_outside_identity_reads_request_
    # user` scans, and `AgentEntitlement.labelled_by` needs the real
    # `User` instance rather than the `Principal` value object the access
    # checks above use.
    labelled_by = user_for_request(request)
    before = set(agent_label_ids(agent))
    wanted = before | submitted if operation == "add" else before - submitted
    set_agent_labels(principal, agent, wanted, labelled_by=labelled_by)
    messages.info(request, entitlement_change_flash(agent.slug, before, wanted))
    # THE MOUNT'S OWN `next` FIRST, exactly as `_save_fields` above does
    # -- the panel ships one since the second mount existed
    # (`agentform.py`'s `tp_fields`). The fallback is this ROW rather
    # than the list: a label edit leaves you where you were, so
    # `row_url`, not the `back` above, which is the row plus the `next` a
    # REFUSAL needs to re-render with.
    return redirect(onward or row_url)
