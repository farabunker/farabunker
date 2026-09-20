# The planning archive

This directory is a **historical record**, not current documentation.

Each file under `plans/` is the implementation plan for one phase of work, written before
that phase was executed and left untouched afterwards. Each file under `specs/` is the
design document a plan argues from. They are dated, and they describe the tree as it stood
on that date: paths, module names, line numbers and file counts in here are frequently
stale, deliberately so. **Nothing in this directory is a statement about how the system
works today.** For that, read [ARCHITECTURE.md](../ARCHITECTURE.md), the decision record in
[docs/adr/](../adr/), and the module READMEs beside the code.

## Why keep them

1. **Resuming a phase.** A plan is executable: its tasks carry the tests, the exact file
   paths, and the commits in order. Work that stopped halfway can be picked up by reading
   the plan and finding the last checked box.
2. **Explaining a decision that never earned an ADR.** An ADR records what was decided; a
   plan records the twenty smaller choices made while carrying it out, and the constraints
   that forced them.
3. **Auditability.** This is a security product, and being able to show how a given
   behaviour got there is part of what the project sells.

Amendments are appended and dated rather than edited in place, for the same reason ADRs
are: the record is worth more than the tidiness. A plan's `## Smoke Checklist`, where
present, is the browser-level walk driven against a preview stack before the merge decision.

## What does not belong in here

- **Anything current.** If a statement needs to stay true, it belongs in `docs/` proper, in
  an ADR, or in a module README — with a test where one is possible.
- **Absolute local paths, personal data, or model names** — see AGENTS.md's non-negotiables
  on model names, and on absolute paths and personal data.
  `foundation/ops/tests/test_agent_standards.py` fails the build on a committed absolute
  home path.
- **Live session state.** Per-session, per-plan working ledgers live in the untracked
  `.superpowers/` directory at the repository root, not here.

The working rules these plans are executed under are in
[AGENTS.md](../../AGENTS.md).
