---
name: test-ladder
description: Use when running the pre-merge test gate on a feature branch, or the shorter hotfix gate -- module-order runs, the feature-flag matrix, posture sweeps, and the migration/check pair.
---

# Test ladder

## Purpose

Module order, flag state and posture leak between tests via module-global registries; a single
green `pytest` proves little. `scripts/ladder.py` runs the whole gate as one citable record.

## The runs

**Full mode** (8 named runs with touched modules given, 6 without). `pytest.ini`'s testpaths
order is already `FULL_MODULES` reversed, so no run is a bare `pytest -q` -- every run below
states its module list explicitly:

| Run | `FARABUNKER_FEATURES` | `FARABUNKER_TEST_POSTURE` | Modules |
|---|---|---|---|
| `r1-forward` | `vision,media` | -- | `scripts identity agents foundation models tools` |
| `r1-reverse` | `vision,media` | -- | reversed |
| `r2-v-forward` | `vision` | -- | full module list |
| `r2-v-reverse` | `vision` | -- | reversed |
| `r2-vm-scoped` | `vision,media` | -- | branch's touched modules |
| `r2-v-scoped` | `vision` | -- | branch's touched modules |
| `r3-personal` | `vision,media` | `personal` | full module list |
| `r3-enterprise` | `vision,media` | `enterprise` | full module list |

No touched modules given drops `r2-vm-scoped`/`r2-v-scoped` (they'd just duplicate the reverse
runs) and prints one stderr line saying so.

Then, always: `manage.py makemigrations --check --dry-run`, then
`manage.py check`. `FARABUNKER_TEST_POSTURE` is the env var
`identity/testing.py`'s `seed_sweep_posture()` reads (re-exported by
`identity/tests/_helpers.py`) and `docs/DEV.md` §8 rung 1 documents; r3 is
required before merging identity, posture, or visibility work (any change
touching `identity/`, or a visibility function elsewhere) and before any
release -- not a per-change gate.

**Hotfix mode** (2 named runs): `r1-forward` and `r1-reverse` only, then the
same trailing `makemigrations --check --dry-run` and `check` pair.

## Running it

```bash
scripts/ladder.py <worktree> <db_url> <outdir> full [--max-others N] <touched-module>...
scripts/ladder.py <worktree> <db_url> <outdir> hotfix
```

- `<db_url>` must name a database nobody else is using -- **never two pytest
  runs on one database name.** A shared name has produced a false red before
  (`pytest-django` creates/drops it per run; two runs racing that lifecycle
  corrupt each other's result).
- Each run writes `<outdir>/<run>.log`, appends
  `<run> | <returncode> | <last summary line>` to `<outdir>/SUMMARY`, and the
  script touches `<outdir>/LADDER_DONE` once every run (the trailing pair
  included) has finished. The process exits non-zero if any run's
  returncode was non-zero.
- Before every pytest run (not the trailing `makemigrations`/`check` pair) the script waits for
  at most `--max-others` other pytest processes machine-wide, printing
  `waiting: N other pytest processes` to stderr every 60s. It counts by listing processes with
  one `ps` call and matching in Python -- a line counts only when its executable is a Python
  interpreter and its arguments mention the runner, and its own pid and process tree are
  excluded -- never by shelling out with the pattern on the command line it then searches,
  which used to make the watcher count its own reflection. Default `1` -- AGENTS.md's "at most
  two full suites" (this run plus one other); pass `0` for a peer-agreed stricter cap.
- **Pausing** is `kill -STOP <ladder.py's own pid>` (not its process group).
  Its in-flight pytest subprocess is a separate process and keeps running to
  completion regardless -- the result still lands in that run's `.log` and
  gets appended to `SUMMARY` the moment `SIGCONT` lets the parent resume and
  move to the next run.
- Runs sequentially, in the table's order; do not parallelize runs against
  the same `<db_url>`.

## Sharing the machine

The wait is advisory, not mutual exclusion -- checking the count and starting the run are two
separate steps with a gap between them, so two sessions can both see a clear machine in the
same instant and start together. Explicit handover -- saying out loud what you're about to run
and when, and correcting it when that changes -- is therefore the protocol, not a politeness:
it is what stopped and restarted a chain here when the count alone would not have, and it is
the part that survives whoever eventually lands a lock, since a lock only says the machine is
taken, never for how long or what to do instead. Next step, not yet built: a lock file created
exclusively (so the loser of a race fails to create it rather than reading stale state),
carrying the holder's pid and a timestamp, with a waiter treating a missing holder or an
implausibly old timestamp as stale and taking it -- it would turn "I looked and nobody was
running" into "I hold the only token", giving every session one place to see who holds the
machine and since when.

## Trusting the matcher

A process check isn't proven by reading it. Five different versions of this predicate got
written in one day across two sessions, each looking obviously correct, each a sharper
exclusion than the last -- sharpness is exactly what didn't help. Only the two that were
actually run against a machine in a known state, once with a real pytest run present and once
confirmed absent, ever gave a right answer. Whoever changes `count_other_pytest` owes it the
same before trusting it: run it both directions against known ground truth -- the check's own
output is the evidence, not the reasoning that produced it.

## Failure modes

- Pointing `<db_url>` at a shared/bare `test_farabunker` -- false reds.
- Treating a `SUMMARY` line with a nonzero returncode as "still running" --
  it means that run failed; read its `.log`.
- Sending `SIGSTOP` to the process group (e.g. `kill -STOP -<pgid>`) instead
  of the pid -- that also stops the in-flight pytest, which is not what
  "pause" means here.
- Citing `LADDER_DONE`'s existence as "green" -- it means the ladder
  *finished*, not that every run passed. Read `SUMMARY`.
- Matching on arguments alone (a sharper pattern instead of an executable check) once reported
  the machine busy on an idle machine, all day, in both directions -- phantoms counted as busy
  is the direction that starves a peer, and explicit handover carried the real coordination
  while it stood undetected. The remedy is not a sharper eye, it is running the check and
  reading its output.
- A peer session's own runner log made both exclusions concrete in two lines: it reported
  waiting on three other test processes, then on one, the moment it killed its own watchers --
  three counted, one real, two of them its own reflections. That's why the executable check and
  the own-process-tree exclusion are each load-bearing alone, not belt and braces: the first
  stops the reflections, the second stops a runner refusing to start because it can see itself
  working.

## See also

`docs/DEV.md` §8 rung 1 for why order and flag state matter and the exact
commands this table is built from; `AGENTS.md` "Quick reference" for the
four-run subset every ordinary change runs by hand when the script isn't
warranted; `.claude/skills/pr-body/SKILL.md` for how a finished ladder run
gets reported in a PR's `## Gate` section.
