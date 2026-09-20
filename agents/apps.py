"""The `agents/` column's Django app (spec section 7, deviation D1).

ONE app for the whole column, `label = "agents"`, so there is one
`agents/migrations/` directory and `apps.get_model("agents", "Agent")`
resolves from a data migration, a management command, and a test
alike. The spec's section 7 preamble named `agents.runtime` as the app;
the owner's P2 direction makes the column root the app and
`agents/runtime/` a private package inside it. Both produce the same
label and the same `agents/0001_initial`.

`ready()` registers the `chat.converse` role, the `agent.turn`
job kind, and the agent-as-tool specs. It imports NO
implementation module: every planner/handler/summarizer/on_terminal/
runner is a dotted-path STRING, resolved lazily by
`models.contracts.jobkinds.resolve_dotted_path` -- the same reason
`tools/vision/apps.py:51-55` gives for its own job kind. And it touches
NO database: Django forbids that here, which is why nothing here writes
a shipped default's row -- `manage.py install_defaults` (or the future
chat-page Add button) is the explicit, consent-gated path ruling 2
requires (`agents/defaults.py`), never a call from this method.
"""
from __future__ import annotations

from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agents"
    label = "agents"

    def ready(self) -> None:
        """Register the `chat.converse` role and the `agent.turn` job kind.

        Local import so app import stays light (no DB, no HTTP at
        startup), matching `tools/rag/apps.py` and `tools/vision/
        apps.py`. Not gated on any feature flag: the agent column is not
        optional, and a role that appeared and disappeared with a flag
        would make the console's role list depend on install-time
        configuration in a way `/inference/` has no way to explain.

        The job kind's registration goes SECOND, after the role: a turn
        cannot even be planned before `chat.converse` exists for
        `resolve_chat` to resolve against.
        """
        from models.contracts.roles import CHAT_CONVERSE_ROLE, RoleSpec, register_role

        register_role(RoleSpec(CHAT_CONVERSE_ROLE, "Agent conversation", "chat"))

        from models.contracts.jobkinds import JobKind, register_job_kind

        register_job_kind(
            JobKind(
                # Literals, not imports: this method imports no handler
                # module by design (see its docstring), which is also
                # why every path below is a string.
                key="agent.turn",
                label="Agent turn",
                planner="agents.runtime.jobs.plan_turn",
                handler="agents.runtime.loop.run_turn",
                summarizer="agents.runtime.jobs.summarize_turn",
                # The queue-wide default (`models/queue/models.py:181`).
                # Lower runs first, so a conversation sits AHEAD of
                # rag.ingest and vision.generate (both 200) and level
                # with rag.ask. A person waiting on a reply should not
                # queue behind a batch ingest.
                default_priority=100,
                # REQUIRED, not optional: a turn has a durable
                # side-effect that predates the job -- the placeholder
                # assistant Turn row -- which is the exact stranded-row
                # condition this hook exists for.
                on_terminal="agents.runtime.jobs.on_turn_terminal",
            )
        )

        # Agent-as-tool (spec section 6.4, deviation D4). Built from
        # CODE -- `agents.defaults.DEFAULT_AGENTS` -- never from rows, so
        # this method still touches no database. The runner stays a
        # dotted-path STRING, so `agents.runtime.delegate` is not
        # imported here.
        from agents.contracts.tools import register_tool
        from agents.resident import agent_tool_specs

        for spec in agent_tool_specs():
            register_tool(spec)

        # The ONE flow tool (ruling 1, deviation P3-D11). A flow is a
        # ROW; its slug reaches the model through this spec's `flow`
        # choices, filled per turn by `agents.runtime.flowtool.
        # narrowed_flow_spec`. So this method still touches no database
        # and still imports no implementation module -- the runner is a
        # dotted-path STRING.
        from agents.runtime.flowtool import FLOW_RUN

        register_tool(FLOW_RUN)

        # THE SETTINGS ASSISTANT'S TWO TOOLS (spec §5.5). Both read-only,
        # neither declaring `mutates`, and `agents.settings_tools` imports
        # nothing heavy at module scope -- so this method still touches no
        # database and still imports no implementation module. Both
        # runners stay dotted-path STRINGS.
        from agents.settings_tools import SETTINGS_CARD, SETTINGS_OVERVIEW

        register_tool(SETTINGS_CARD)
        register_tool(SETTINGS_OVERVIEW)

        # The three owned tables in this column, so `manage.py
        # adopt_open_rows` and `manage.py reassign_owner` can walk them
        # without `identity/` importing `agents` (import-law rule 4).
        # Strings, resolved by `apps.get_model` at command time, exactly
        # as a JobKind's handler is a dotted path.
        from identity.contracts.ownership import OwnedRows, register_owned_rows

        register_owned_rows(OwnedRows("agents.agent", "Agents", "agents.Agent"))
        register_owned_rows(OwnedRows("agents.flow", "Flows", "agents.Flow"))
        register_owned_rows(
            OwnedRows("agents.conversation", "Conversations", "agents.Conversation"))

        # This column's answer to "an entitlement is being deleted", as a
        # DOTTED-PATH STRING so `identity/` can run it without importing
        # `agents/` (import-law rule 4). Registration imports nothing:
        # `identity/cascades.py` resolves the path at delete time, the
        # same way a `JobKind`'s handler is resolved at run time.
        from identity.contracts.cascades import (
            EntitlementCascade, register_entitlement_cascade,
        )

        register_entitlement_cascade(EntitlementCascade(
            key="agents.tool_labels",
            label="Tool labels",
            handler="agents.labels.tool_labels_cascade",
        ))

        # Task 15 (spec sections 6.11, 9.6): this column's SECOND answer
        # to "an entitlement is being deleted" -- agent and flow labels,
        # one cascade for both tables (`agents.labels.
        # agent_flow_label_cascade`'s own docstring says why).
        register_entitlement_cascade(EntitlementCascade(
            key="agents.runnable_labels",
            label="Agent and flow labels",
            handler="agents.labels.agent_flow_label_cascade",
        ))

        # This column's THIRD answer to "an entitlement is being
        # deleted": a stream's wall rows, and its taint tags at both
        # levels (WS-2, Task 16). Same dotted-path mechanism, same
        # reason.
        register_entitlement_cascade(EntitlementCascade(
            key="agents.workstream_entitlements",
            label="Workstream scopes and taint tags",
            handler="agents.workstreams.workstream_entitlement_cascade",
        ))

        # THE OTHER DIRECTION ON THE SAME THREE TABLES. The cascades
        # above answer "this entitlement is going away"; these answer
        # "which of your rows carry it, and change that" -- the question
        # an operator administering fifty entitlements asks from the
        # entitlement's own page. Same dotted-path mechanism, same
        # import-law reason (rule 4: `identity/` may not name anything in
        # here), same idempotent `ready()`. `agents/axes.py`'s own
        # docstring says why the writes still go through
        # `agents/labels.py`.
        #
        # THREE AXES FOR THE ONE CASCADE that covers agents and flows
        # together, deliberately: a delete confirmation reads better as
        # one line, and an EDITOR reads worse as one pane holding two
        # kinds of thing.
        #
        # `agents.axes` IS IMPORTED, unlike the cascade block above which
        # imports nothing: the tools panel's one-line hint is a
        # user-facing sentence, and the house rule is that those are
        # declared once, in Python, beside the marker they explain
        # (`agents.axes.PAGE_ONLY_NOTE`) rather than retyped here. The
        # module holds no model import at its own top level, so this
        # keeps `ready()`'s no-database, no-heavy-import promise.
        from agents import axes
        from identity.contracts.axes import EntitlementAxis, register_entitlement_axis

        register_entitlement_axis(EntitlementAxis(
            key="agents.tools", label="Tools", order=10,
            hint=axes.TOOLS_HINT,
            counts="agents.axes.tool_counts",
            rows="agents.axes.tool_rows",
            ids_for="agents.axes.tool_ids_for",
            set_for="agents.axes.set_tool_axis",
        ))
        register_entitlement_axis(EntitlementAxis(
            key="agents.agents", label="Agents", order=20,
            counts="agents.axes.agent_counts",
            rows="agents.axes.agent_rows",
            ids_for="agents.axes.agent_ids_for",
            set_for="agents.axes.set_agent_axis",
        ))
        register_entitlement_axis(EntitlementAxis(
            key="agents.flows", label="Flows", order=30,
            counts="agents.axes.flow_counts",
            rows="agents.axes.flow_rows",
            ids_for="agents.axes.flow_ids_for",
            set_for="agents.axes.set_flow_axis",
        ))
