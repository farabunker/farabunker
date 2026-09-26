---
name: subagent-brief
description: Use when the orchestrating session is dispatching a subagent to implement or review a task -- what a brief must carry so the subagent doesn't re-derive process from a plan path alone.
---

# Subagent brief

## Purpose

Plans here run 5-12k lines; a brief that hands a subagent only the plan's file path forces it
to re-derive everything relevant and drops the rulings only the orchestrator's head holds.

## Every brief carries

- **The task section, copied in.** Not "see task 7 in the plan" -- the actual
  text of that task, pasted into the brief.
- **File anchors as grep patterns**, never line numbers -- a whole-branch
  review re-verifies citations against the real tree, and line numbers drift
  the moment anyone else touches the file.
- **The ledger rulings that bind this task** -- prior decisions, with their
  cost-if-wrong, that the subagent must not silently re-litigate.
- **The worktree path, and an explicit line forbidding the root checkout.**
  Every delegated prompt that edits files names both (`AGENTS.md`,
  "Subagent-driven development").
- **The private test-database name** this subagent's runs must use --
  never a bare shared name.
- **The exact gate command** for this task's scope (a single module's pytest
  invocation, or a pointer to `.claude/skills/test-ladder/SKILL.md` for the
  full ladder).
- **The tier chosen, and why** -- cheapest capable tier by default; note what
  would justify escalating.
- **The report shape expected back**: the SHA it left, test counts (not just
  "passed"), and any deviation from the brief with its reason.

## Briefs that ask for real-state verification against a shared resource

A brief that says "verify this for real against the live database/port" without saying HOW is an
invitation to evade the containment it's implicitly assuming. A constraint written about the
usual way of doing a thing (run the suite, which takes the slot lock) is silently evaded by an
unusual way of doing the same thing (call the function directly, which doesn't) -- this happened
here: standalone verification against a shared Postgres port ran outside the slot lock the code
under test itself exists to enforce, and its clean result proved nothing about interference the
lock is specifically there to prevent. So brief this shape structurally, not by asking nicely:
**one foreground script that acquires the slot, runs the exercise, and releases** -- the
verification runs under the exact containment it is verifying, the same way the code path it's
checking would. See `.claude/skills/test-ladder/SKILL.md` ("The lock protects the resource, not
the ceremony of running a suite") for the case that found this.

## Reviewer briefs additionally carry

- **BASE and HEAD SHAs** -- a reviewer diffs those exact commits, not "the
  branch" as of whenever it reads it.
- **The verdict vocabulary**: findings are Critical / Important / Minor / Nit;
  the review's own verdict is APPROVE / FIX / ALL ADDRESSED. A reviewer that
  invents its own severity words produces a report the orchestrator has to
  re-translate before acting on it.

## Failure modes

- "Read the plan and do task 7" -- the plan path alone. The subagent burns
  its first turns re-deriving what the brief should have stated.
- Line-number anchors that are already stale by the time a second subagent
  reads the same brief.
- A report that says "done" with no SHA or test count -- nothing to verify
  against.

## See also

`AGENTS.md` "Subagent-driven development"; `.claude/skills/test-ladder/SKILL.md`
for the gate command a brief points at; `.claude/skills/pr-body/SKILL.md` for
how a finished task's evidence flows into the eventual PR.
