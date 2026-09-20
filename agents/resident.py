"""Agent-as-tool registration (spec section 6.4, deviation D4).

PURE DATA plus one derivation -- `agent_tool_specs()` -- the two things
`agents/apps.py::ready()` genuinely needs. No Django import of any
kind: `ready()` reads this module while touching no database, and an
AST test pins it (`agents/tests/test_resident.py`).

RULING 2 (2026-08-28) retired `sync_resident_agents` and moved
`AgentSpec`/`RESIDENT_AGENTS` (renamed `DEFAULT_AGENTS`) to
`agents/defaults.py`: nothing writes an agent row automatically any
more, and the declarations are now a CATALOGUE an operator installs
explicitly, through `agents.defaults.install_default` or
`manage.py install_defaults`. What survives here is only what
`ready()` needs at import time:

- `AGENT_TOOL_PREFIX`, shared with `agents/runtime/jobs.py`'s planner
  closure walk (which needs to recognise a delegate tool key without
  importing the runner it never calls) and `agents/runtime/
  delegate.py`'s runner (which strips it back off to recover the
  agent's slug);
- `agent_tool_specs()`, built from CODE -- `agents.defaults.
  DEFAULT_AGENTS` -- never from rows, so `ready()` still touches no
  database. A row-declared (non-default) agent has no way to become an
  `agent.<slug>` tool for exactly that reason -- a named deferral, not
  an oversight (`agents/README.md`'s P3-G1 note).
"""
from __future__ import annotations

from agents.defaults import DEFAULT_AGENTS

# The prefix every agent-as-tool spec key carries. Shared with
# `agents/runtime/jobs.py`'s planner closure walk, which needs to
# recognise one without importing the delegate runner it never calls.
AGENT_TOOL_PREFIX = "agent."


def agent_tool_specs() -> tuple:
    """One `ToolSpec` per `as_tool=True` entry of `agents.defaults.
    DEFAULT_AGENTS` (spec section 6.4, deviation D4).

    The spec says ONE `agent.delegate` tool with an `agent` param,
    "because tools are code-registered; agents are rows". This registers
    one spec PER AGENT and stays inside that constraint by building them
    from CODE -- `DEFAULT_AGENTS` -- never from rows, so
    `AppConfig.ready()` still touches no database. A user-built (or a
    row-installed) agent is therefore NOT exposed as a tool. That needs
    either DB access in `ready()` (forbidden) or a dynamic registry (not
    designed). A named deferral.

    All of them share ONE runner, which is what
    `ToolContext.tool_key` exists for: a runner's signature is
    `(args, ctx)`, and N specs behind one function have no other way to
    learn which one invoked them.

    `roles=()` on every spec, deliberately: a delegate's roles are not
    knowable at registration time, and `agents.runtime.jobs.plan_turn`
    supplies them at enqueue time by walking the closure.
    """
    from agents.contracts.tools import ToolSpec
    from models.contracts.operations import Param

    return tuple(
        ToolSpec(
            key=f"{AGENT_TOOL_PREFIX}{spec.slug}",
            label=f"Ask the {spec.name} agent",
            description=(
                f"{spec.description} Hand it one self-contained task and it will "
                f"work on it with its own tools and hand back what it found. It "
                f"cannot see this conversation, so say everything it needs."
            ),
            params=(Param(
                "task", "text", "Task", required=True,
                description="The complete, self-contained assignment for that agent.",
            ),),
            roles=(),
            runner="agents.runtime.delegate.run_agent_tool",
        )
        for spec in DEFAULT_AGENTS
        if spec.as_tool
    )
