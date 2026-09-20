# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first.** It is the single source of truth for how work is done
in this repository, and it applies to every agent and every contributor regardless of vendor.

This file exists only because this harness reads this filename by convention. It adds nothing
to the rules — it records the tooling this harness happens to use.

## Claude-specific notes

- **Skills.** Work on this repository runs through the superpowers skill set, in order:
  `brainstorming` → `writing-plans` → `plan-hygiene-review` → `subagent-driven-development`
  or `executing-plans` → `test-driven-development` → `requesting-code-review` and
  `receiving-code-review` → `verification-before-completion`.
- **Compact before implementing**, so the implementer starts from the plan.
- **Memory is per-user, and never a substitute for `AGENTS.md`.** Anything in a session's
  private memory that should bind the next contributor belongs in the repository, with a test
  where one is possible.
- **Permissions.** `.claude/settings.json` is committed and read-only by design; it is the
  posture every session in this repository inherits. `.claude/settings.local.json` is yours,
  is gitignored, and is where anything machine-specific belongs.
