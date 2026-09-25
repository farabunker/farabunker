---
name: test-ladder
description: Use when running the pre-merge test gate on a feature branch, or the shorter hotfix gate -- module-order runs, the feature-flag matrix, posture sweeps, and the migration/check pair.
---

# Test ladder

## Purpose

Module order, flag state and posture leak between tests via module-global registries; a single
green `pytest` proves little. `scripts/ladder.py` runs the whole gate as one citable record.

## The runs

**Full mode** (8 named runs, in order). `pytest.ini`'s own testpaths order
is already `FULL_MODULES` reversed, so there is no separate "bare `pytest
-q`" run -- every run below states its module list explicitly:

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
- Before every pytest run (not the trailing `makemigrations`/`check` pair)
  the script waits, polling `pgrep -f pytest`, until at most `--max-others`
  other pytest processes are running machine-wide, printing
  `waiting: N other pytest processes` to stderr every 60s. Default is `1` --
  AGENTS.md's "at most two full suites across the machine" (this run plus
  one other); pass `--max-others 0` when peers have agreed a stricter cap
  for a period.
- **Pausing** is `kill -STOP <ladder.py's own pid>` (not its process group).
  Its in-flight pytest subprocess is a separate process and keeps running to
  completion regardless -- the result still lands in that run's `.log` and
  gets appended to `SUMMARY` the moment `SIGCONT` lets the parent resume and
  move to the next run.
- Runs sequentially, in the table's order; do not parallelize runs against
  the same `<db_url>`.

## Failure modes

- Pointing `<db_url>` at a shared/bare `test_farabunker` -- false reds.
- Treating a `SUMMARY` line with a nonzero returncode as "still running" --
  it means that run failed; read its `.log`.
- Sending `SIGSTOP` to the process group (e.g. `kill -STOP -<pgid>`) instead
  of the pid -- that also stops the in-flight pytest, which is not what
  "pause" means here.
- Citing `LADDER_DONE`'s existence as "green" -- it means the ladder
  *finished*, not that every run passed. Read `SUMMARY`.

## See also

`docs/DEV.md` §8 rung 1 for why order and flag state matter and the exact
commands this table is built from; `AGENTS.md` "Quick reference" for the
four-run subset every ordinary change runs by hand when the script isn't
warranted; `.claude/skills/pr-body/SKILL.md` for how a finished ladder run
gets reported in a PR's `## Gate` section.
