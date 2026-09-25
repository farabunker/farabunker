"""`/settings/agents/` -- every agent on this box, for an administrator.

THE SECOND MOUNT OF ONE EDITOR, NOT A SECOND EDITOR. `/chat/agents/`
lists the rows a principal may edit; this lists every row on the box for
an operator. Both link to `chat-agent-edit`, which renders
`chat/agent_edit.html` from `agents.chat.agentform.agent_form_context` --
one builder, one template, one POST vocabulary. Nothing about EDITING an
agent is written twice, which is the whole of the owner's "not an
entangled mess", and this module adds no route that edits anything.

CLASS S, AND IT CARRIES ITS OWN `is_admin` CHECK IN ADDITION to the
middleware gate, for the reason `agents/chat/views/assistant.py` records
for its own three views: the gate is not the only way a view function
can be reached, and a settings-area page whose whole body is
administrator-only owes the check at the seam that builds the body
rather than only at the one that routed to it.

IT LISTS THROUGH `agents.visibility.labellable_agents`, the EXISTING
unfiltered read, reused rather than re-spelled. That function's own
docstring gives the reasoning this page inherits unchanged: class S
already means every caller here is an administrator, so asking
`visible_agents(principal)` would wrongly hide a member's own agent from
a page that exists to administer the box's agents whenever
`admin_sees_content` is off -- and an administrator who could not see
the row could not turn a runaway agent off.

THE OWNER COLUMN IS ADMINISTRATOR-ONLY DATA on an administrator-only
route, which is where render-vs-gate lands for this page: the WHOLE body
is admin-only and the route class is the gate, so there is no non-admin
render in which any of it is built.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.http import require_safe

from agents.labels import agent_entitlement_ids
from agents.visibility import labellable_agents
from identity.access import accounts_on, is_admin
from identity.request import principal_for_request, settings_row_for

# THE TWO SENTENCES THE REACH COLUMN CAN SAY, declared here because a
# user-facing sentence is declared once, in Python (AGENTS.md, house
# style), never typed into a template. They are the reader's words for
# `Agent.box_wide`, which is the same distinction the editor's own reach
# control writes -- see `chat/_agent_form.html`.
REACH_EVERYONE = "Everyone on this box"
REACH_GIVEN = "The people it is given to"

# THE PAGE'S OWN FIRST SENTENCE, in the same two-declared-forms shape and
# for the same reason the Restrictions column has two states (ADR 0019
# decision 6, hidden never zeroed): with accounts off the column is not
# rendered, so a tagline promising "what restricts it" contradicts the
# table under it in the reader's very first sentence (wave r1). Declared
# here rather than `{% if %}`-ed inside a template-typed sentence, which
# is the house rule that stops this recurring.
TAGLINE_WITH_RESTRICTIONS = (
    "Every agent on this box — who can reach it, who owns it, and what "
    "restricts it.")
TAGLINE = "Every agent on this box — who can reach it and who owns it."


@require_safe
def agents_admin_list(request):
    """GET `/settings/agents/` -- every agent, its audience, its owner.

    NO POST PATH AT ALL. Every mutation this page offers is a link to
    `chat-agent-edit`, which owns both writes; a second write endpoint
    here would be the second editor this feature exists not to have.
    `require_safe` is what makes that a declared 405 rather than a
    branch nobody wrote.
    """
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    if not is_admin(principal, settings_row=settings_row):
        raise PermissionDenied
    # ONE BATCH READ for every row's labels, and none at all on an open
    # box. `agents/chat/views/agents.py::_restriction_fold` carries the
    # whole argument -- the flat/unbounded trade, the repair if the table
    # outgrows the page, and why the open-box gate is correctness rather
    # than economy. This page takes the identical shape, except it counts
    # every label rather than subtracting `mine`.
    accounts = accounts_on(settings_row=settings_row)
    labels = agent_entitlement_ids() if accounts else {}
    # `labellable_agents()` RETURNS A LIST, so the fold below costs no
    # query per row.
    rows = [{
        "agent": agent,
        "reach": REACH_EVERYONE if agent.box_wide else REACH_GIVEN,
        "label_count": len(labels.get(agent.pk, frozenset())),
    } for agent in labellable_agents()]
    return render(request, "chat/agents_admin.html", {
        "rows": rows,
        # THE COLUMN IS HIDDEN, NEVER ZEROED. `/chat/agents/` renders its
        # count as a chip behind `{% if row.restrictions %}`, so a zero
        # there is already invisible; a TABLE always prints a cell, so
        # this page needs the flag -- and printing `0` would be a third
        # wrong answer, meaning "none" where the truth is "not asked".
        "show_restrictions": accounts,
        # ONE FLAG, EVERY DEPENDENT SENTENCE -- the header, the cell, the
        # colspan and now the tagline all read `accounts`, so none of
        # them can disagree with another.
        "tagline": TAGLINE_WITH_RESTRICTIONS if accounts else TAGLINE,
        # WHERE THE EDITOR COMES BACK TO. `request.get_full_path()` is
        # this page including whatever query string it was reached with
        # (the assistant panel's own `?assistant=1` among them), and the
        # editor validates it through `agents.chat.service` before it is
        # ever redirected to or rendered as an href.
        "next_url": request.get_full_path(),
    })
