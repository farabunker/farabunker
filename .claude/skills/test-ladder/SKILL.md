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
scripts/ladder.py <worktree> <db_url> <outdir> full [--max-others N] [--expect-minutes N] <touched-module>...
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
  at most `--max-others` other pytest processes machine-wide. It lists processes with one `ps`
  call and matches in Python -- a line matches only when its executable is a Python interpreter
  AND some argument token IS the runner outright (its own basename is exactly `pytest`, or it
  forms the `-m pytest` pair), and its own pid and process tree are excluded -- never by
  shelling out with the pattern on the command line it then searches (a watcher counting its
  own reflection), and never by matching the word anywhere in the text (a real Python process
  whose path, `--outdir`, or `--db-name` merely contains it -- any token with "=" is skipped
  before the basename check, since an executed runner token never has one). Two narrower
  residuals stay open on purpose (see `_runner_token_index`'s docstring): a bare positional path
  argument ending in a component named `pytest`, and a transient invocation naming `pytest` as a
  bare word (a package install) without running it -- both are safe-direction phantoms, not
  worth a positional-vs-flag-value parser. Every 60s it prints one line naming each match by pid
  and a trimmed invocation, not just a count -- `waiting: N other pytest processes --
  <pid>:<label>, ...` -- because a count can't say whose run is holding the machine or how far
  along it is. Default `1` -- AGENTS.md's "at most two full suites" (this run plus one other);
  pass `0` for a peer-agreed stricter cap.
- The script also holds the slot lock for its whole chain (see "Sharing the machine" below) --
  `--expect-minutes` states how long that chain expects to take (default `120` full / `20`
  hotfix); it is written into the lock so a peer can decide between waiting and doing something
  else, not enforced as a timeout.
- **Pausing** is `kill -STOP <ladder.py's own pid>` (not its process group).
  Its in-flight pytest subprocess is a separate process and keeps running to
  completion regardless -- the result still lands in that run's `.log` and
  gets appended to `SUMMARY` the moment `SIGCONT` lets the parent resume and
  move to the next run.
- Runs sequentially, in the table's order; do not parallelize runs against
  the same `<db_url>`.

## Sharing the machine

The wait alone is advisory, not mutual exclusion -- checking the count and starting the run are
two separate steps with a gap between them, so two sessions could always see a clear machine in
the same instant and start together. `acquire_lock`/`release_lock`/`read_lock`/`is_stale`
(`LOCK_PATH`) close that gap for any session that adopts them: one machine-wide token, created
exclusively (the loser of a race FAILS TO CREATE rather than reading stale state and proceeding
anyway). AUTHORITATIVE for a session that acquires it; the process check above stays a
SECONDARY ADVISORY, for a run started by a session that has not adopted the lock.

`LOCK_PATH` is `/tmp/farabunker-test-slot.lock`, LITERAL -- the fleet's own path, byte for byte,
not a name this script invented and not one derived from `tempfile.gettempdir()` (which resolves
somewhere else entirely on this platform, and drops the `.lock` suffix). Landing on their path
rather than asking them to move to ours means `read_lock` has to understand their plain
`key=value` line too, not just the structured (JSON) format this script writes. A lock that
parses in EITHER format is a live holder and must never be treated as vanished merely because
this script doesn't natively write that shape -- naive unification (read JSON, return None on
anything else) is DANGEROUS, not just incomplete, because None routes straight into the
vanished-lock steal and would unlink a live holder in the other format.

THE FLEET'S FIELD CONTRACT, copied from a live lock on the review machine rather than
reconstructed from a description of it (see "Trusting the matcher" below for why that distinction
is the whole point):

```
session=<name> pid=$$ purpose=<hyphenated-what> started=<ISO-8601 UTC, trailing Z> expect_min=<N>
```

`session` aliases to `holder`, `purpose` to `running`, `expect_min` (INTEGER MINUTES, not seconds)
times sixty to `expected_seconds`, and `started` is an ISO-8601 UTC instant with a trailing `Z`
(`date -u +%FT%TZ`), not an epoch float. `pid` is the ONE process field, deliberately: it's the
shell that acquires, runs and releases in a single call, so its death means the hold ended. A
two-process variant (a supervisor pid alongside the suite's own) existed for about half an hour
while a peer repaired a dead identifier mid-run and is gone from the current line -- this parser
does not look for it, and does not need to, because it IGNORES UNKNOWN KEYS rather than failing
on them. That's deliberate tolerance, not sloppiness: a parser that skips fields it doesn't
recognise would have read the now-gone two-process variant correctly by itself, containing that
entire class of break without a code change.

ROLLOUT RULE, both halves, because a lock introduced mid-flight is a failure mode that looks
exactly like the protocol working: while some sessions still run without the lock, its ABSENCE
is NOT evidence the machine is idle -- a pre-lock session can genuinely hold the machine with no
lock file ever written, and seeing that holder is exactly the process check's remaining job.
Only once every session acquires the lock does "no lock file" mean idle, and only then does the
process check stop being load-bearing. The database probe below (`_db_activity_present`) gives
this rule a SECOND clause: retirement now needs every holder writing a token AND the pid field
being trustworthy (see the comment where `pid` is written in `acquire_lock`) -- not the first
clause alone, since a token that names a dead-on-arrival process is not the rollout completing,
it's the same defect wearing a lock's clothes.

A lock is stale -- and stolen, with an announcement to stderr first, never silently -- when its
holding process is gone, or its start time is more than 3x its own stated `expected_seconds` in
the past, AND (see "Staleness is a conjunction" below) the database probe finds no activity to
contradict that. A well-formed-but-wrong-shaped lock (a missing `pid`/`started`/
`expected_seconds`) is judged stale rather than crashing acquisition -- hardened field reads
throughout, not a happy-path assumption. Stale-detection is the flimsiest part of this whole
scheme and is deliberately the LAST line of defence, not the first: `main` arms the release
handlers the instant `acquire_lock` returns, before anything else in the run gets a chance to
fail and leave the lock waiting to go stale instead of being released properly.

Stealing CAPTURES before it JUDGES, never the reverse -- and this took two rounds to get right,
which is itself the lesson (see "Trusting the matcher" below): judging a lock via a read and
THEN acting on it with a separate rename/unlink leaves a gap where the content can change between
the two steps, so a second thief can act on a live lock it never actually judged. The fix renames
UNCONDITIONALLY to a uniquely-named tombstone first, and only then reads and judges the CAPTURED
copy -- nothing else can be racing for a tombstone only one call named, so the gap closes
entirely rather than narrowing. A version that only fixed "unlink after judging" to "rename after
judging" still had this bug one level up, and a single green test run did not catch it -- it took
a stress loop (dozens of repeated real-thread runs) to surface on iteration 33 of one attempt. A
cheap PRE-READ GATE runs before the capture too (judging, and refusing, on an ordinary read) so an
obviously-live lock is almost never captured in the first place -- it is an optimization on top of
capture-then-judge, not a replacement for it, since the pre-read can itself go stale before the
capture actually runs.

RESTORING a captured lock that turns out to be live uses LINK-then-unlink, never rename: the path
sits EMPTY for the whole capture window, so a plain acquirer's exclusive create can legitimately
succeed there while this call still holds the tombstone -- renaming the tombstone back would
silently REPLACE that third party's fresh, genuine claim. `link()` raises `FileExistsError`
instead of overwriting, so that collision is caught (announced LOUDLY, naming both parties,
refused) rather than clobbered. A capturer killed between capturing and restoring dispossesses a
live holder silently and orphans its tombstone forever -- not fully fixable, only mitigated: the
pre-read gate above shrinks how often it can happen, and `acquire_lock` sweeps for orphaned
`.captured-*`/`.tmp-*` artifacts (announcing each one's age, never auto-removing) so the wreckage
stays visible instead of accumulating unseen. That sweep can false-positive on a genuinely
concurrent, still-live acquisition -- a warning at a few seconds' age during a real race is noise;
one at minutes' age is real.

A lock UNPARSEABLE IN BOTH FORMATS is HELD, never vanished -- "we can't tell" is not "nobody's
there". This applies at the pre-read gate (refused before ever capturing) and again on the
captured copy, in case the content changed into garbage between the two reads; either way it is
restored/left alone and refused, never stolen with just the generic line the way an earlier
version silently did.

Expected duration is FLOORED, not trusted as given: `expected_seconds` below
`_MIN_EXPECTED_SECONDS` (60s) would make a lock's own 3x budget tiny too, so a live chain could
read as stale to a waiter moments after acquiring -- floored both where this script writes it
and defensively wherever a foreign lock's own value is read back.

NOTE, LOUDLY: release happens on normal exit, on SIGINT, and on SIGTERM -- NEVER on SIGSTOP.
This runner's own pause mechanism (below) is SIGSTOP, and a paused run STILL OWNS THE MACHINE --
a stop that freed the lock would hand the box to a peer while a suite sits frozen holding it.
SIGSTOP can't be caught by any handler regardless, by the OS's own design, so there's no
accidental path to releasing on pause even if this forgot to be careful about it.

Explicit handover -- saying out loud what you're about to run and when, and correcting it when
that changes -- remains the protocol underneath all of this, not a politeness: it is what
stopped and restarted a chain here when a count alone would not have, and it survives the lock
too, since the lock only says the machine is taken, never for how long or what to do instead --
its `running`/`expected_seconds` fields narrow that gap but are a stated expectation, not a
guarantee.

## Staleness is a conjunction, not a proxy

A process id and an age both only answer "does the holder LOOK alive" -- neither one measures
the resource the lock actually exists to protect. `_db_activity_present` is the first staleness
signal here that asks the database ITSELF whether anything is connected and active on the
recorded `db_port`, rather than reading a proxy for that and hoping. So a lock is stolen only
when the process looks gone or aged out AND the probe finds no contradicting activity -- never
on the process signal alone while a real answer is available. DIRECTION, stated as a MIRROR IMAGE
of today's arrangement rather than a replacement for it, and this is the corrected version of an
earlier note that had it backwards: the probe could eventually become PRIMARY, since it measures
the resource, but the process id and the age do not then retire -- they become the resolver of
THE PROBE'S OWN STUCK CASE. A live peer lock verified against this exact probe showed why: the
connection it saw was IDLE IN TRANSACTION, not active -- exactly what the probe should catch (a
live suite paused between tests), but in a SINGLE SAMPLE that state is indistinguishable from a
CRASHED run that left a transaction open, and an abandoned connection sits there indefinitely. A
conjunction that trusted the probe alone would read busy forever and wedge the machine on
exactly the input it exists to interpret; the process clause -- for all that it lies today about
a harness that splits acquire from run -- is the thing that would eventually notice the crash and
release it. Closing this needs either two samples a minute apart (activity that persists is a
live suite; activity that vanishes was a snapshot of a transaction already gone) or watching the
connection's state change rather than sampling it once. Not now, mid-rollout -- but the reason is
worth keeping, because every defect this week came from measuring a proxy and believing it, and
the fix for that is not "trust the resource signal instead" but "know what each signal is FOR".

Degradation is RULED, not improvised: if the probe can't run at all (`psql` missing, no
`db_port` ever recorded, the connection attempt itself refused or timed out), this does not
silently fall back to the process-only check and does not wedge the machine either -- it steals
on the process evidence alone, but ANNOUNCES that it is doing so on evidence that can be
STRUCTURALLY invalid, not merely weak, before doing it. A session reading that line can stop it;
a silent fallback cannot be stopped by anyone. The probe authenticates with the documented local
development password (`docs/DEV.md`'s `farabunker`/`farabunker`) as its default -- on a
password-secured box where that default is wrong, every probe auth-fails and the conjunction
degrades PERMANENTLY, always taking the warned-and-proceed path rather than ever confirming
clear. `PGPASSWORD` in the environment overrides it; nothing here detects the permanently-degraded
state itself.

THE PID FIELD'S OWN DANGER, pinned as a comment at the exact line it's written in
`acquire_lock` (not here, and not in the module docstring, because an editor of the call site
would not see either): the field is meaningful only if the process that writes it is the SAME
process that holds the lock for the whole run. In an agent harness, EVERY SHELL CALL IS A
SEPARATE PROCESS -- acquiring the lock in one call and running the suite in a second call cannot
ever satisfy this, because the writing process is gone before the run it was supposed to
describe even starts. That is not a race, not clock drift, not an overrun: a lock built this way
is BORN stealable with nothing having gone wrong, which is what makes the field actively
misleading rather than merely unreliable. This script's own `main()` satisfies the requirement
(acquire and the whole run to `release_lock` share one process, verified by reading it) --
whoever changes the call site must re-verify this, not assume it, and the specific way to break
it is splitting acquisition into a separate setup step.

THE RULE HAD A HOLE: "acquire, run and release in one call" is not enough on its own, because a
run BACKGROUNDED from that call satisfies the words while destroying the purpose -- the acquiring
shell reaches its own end and exits immediately, leaving a live suite behind a lock that already
names a dead process. This is exactly what makes a rule dangerous: it is easy to write while
genuinely believing it is being followed. The rule as it actually has to read: acquire, run and
release in ONE SHELL, AND THE RUN MUST BE IN THE FOREGROUND OF THAT SHELL -- not backgrounded
(`&`), not detached, not handed to a supervisor that returns before the run ends.

Two general lessons this saga produced, worth stating outside this file's own case:

- **A record can be simultaneously informative and unsafe.** The peer lock this defect was found
  on was right about everything a human reads -- holder, purpose, since when -- and wrong about
  the one field a machine acts on. Reviewing a record by reading it cannot catch a defect in a
  field only a machine reads, because the parts a human checks are exactly the parts that were
  correct. MACHINE-CONSUMED FIELDS NEED MACHINE-EXECUTED CHECKS -- the same negative-control
  discipline the documentation gate already applies, aimed at lock files instead of prose.
- **A fixture for a foreign format must be COPIED from the foreign artefact, never reconstructed
  from its description.** This file's own dual-format tests failed to catch F1 for exactly this
  reason: their fixtures wrote this script's OWN schema transliterated into key=value form,
  which is a test built from the same misunderstanding as the code, and a machine-executed check
  running the wrong thing cannot fail -- the one way the remedy just above can itself go wrong.
  Copy the bytes.
- **A lock's evidence must have the same lifetime as the thing it claims.** Every safety property
  added this round was defeated by something outliving or predating what it measured -- a
  watcher outliving its purpose, a lock predating its protocol, a process dying before its run
  ended. The starting question for reviewing any guard, before anything else: what resource does
  this protect, and does anything here actually MEASURE it -- a check that verifies that finds
  the whole class in one pass; a check that assumes it finds nothing, by construction.

## Trusting the matcher

A process check isn't proven by reading it. Five different versions of this predicate got
written in one day across two sessions, each looking obviously correct, each a sharper
exclusion than the last -- sharpness is exactly what didn't help. Only the two that were
actually run against a machine in a known state, once with a real pytest run present and once
confirmed absent, ever gave a right answer. Whoever changes `count_other_pytest` owes it the
same before trusting it: run it both directions against known ground truth -- the check's own
output is the evidence, not the reasoning that produced it. Do not count matching processes;
read their arguments and say which suite is running -- a count cannot tell you whose run it is,
and whose run it is turns out to be the thing every session actually needs to know.

The same disease shows up outside this file, and it's the same lesson, not a separate one: a
process-name match that can never match its actual target (a daemon that runs under a different
name entirely) is an OPINION about state, not a check of it, and it was reported as truth here
more than once. A socket that refuses a connection, or a request that times out, is EVIDENCE --
it asked the thing itself rather than guessing from a name nobody ran against a known-good case.
Prefer asking the thing directly over pattern-matching a name for it whenever the target can be
asked.

## Standing rules

- **No private guard variants.** Five different versions of the other-pytest predicate got
  written in one day across two sessions, and one nearly shipped to every implementer here --
  one canonical, reviewed, tested matcher, or the same phantom comes back wearing someone else's
  copy of it.
- **No background watcher whose command line mentions the runner.** One blocked its own
  session's chain -- the pattern must never travel on a command line anything is watching,
  including that watcher's own.
- **A process check is untrusted until it has been run in both directions against a machine
  known busy and known idle.** Its own output is the evidence; a check that only reads correctly
  has proven nothing.
- **Announce identity and expected duration, never a bare count.** A count can't say whose run
  is holding the machine or how much longer it has -- that's exactly what the lock's fields
  (holder, pid, running, started, expected_seconds) exist to answer instead.
- **A void run stops immediately when the database is absent or refusing**, rather than grinding
  through the whole ladder producing hours of connection-refused noise -- that failure mode
  costs the slot twice, the wasted run and the lock held the entire time it ran for nothing.

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
