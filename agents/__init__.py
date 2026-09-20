"""The `agents/` column: the central brain.

A Django app since P2 (`agents/apps.py`, label `agents`), holding:

- `contracts/` -- the P1 TOOL CONTRACT. A rule-1 PURE LEAF: no Django,
  universally importable, in any direction. The app never imports it
  from `models.py` or `apps.py` in a way that could reverse that.
- `models.py`  -- Agent, Conversation, Turn, ToolInvocation (P2).
- `runtime/`   -- the turn runtime: prompt building, invoke, the
  bounded loop, the job wiring (P2). A PRIVATE package: nothing outside
  `agents/` imports it.
- `resident.py`-- the code-declared resident agents (P2). Pure data, no
  Django, so a management command and a test can both read it.

`agents/runtime` reaches a tool's implementation through a dotted-path
STRING resolved at call time (import-law rule 3), never a module-scope
import of `tools/*` -- exactly as an `AppConfig.ready()` today
registers a job kind without ever importing its handler.

THIS FILE MUST STAY A DOCSTRING AND NOTHING ELSE. Importing
`agents.contracts.tools` evaluates it first, and the pure leaf below
inherits anything imported here. Pinned by
`agents/tests/test_apps.py::test_agents_package_init_is_inert`.
"""
