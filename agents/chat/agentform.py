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
from models.contracts.roles import chat_capable_roles

RESIDENT_WARNING = (
    "This agent was installed from the shipped catalogue. Running "
    "`install_defaults --reset` on it replaces everything below with the shipped text."
)

# THE FORM'S OWN REFUSAL, for a role value the select never offered.
ROLE_NOT_OFFERED = "Pick one of the model roles offered here."


def chat_role_options() -> tuple[tuple[str, str], ...]:
    """`((key, label), ...)` -- the chat-capable model roles this form
    offers, in key order.

    THE FILTER ITSELF LIVES IN `models.contracts.roles.chat_capable_
    roles` (fix round, review M1), not here: the WRITER
    (`agents.visibility::_validated_agent_fields`) has to refuse the same
    vocabulary at its own seam so a caller that is not this form -- a
    management command, a future MCP edge, both anticipated in this
    module's own header -- cannot stamp an agent with a role no chat turn
    can resolve, and `agents/visibility.py` may not import `agents.chat`
    (import law). One filter below both columns is the only way both can
    ask the same question.

    THIS FUNCTION STAYS, as the FORM's name for it: spec 4.4 assigns the
    vocabulary to the form, `agents.chat.views.agents` refuses through
    this name with the form's own sentence, and a second spelling of the
    filter would be a select that offers what the refusal rejects.
    """
    return chat_capable_roles()


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
                       next_value="", settings_row=None) -> dict:
    """Everything `chat/_agent_form.html` needs, as plain data.

    `agent=None` is the CREATE form. `posted` is the raw POST body on a
    refused save, so nothing the operator typed is retyped; `errors` is
    the `{field: sentence}` map `agents.visibility.create_agent`/
    `update_agent` returned.

    `next_value` IS THE MOUNT'S OWN "WHERE DID I COME FROM", and it is
    the ONE thing the second mount adds to this builder -- no fork, no
    second panel, no second spelling. The field form already carries a
    hidden `next`; the entitlement panel renders its own `<form>` (it
    must -- nested forms are illegal HTML), so it needs its own copy or
    a label save from `/settings/agents/` lands back on a row that has
    forgotten where it came from. `agents.chat.views.agents::_save_
    labels` already honours `validated_next_url` first and falls back to
    the row, so this is the whole of what that mount needs: one hidden
    field, and no view change at all.

    ECHOED, NEVER TRUSTED. The value goes into a hidden field and
    nowhere else; the POST that carries it back is validated by
    `agents.chat.service.validated_next_url`, and the only place it is
    ever rendered as a clickable href goes through the stricter
    `validated_next_link` in the view.

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

    role_options = chat_role_options()
    roles = dict(role_options)
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
                # `next` LAST, AND ONLY WHEN THERE IS ONE, so the
                # rendered panel on `/chat/agents/`'s own mount is
                # byte-identical to what it was before the second mount
                # existed -- an empty hidden field is not the same
                # markup as no hidden field, and `_transfer_panel.html`
                # renders `tp_fields` in order.
                "fields": ({"pk": agent.pk, "action": "labels", "next": next_value}
                           if next_value else {"pk": agent.pk, "action": "labels"}),
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
        "role_options": role_options if admin else (),
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
