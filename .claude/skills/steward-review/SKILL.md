---
name: steward-review
description: Use when a peer session's PR touches a column you steward, before it merges -- clearing a cross-column diff packet, checking row-vs-content visibility, import law, guard coverage, and doc truth.
---

# Steward review

## Purpose

A column has an owning session (a "steward") for its branch of work; when another session's
change reaches into it, the steward clears it **before** the peer's PR merges -- never a post-merge ping.

## The packet

The peer sends, before merging: the diff of the hunks that touch the
stewarded column, plus the caller-side source as text (the code that calls
into the steward's column, so the steward can see the call site, not just the
seam). The steward reads it personally -- not by delegating the read to a
subagent that reports back a verdict the steward never saw the diff for.

The packet's summary always names a new model field, a new migration, and
any new key added to a payload that crosses the column boundary, even when
the author believes them incidental -- a summary can be true of the shape a
function returns and still hide the field, migration, or payload key the
steward is actually being asked to clear.

## What to check, every time

1. **Row visibility vs. content visibility -- never let one stand in for the
   other.** `identity.access.is_admin` decides which rows exist for a
   principal at all; `identity.access.sees_all_content` (and
   `models.queue.visibility.may_read_job_content`, which composes it) decide
   what *text* may be shown once a row is visible. A caller that checks
   `is_admin` and then renders content has skipped a check --
   `models/queue/visibility.py`'s own module docstring states this split
   ("ROWS ARE `is_admin`; CONTENTS ARE `sees_all_content`") because getting
   it backwards is the actual, recurring failure mode.
2. **Import law.** A column imports downward only (`identity` -> `foundation`,
   never sideways or up; a tool reached only through the registry's one
   sanctioned door). Grep the diff's new imports against
   `foundation/ops/tests/test_import_law.py` and
   `foundation/ops/tests/test_column_boundaries.py` -- both are the gates
   that fail the build on a violation, so a clean local run of them on the
   peer's branch is a precondition, not optional evidence.
3. **Guard-test coverage of the new seam.** If the diff adds a new function a
   caller reaches across the boundary, a guard test exercising that reach
   exists in the stewarded column's own `tests/`, not only in the caller's.
4. **Doc truth.** The stewarded column's `README.md` states its own import
   direction and any invariant the diff touches; if the diff changes
   behavior the README claims, the README moves in the same PR.

## Verdict

One of:

- **CLEAR** -- nothing further needed.
- **CLEAR WITH CONDITIONS** -- each condition names the pin test that will
  catch a regression of it (a condition with no test behind it is a hope,
  not a gate).
- **FINDINGS** -- blocking. Each finding states the cost if wrong (what
  breaks, for whom, and how visibly) so the peer can weigh it against
  schedule pressure honestly.

## Failure modes

- Reviewing the diff without the caller-side source -- you cannot tell
  whether a seam is used correctly from the seam's own diff alone.
- Checking `is_admin` where the code actually needs `sees_all_content` (or
  vice versa) because the two read as interchangeable at a glance.
- Clearing on a promise ("we'll add the guard test after merge") -- the
  packet is reviewed as it stands, not as it is promised to become.
- Reporting an intention as a fact -- summarizing what a change was meant to
  do instead of what the diff shows it did.

## See also

`AGENTS.md` "Working alongside other sessions" (zones and slices); `identity/
access.py` and `models/queue/visibility.py` for the row/content split in
context; `.claude/skills/pr-body/SKILL.md` for where a steward's CLEAR
verdict gets cited in the PR that merges.
