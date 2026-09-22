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
both POST paths below refuse a role that is not among the options the
select really rendered, rather than letting a stale or hand-made body
stamp an agent with a role no chat turn can resolve.
"""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from agents.chat.agentform import ROLE_NOT_OFFERED, agent_form_context, chat_role_options
from agents.chat.service import validated_next_url
from agents.chat.sidebar import sidebar_context
from agents.labels import agent_entitlement_ids
from agents.visibility import (
    box_wide_agents_owned_by, create_agent, editable_agents, labellable_agent,
    may_manage_agent, update_agent,
)
from identity.access import is_admin, labelling_entitlements
from identity.request import principal_for_request, settings_row_for

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

# `agent_edit` accepts ONE action today (fix round, review M2). Task 9 adds
# "labels" beside it; until then anything else is refused rather than silently
# handled as a field save, which is what a branchless POST handler did.
FIELDS_ACTION = "fields"
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
    labels = agent_entitlement_ids()
    mine = {pk for pk, _name in labelling_entitlements(principal,
                                                       settings_row=settings_row)}

    def fold(rows):
        return [{"agent": row,
                 "restrictions": len(labels.get(row.pk, frozenset()) - mine),
                 "manageable": may_manage_agent(principal, row,
                                                settings_row=settings_row)}
                for row in rows]

    return fold


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
        return render(request, "chat/agent_edit.html", {
            **agent_form_context(principal, settings_row=settings_row),
            "form_action": reverse("chat-agent-new"),
            "next_value": request.GET.get("next", ""),
            **sidebar_context(principal, settings_row=settings_row),
        })
    fields = request.POST.dict()
    errors = _unoffered_role_errors(principal, fields, settings_row)
    row = None
    if not errors:
        row, errors = create_agent(principal, fields, settings_row=settings_row)
    if errors:
        return render(request, "chat/agent_edit.html", {
            **agent_form_context(principal, posted=fields, errors=errors,
                                 settings_row=settings_row),
            "form_action": reverse("chat-agent-new"),
            "next_value": request.POST.get("next", ""),
            **sidebar_context(principal, settings_row=settings_row),
        })
    messages.info(request, f"Created {row.name}.")
    return redirect(validated_next_url(request) or reverse("chat-agents"))


@require_http_methods(["GET", "HEAD", "POST"])
def agent_edit(request, pk: int):
    """One agent, edited. CLASS O: 404 unless `may_manage_agent`.

    TWO POST PATHS, told apart by an `action` field: `fields` writes the
    row through `update_agent`, and `labels` goes through
    `parse_entitlement_diff` -> `set_agent_labels` (Task 9). They are
    two forms on one page for the reason spec 4.3.1 gives: reach
    answers to the actor's own authority and a label answers to whether
    the actor may label with THAT entitlement, and one control writing
    both would either clobber a label it may not touch or refuse an edit
    it should allow.
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
        # ONE ACTION TODAY, NAMED RATHER THAN ASSUMED (fix round, review
        # M2). `chat/_agent_form.html` already ships the hidden
        # `action=fields` field, so a body naming anything else is a stale
        # form or a hand-made request -- and handling it as a field save is
        # how an `action=labels` body would silently blank a row's prompt
        # the day Task 9's panel starts posting one. Refused with the page
        # re-rendered and a declared sentence, the house 400 shape
        # (`views/workstreams.py`, `views/conversations.py`), never a save.
        # Task 9's second path is then a pure insertion here.
        if request.POST.get("action", "") != FIELDS_ACTION:
            return render(request, "chat/agent_edit.html", {
                **agent_form_context(principal, agent=agent,
                                     settings_row=settings_row),
                "form_action": reverse("chat-agent-edit", args=[agent.pk]),
                "next_value": request.POST.get("next", ""),
                "page_error": AGENT_UNKNOWN_ACTION,
                **sidebar_context(principal, settings_row=settings_row),
            }, status=400)
        return _save_fields(request, principal, agent, settings_row)
    return render(request, "chat/agent_edit.html", {
        **agent_form_context(principal, agent=agent, settings_row=settings_row),
        "form_action": reverse("chat-agent-edit", args=[agent.pk]),
        # THE GET ECHOES `?next=` AND DOES NOTHING ELSE WITH IT -- see
        # this module's docstring.
        "next_value": request.GET.get("next", ""),
        **sidebar_context(principal, settings_row=settings_row),
    })


def _save_fields(request, principal, agent, settings_row):
    fields = request.POST.dict()
    errors = (_unoffered_role_errors(principal, fields, settings_row)
              or update_agent(principal, agent, fields, settings_row=settings_row))
    if errors:
        return render(request, "chat/agent_edit.html", {
            **agent_form_context(principal, agent=agent, posted=fields,
                                 errors=errors, settings_row=settings_row),
            "form_action": reverse("chat-agent-edit", args=[agent.pk]),
            "next_value": request.POST.get("next", ""),
            **sidebar_context(principal, settings_row=settings_row),
        })
    messages.info(request, f"Saved {agent.name}.")
    return redirect(validated_next_url(request) or reverse("chat-agents"))
