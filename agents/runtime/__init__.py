"""The turn runtime: prompt building, tool invocation, the bounded loop,
agent-as-tool delegation, and the queue job wiring.

A PRIVATE package inside the `agents` app. Nothing outside `agents/`
imports it, and it imports `tools/*` through NOTHING but
`models.contracts.jobkinds.resolve_dotted_path` -- import-law rule 3,
the clause that keeps this column from depending on the columns whose
tools it runs.

RULE 3 IS PINNED (Task 14): `foundation/ops/tests/test_import_law.py::
test_no_agents_module_imports_a_tools_package` AST-walks EVERY import
node (module scope or lazy, in-body) in every production file under
`agents/`, this package included, for a `tools`/`tools.*` name -- not
just the `_REGISTRATION_MODULES` shape `test_column_boundaries.py`'s
module-scope-import guard polices for `AppConfig.ready()`'s own narrower
promise. `delegate.py` is swept for the no-`get_job`-blocking rule under
`RUNTIME_MODULES` (`test_column_boundaries.py`), not under `TOOL_MODULES`
(the registration modules `ready()` actually imports) -- see that list's
own comment for why the two are separate.

Six modules, bottom-up:
  prompt.py    -- conversation rows -> llama-index ChatMessages.
  invoke.py    -- one tool call: validate, run, classify, audit.
  bindings.py  -- `resolve_chat`: which model answers this turn, the one
                  rule `plan_turn`, `run_turn`, and `agent_turn`'s own
                  preflight all share.
  loop.py      -- the bounded ReAct loop over the two above, extracted
                  as `run_loop` so `delegate.py` can run it a level
                  deeper on the same shared budget.
  delegate.py  -- agent-as-tool: `run_loop` again, `depth + 1`, same
                  budget, own principal (spec section 6.4).
  jobs.py      -- planner / handler / summarizer / on_terminal for the
                 `agent.turn` job kind.

See `agents/runtime/README.md` for the loop's five endings, the recovery
policy, and the post-deadline-latency note -- the place to start when
debugging a turn.
"""
