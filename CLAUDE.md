# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first.** It is the single source of truth for how work is done
in this repository, and it applies to every agent and every contributor regardless of vendor.

This file exists only because this harness reads this filename by convention. It adds nothing
to the rules — it records the tooling this harness happens to use.

## Claude-specific notes

- **The orchestrating session never writes code.** Not production code, not tests, not
  migrations, not documentation, on any branch, however small the change. It brainstorms,
  plans, briefs, dispatches, reviews, gates, and deploys; every file change in the repository
  is made by a dispatched subagent in a named worktree. The only things it writes itself are
  orchestration artifacts: a brief, a ledger entry, a scratchpad note. If it is about to open
  an editor on a tracked file, it stops and dispatches instead.
- **Tiers.** Orchestration runs on the second tier by default. Implementers default to the
  middle tier, mechanical edits to the cheapest (`AGENTS.md`, "Subagent-driven development").
- **The top tier plans; it never executes.** Keyed to the tier, not the role. Dispatch the
  top tier for architectural judgment, planning, and whole-branch review — as a dispatched
  subagent it returns that plan or analysis to its caller, never a file edit and never the
  work itself.
- **Compact often, at seams.** Compact when a plan is committed, when a task's review closes,
  when a PR merges and its deploy is verified, when an investigation resolves, and whenever
  `/context` shows the conversation past about 40%. Before compacting, write a resume map
  into the plan's ledger (branch and SHA, processes in flight, the next three steps, open
  owner decisions) so nothing survives only in the transcript. Never compact with a subagent
  report unread.
- **Skills.** Work on this repository runs through the superpowers skill set, in order:
  `brainstorming` → `writing-plans` → `plan-hygiene-review` → `subagent-driven-development`
  or `executing-plans` → `test-driven-development` → `requesting-code-review` and
  `receiving-code-review` → `verification-before-completion`.
- **Memory is per-user, and never a substitute for `AGENTS.md`.** Anything in a session's
  private memory that should bind the next contributor belongs in the repository, with a test
  where one is possible.
- **Permissions.** `.claude/settings.json` is committed and read-only by design; it is the
  posture every session in this repository inherits. `.claude/settings.local.json` is yours,
  is gitignored, and is where anything machine-specific belongs — including plugin enablement.
