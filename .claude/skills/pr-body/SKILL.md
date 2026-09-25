---
name: pr-body
description: Use when writing a pull request description in this repository -- the shape a reviewer and the owner expect, and what evidence each section requires.
---

# PR body

## Purpose

One shape, every PR, so a reviewer and the owner know where to look for each kind of claim
rather than re-deriving it from prose.

## Sections, in order

**`## What`** -- the gap this closes and what changes, grouped by file group
(not a flat file list). State the *why* in a sentence or two per group, not
just the filenames.

**`## Evidence`** -- measured before/after, wherever behavior changed. A
number, a screenshot description, an error message that stopped appearing --
not "should now work."

**`## Gate`** -- the review verdict, plus the ladder result:
- Each ladder run as `N passed / M xfailed` (see
  `.claude/skills/test-ladder/SKILL.md`), named (`r1-forward`, `r2-v-scoped`,
  ...), with the SHA the ladder ran at.
- `makemigrations --check --dry-run` and `check`, each with its result.
- Migrations: list them by name, or write "No migration."

**`## After merge (owner's word first)`** -- the deploy shape, per
`.claude/skills/deploy-window/SKILL.md`: what lands, whether it needs a
migration or a restart, and that it waits on the owner's merge word.

**Attribution footer** -- the harness's required trailer, verbatim, exactly
as the harness supplies it for this session. Never paraphrased, never
omitted.

## The owner's word

Nothing merges or deploys without the owner's merge word, given in the acting session's own
conversation (`AGENTS.md` "Merge readiness" / "Definition of done") -- a relayed "the owner
said" is not authorization.

## Failure modes

- Citing the ladder as "tests pass" instead of `N passed / M xfailed` per
  named run -- a passing count with no SHA cannot be re-verified later.
- Writing `## After merge` as if merge is already authorized because the
  ladder is green -- green tests are a precondition for asking, not the
  asking.
- Omitting "No migration" when there genuinely are none -- an empty section
  reads as "forgot to check," not "checked, none."

## See also

`.claude/skills/test-ladder/SKILL.md` for what a named run and its log
represent; `.claude/skills/deploy-window/SKILL.md` for the after-merge
sequence this section summarizes; `AGENTS.md` "Merge readiness" for the
checklist a PR must satisfy before it is even opened.
