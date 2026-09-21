"""The agent form, as plain data.

ONE BUILDER, TWO MOUNTS, ZERO MANAGERS. `/chat/agents/` and
`/settings/agents/` differ only in which rows they list and who may open
them; nothing about EDITING an agent is written twice, which is the
whole of the owner's "not an entangled mess". A third mount -- a
workstream panel, a future MCP edge -- includes the same fragment and
posts to the same route because this function returns data rather than
markup.

IT NEVER TOUCHES A MANAGER. `foundation/ops/tests/
test_column_boundaries.py` forbids any module under `agents/chat/` from
reaching `Agent`/`Conversation`/`Flow`/`Share`/`Workstream` `.objects`;
everything here comes from `agents.visibility`, `agents.labels`,
`identity.access` and `models.contracts.roles`.

THE LABEL EDITOR IS NOT A NEW CONTROL. It is the two-pane transfer panel
`/chat/access/` and `/chat/tools/` already render
(`foundation/templates/_transfer_panel.html`, fed by
`agents.chat.service.entitlement_panes`), a third consumer of a shared
fragment rather than a fourth spelling of "this row carries entitlement
ids".
"""
from __future__ import annotations

from agents.chat.service import NO_ENTITLEMENTS_COPY, entitlement_panes
from agents.labels import agent_label_ids
from agents.limits import MAX_STEPS_CEILING, MAX_STEPS_DEFAULT
from agents.visibility import name_for_viewer
from identity.access import is_admin, labelling_entitlements
from models.contracts.roles import all_roles

_CHAT_CAPABILITY = "chat"

RESIDENT_WARNING = (
    "This agent was installed from the shipped catalogue. Running "
    "`install_defaults --reset` on it replaces everything below with the shipped text."
)


def _foreign_label_sentence(named, unnamed_count) -> str:
    """How many restrictions this actor cannot change here, and -- only
    for entitlements they already HOLD -- which.

    THE COUNT IS THE MOST THAT CAN BE SAID. `entitlement_panes` builds
    both panes from what this actor may LABEL WITH, so a label an
    administrator set is invisible in the panel; the member's agent is
    narrowed and the page they are standing on looks unlabelled. The
    omission is not a bug to fix by rendering the name -- the
    entitlement non-disclosure rule is a GATE, and
    `identity/tests/test_route_matrix.py` sweeps these routes for
    exactly that. So: counted for what they do not hold, named for what
    they do (no new information -- they hold it, they know its name).
    """
    if not named and not unnamed_count:
        return ""
    parts = []
    if named:
        parts.append(", ".join(name for _pk, name in named))
    if unnamed_count:
        noun = "restriction" if unnamed_count == 1 else "restrictions"
        parts.append(f"{unnamed_count} {noun}")
    carried = " and ".join(parts)
    return (f"This agent also carries {carried} set by an administrator, "
            "which you cannot change here.")


def agent_form_context(principal, *, agent=None, posted=None, errors=None,
                       settings_row=None) -> dict:
    """Everything `chat/_agent_form.html` needs, as plain data.

    `agent=None` is the CREATE form. `posted` is the raw POST body on a
    refused save, so nothing the operator typed is retyped; `errors` is
    the `{field: sentence}` map `agents.visibility.create_agent`/
    `update_agent` returned.

    RENDER-VS-GATE THROUGHOUT: the role options and the reach control
    are administrator-only DATA, and a non-admin's context never builds
    them. A template `{% if %}` over a value that was computed anyway is
    not the same thing.
    """
    admin = is_admin(principal, settings_row=settings_row)
    posted = posted or {}
    if posted:
        values = {
            "name": posted.get("name", ""),
            "description": posted.get("description", ""),
            "system_prompt": posted.get("system_prompt", ""),
            "max_steps": posted.get("max_steps", MAX_STEPS_DEFAULT),
            "enabled": bool(posted.get("enabled")),
        }
    elif agent is not None:
        values = {
            "name": agent.name, "description": agent.description,
            "system_prompt": agent.system_prompt, "max_steps": agent.max_steps,
            "enabled": agent.enabled,
        }
    else:
        values = {"name": "", "description": "", "system_prompt": "",
                  "max_steps": MAX_STEPS_DEFAULT, "enabled": True}

    roles = {role.key: role.label for role in all_roles()
             if role.capability == _CHAT_CAPABILITY}
    current_role = agent.llm_role if agent is not None else ""

    panel = None
    foreign_sentence = ""
    if agent is not None:
        # DEVIATION FROM THE BRIEF (named per the brief's own instruction:
        # the stalled attempt found this and the fix is recorded here
        # rather than rediscovered). The brief's Step 3 computed
        # `missing`/`foreign_sentence` only INSIDE `if choices:`, which
        # means a member who owns no entitlements at all (`choices ==
        # ()`) was told nothing about a label an administrator set on
        # their own row -- exactly the case
        # `test_a_member_is_told_how_many_restrictions_they_cannot_change`
        # exercises. `choices` answers "what may THIS actor label with",
        # which is a different question from "what does this row
        # carry", so the sentence must be computed whenever there is an
        # AGENT to ask about, regardless of whether this actor may label
        # with anything at all. The panel itself still only renders when
        # there is something to offer (`choices` non-empty) -- an actor
        # who may label with nothing has no transfer panel to show, open
        # box or not.
        choices = labelling_entitlements(principal, settings_row=settings_row)
        held = agent_label_ids(agent)
        missing = frozenset(held) - {pk for pk, _name in choices}
        foreign_sentence = _foreign_label_sentence(*name_for_viewer(missing, principal))
        if choices:
            available, active = entitlement_panes(choices, held)
            # THE KEYS `foundation/templates/_transfer_panel.html`
            # REALLY REQUIRES, named the way its own parameter block
            # names them. The two COUNTS are computed here, in the
            # builder, and not in the template, for the reason the
            # fragment states: they are "computed by the caller's own
            # view where the split is ... so a heading cannot disagree
            # with the list under it". `anchor` is the row's own id and
            # `panel_anchor` the panel's own id nested inside it -- one
            # element, one id, the shape `/chat/access/` already passes
            # as `tp_key` and `tp_anchor`.
            panel = {
                "available": available, "active": active,
                "available_count": len(available), "active_count": len(active),
                "total": len(choices),
                "fields": {"pk": agent.pk, "action": "labels"},
                "anchor": f"agent-{agent.pk}",
                "panel_anchor": f"panel-agent-{agent.pk}",
                "no_entitlements": NO_ENTITLEMENTS_COPY,
            }

    return {
        "agent": agent,
        "values": values,
        "errors": errors or {},
        "may_choose_role": admin,
        # ADMINISTRATORS ONLY (spec decision 7): which model backs an
        # agent is box policy, the same call `Chat` and `Job execution`
        # already record for being ADMIN rather than per-person. A
        # non-admin owner sees the current role as read-only text.
        "role_options": tuple(sorted(roles.items())) if admin else (),
        "current_role_label": roles.get(current_role, current_role),
        "may_set_reach": admin,
        "box_wide": bool(agent is not None and agent.box_wide),
        "max_steps_ceiling": MAX_STEPS_CEILING,
        # TRUE, AND NOTHING ELSE SAYS IT.
        "resident_warning": (RESIDENT_WARNING
                             if agent is not None and agent.resident else ""),
        "entitlement_panel": panel,
        "foreign_label_sentence": foreign_sentence,
    }
