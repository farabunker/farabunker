#!/usr/bin/env python3
"""scripts/ladder.py -- the pre-merge test ladder, as one runnable script.

See .claude/skills/test-ladder/SKILL.md for the full procedure this encodes.

Usage:
    scripts/ladder.py <worktree> <db_url> <outdir> <full|hotfix> [--max-others N] [--expect-minutes N] [touched-modules...]

Each named run writes <outdir>/<run>.log, appends
"<run> | <returncode> | <last summary line>" to <outdir>/SUMMARY, and
<outdir>/LADDER_DONE is touched once every run (the trailing
makemigrations/check pair included) has finished. Exits non-zero if any
run's returncode was non-zero.

One test database per runner: point <db_url> at a database name nobody else
is using (docs/DEV.md rung 1) -- never two pytest runs sharing one database
name, which has produced a false red before.

Before every pytest run (not before the trailing makemigrations/check pair)
this waits until at most --max-others other pytest processes are running
machine-wide. It lists processes with one `ps` call whose own command line
never names "pytest" (so it can never match itself or be matched by a peer
running the same check), then matches in Python: a line counts only when
its executable is a Python interpreter AND some argument TOKEN is the
runner outright -- its own basename is exactly "pytest", or it forms the
"-m pytest" pair -- so neither a shell/grep that merely has "pytest" in
its arguments (its own polling command, another session's wait loop) nor
a real Python process whose path/outdir/db-name merely contains the word
is ever counted. This process's own pid and its whole process tree (an
in-flight pytest run it launched itself) are excluded from the count.
# ponytail: executable+token match, not a lock -- a false positive just
# costs a few extra seconds of waiting, never a wrong result.
Default --max-others is 1 (AGENTS.md: at most two full suites across the
machine, this run plus one other); pass --max-others 0 when peers have
agreed a stricter cap for a period. A line is printed to stderr every 60s
while waiting, naming each match by pid and a trimmed invocation rather
than just a count -- "waiting: N other pytest processes -- <pid>:<label>,
...": a count can't say whose run it is holding the machine or how far
along it is, and that's what a waiting session actually needs to know.

The slot lock (LOCK_PATH, acquire_lock/release_lock/read_lock/is_stale):
one machine-wide token, exclusively created (os.O_EXCL -- the loser of a
race FAILS TO CREATE rather than reading stale state), held for a whole
chain. AUTHORITATIVE for any session that adopts it; the process check
above stays a SECONDARY ADVISORY for a run started by a session that has
not. ROLLOUT RULE, both halves: while some sessions still run without the
lock, the ABSENCE of a lock file is NOT evidence of an idle machine --
that is exactly the process check's remaining job, to see a holder who
has not yet adopted the lock. Only once every session acquires it does
"no lock file" mean idle, and the process check stop being load-bearing.

Staleness is a CONJUNCTION, not the process-and-age check alone: a lock
whose process looks gone is stolen only after the database probe
(_db_activity_present) also finds no activity on its recorded db_port --
a process id and an age both only measure whether the HOLDER looks alive,
never whether the database it's protecting is actually in use, which is
what a lock exists to protect in the first place. The pid field's own
comment in acquire_lock explains the harness-specific way that field can
be structurally meaningless rather than merely stale (see it before
touching how or where that field gets written).

NOTE, LOUDLY: this script's own pause mechanism is SIGSTOP (below), and a
paused run STILL OWNS THE MACHINE. The lock is released on normal exit,
on SIGINT and on SIGTERM -- NEVER on SIGSTOP. A stop that freed the lock
would hand the machine to a peer while a suite sits frozen holding it.
Nothing here even tries to hook SIGSTOP: the OS never lets a handler
catch it, by design, so there is no accidental path to releasing on pause.

Pausing: SIGSTOP this script's own pid (not its process group). Its
in-flight pytest subprocess is a separate process and keeps running to
completion regardless -- the result lands in that run's .log and gets
appended to SUMMARY normally the moment SIGCONT lets the parent resume.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

FULL_MODULES = ("scripts", "identity", "agents", "foundation", "models", "tools")

# The slot lock: a single machine-wide token, exclusively created, that a
# session holds for its whole chain. AUTHORITATIVE for any session that
# acquires it -- the process check above (other_pytest_matches et al.)
# remains a SECONDARY ADVISORY only, for a run started by a session that
# has not adopted the lock. Neither replaces the other yet: the lock gives
# true mutual exclusion (one holder, ever) where it's used; the count-based
# wait still runs underneath it and is all a non-adopting session gets.
# ROLLOUT RULE (module docstring has the full text): mid-rollout, no lock
# file does NOT mean idle -- a pre-lock session can genuinely hold the
# machine with none written. That's the process check's remaining job.
#
# This is the path the four sessions agreed on by hand, not a name this
# script invented -- landing on their path (rather than asking them to
# move to ours) is why read_lock also has to understand their plain
# key=value format below: two tokens for one machine is worse than one
# token in a format this script had to learn.
LOCK_PATH = Path(tempfile.gettempdir()) / "farabunker-test-slot"


class LockHeld(Exception):
    """Raised by acquire_lock when the lock is held by another live,
    non-stale session. str(exc) names who holds it and what they're
    running, for a caller to print and give up on rather than retry in a
    loop -- looping here would just be the poll's race window rewritten
    under a lock's name."""


def build_run_plan(mode: str, touched_modules: list[str]) -> list[dict]:
    """The named runs for `mode`, in order. No subprocess -- scripts/tests/
    test_ladder.py imports this directly. Its only I/O is the one stderr
    line below when the scoped pair is dropped.

    pytest.ini's own testpaths order ("tools models foundation agents
    identity scripts") already IS FULL_MODULES reversed, so a bare
    `pytest -q` with no path args is not a distinct third order -- every
    run below states its module list explicitly instead.

    With no touched modules, `r2-vm-scoped`/`r2-v-scoped` would run the
    exact same module list as `r2-v-reverse`/`r1-reverse` -- a silent
    duplicate, not a distinct run -- so that pair is dropped instead."""
    runs = [
        {"name": "r1-forward", "features": "vision,media", "posture": None, "modules": FULL_MODULES},
        {"name": "r1-reverse", "features": "vision,media", "posture": None, "modules": tuple(reversed(FULL_MODULES))},
    ]
    if mode == "hotfix":
        return runs
    scoped = tuple(touched_modules)
    runs += [
        {"name": "r2-v-forward", "features": "vision", "posture": None, "modules": FULL_MODULES},
        {"name": "r2-v-reverse", "features": "vision", "posture": None, "modules": tuple(reversed(FULL_MODULES))},
    ]
    if scoped:
        runs += [
            {"name": "r2-vm-scoped", "features": "vision,media", "posture": None, "modules": scoped},
            {"name": "r2-v-scoped", "features": "vision", "posture": None, "modules": scoped},
        ]
    else:
        print("skipped: r2-vm-scoped/r2-v-scoped (no touched modules given)", file=sys.stderr)
    runs += [
        {"name": "r3-personal", "features": "vision,media", "posture": "personal", "modules": FULL_MODULES},
        {"name": "r3-enterprise", "features": "vision,media", "posture": "enterprise", "modules": FULL_MODULES},
    ]
    return runs


def _process_lines() -> list[str]:
    """One `ps` call, pid + ppid + short executable name + full arguments
    per line. None of that argv (["ps", "-eo", "pid=,ppid=,ucomm=,args="])
    names "pytest" anywhere, so this call can never match itself, and
    nothing else watching for "pytest" in a command line can match it
    either -- the pattern never travels on a command line at all."""
    result = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,ucomm=,args="], capture_output=True, text=True,
    )
    return result.stdout.splitlines()


def _parse_process_line(line: str) -> tuple[int, int, str, str] | None:
    # ucomm can itself contain a space under this platform's truncation
    # (a helper process's name cut mid-word). split(None, 3) then puts only
    # the first word in the ucomm slot and folds the rest into args, or the
    # reverse. Harmless for this predicate on purpose: it only needs
    # whatever lands in the ucomm slot to correctly fail "is this a Python
    # interpreter" for a non-Python process, which the first word always
    # does -- it does not need to reproduce ps's own field boundary
    # exactly. Do not "fix" this split without re-examining that contract.
    parts = line.split(None, 3)
    if len(parts) < 4:
        return None
    try:
        pid, ppid = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return pid, ppid, parts[2], parts[3]


def _runner_token_index(tokens: list[str]) -> int | None:
    """Index of the token where the runner is actually being executed, or
    None -- shared by _runs_pytest and _invocation_label so the count and
    the progress line's label can never disagree about what they matched.

    Require either some token whose own BASENAME is exactly "pytest"
    (direct invocation: ".venv/bin/pytest", a bare "pytest") or the
    adjacent pair "-m pytest" (module-invocation form) -- structural, not
    a sharper string match, so a path/directory/database name that merely
    CONTAINS "pytest" as a substring inside a component (a peer's
    --outdir=/tmp/mypytest_results) has no token whose basename equals it
    outright and never matches.

    A token containing "=" is skipped before the basename check, because
    an executed runner token never contains one -- this closes the
    option-valued route (--outdir=/some/path/pytest) outright, where the
    option's PATH VALUE, not a substring inside one component, ends at a
    component named "pytest".

    Two residuals are accepted deliberately here, not closed: a bare
    POSITIONAL path argument (no "=") whose final component happens to be
    named "pytest", and a transient invocation that names "pytest" as a
    bare word without executing it (e.g. a package installer's target).
    Both are phantoms in the safe direction -- extra waiting, never
    wrongness -- except at --max-others 0, where waiting past the poll
    interval reads as starvation, so a long-lived process wedged into
    either shape would stall a strict run. Closing them for real needs
    positional-vs-flag-value parsing against the runner's own flag
    grammar, which is gnarly and not worth building for this; the only
    thing that would make this a true execution assertion instead of an
    appearance-at-component-granularity one is exactly that parser, and
    it stays unbuilt."""
    for i, tok in enumerate(tokens):
        if "=" in tok:
            continue
        if os.path.basename(tok) == "pytest":
            return i
        if tok == "-m" and i + 1 < len(tokens) and tokens[i + 1] == "pytest":
            return i + 1
    return None


def _runs_pytest(args: str) -> bool:
    """True only when the runner is what is actually being executed --
    see _runner_token_index for the rule and its two accepted residuals."""
    return _runner_token_index(args.split()) is not None


def _own_process_tree(lines: list[str], root_pid: int) -> set[int]:
    """root_pid plus every descendant, walked from `ps`'s pid/ppid links --
    a run this script launched itself is not "another" process, however
    the matching predicate below classifies it."""
    children: dict[int, list[int]] = {}
    for line in lines:
        parsed = _parse_process_line(line)
        if parsed is None:
            continue
        pid, ppid, _ucomm, _args = parsed
        children.setdefault(ppid, []).append(pid)
    tree = {root_pid}
    frontier = [root_pid]
    while frontier:
        pid = frontier.pop()
        for child in children.get(pid, ()):
            if child not in tree:
                tree.add(child)
                frontier.append(child)
    return tree


def other_pytest_matches(lines: list[str], exclude_pids: set[int]) -> list[tuple[int, str]]:
    """Pure matcher over already-collected `ps` output -- no subprocess call
    here, so a test can hand it a self-referential line with no process run
    at all. A line matches only when its EXECUTABLE is a Python interpreter
    AND _runs_pytest says the runner is actually what's being executed:
    two structural checks, not a sharper pattern, because any pattern is
    just more text a text-only match would also see -- in a shell or grep
    that merely mentions it in its own arguments (a wait loop's own
    polling command), or in a path/directory/database name that merely
    contains the word (an --outdir, a peer's --db-name). The executable
    check rejects the former (ucomm "zsh"/"bash"/etc is never a python
    interpreter); the tokenised basename check in _runs_pytest rejects the
    latter (a substring has no token whose basename equals the runner).

    Returns (pid, args) per match rather than just a count -- a count
    can't say whose run it is, and whose run it is is what a waiting
    session actually needs to know."""
    matches = []
    for line in lines:
        parsed = _parse_process_line(line)
        if parsed is None:
            continue
        pid, _ppid, ucomm, args = parsed
        if pid in exclude_pids:
            continue
        if "python" in ucomm.lower() and _runs_pytest(args):
            matches.append((pid, args))
    return matches


def count_other_pytest(lines: list[str], exclude_pids: set[int]) -> int:
    """How many other pytest processes -- see other_pytest_matches for the
    predicate. Kept as its own pure entry point since some callers (and
    the existing tests) only need the number."""
    return len(other_pytest_matches(lines, exclude_pids))


def _invocation_label(args: str, max_len: int = 60) -> str:
    """A short, recognisable slice of `args` for the progress line: start
    at the runner token (found by the same scan _runs_pytest uses, so the
    label and the count can never disagree about what they matched) and
    run to the end, trimmed to `max_len` chars. Recognisability, not
    completeness -- this names which suite is running, it does not dump
    the full command line."""
    tokens = args.split()
    start = _runner_token_index(tokens)
    if start is None:
        start = 0
    label = " ".join(tokens[start:])
    if len(label) > max_len:
        label = label[: max_len - 3] + "..."
    return label


def _other_pytest_matches() -> list[tuple[int, str]]:
    lines = _process_lines()
    return other_pytest_matches(lines, _own_process_tree(lines, os.getpid()))


# A tiny or zero expected_seconds would make a lock's own 3x budget tiny
# too, so a fresh, live holder could read as stale to a waiter moments
# later. Floored at both ends: acquire_lock clamps what THIS script
# writes; _lock_age_exceeds_budget clamps whatever it reads, since a
# foreign-format or hand-written lock may not have floored its own.
_MIN_EXPECTED_SECONDS = 60.0


def _parse_keyvalue_lock(text: str) -> dict | None:
    """Fallback for the plain `key=value` (one pair per line) convention
    the other sessions adopted by hand before this script had a lock at
    all -- same field names as the structured (JSON) format this script
    writes, so both normalize to one shape once parsed. Returns None only
    when NOTHING in the text looks like a key=value line -- a line that
    parses but is missing a field is a malformed but REAL lock, not an
    absent one, and must come back as a dict (is_stale's hardened reads
    judge it stale rather than crash; it is never treated as vanished by
    virtue of a missing field alone)."""
    fields: dict[str, object] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    if not fields:
        return None
    for numeric_key in ("pid", "started", "expected_seconds", "db_port"):
        if numeric_key in fields:
            try:
                fields[numeric_key] = float(fields[numeric_key])
            except ValueError:
                pass  # left as a string; the hardened reads below catch it
    if isinstance(fields.get("pid"), float):
        fields["pid"] = int(fields["pid"])
    return fields


def read_lock(path: Path = LOCK_PATH) -> dict | None:
    """The lock's contents, or None if there is none or it parses as
    NEITHER format. Tries the structured (JSON) format this script writes
    first, then the plain key=value format adopted by hand -- a lock that
    parses in EITHER format is a live holder and must never be treated as
    vanished just because this script doesn't natively write that shape.

    Public so a session can check by hand: `python3 -c "import ladder as
    l; print(l.read_lock())"` answers who holds the machine, what they're
    running, and how long they expect to take -- the question a count
    never could."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return _parse_keyvalue_lock(text)


def _pid_alive(pid: int) -> bool:
    """Signal 0 probes existence without sending a real one -- stdlib
    os.kill, no subprocess."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    return True


def _lock_age_exceeds_budget(info: dict, now: float) -> bool:
    """Pure: True once `now` is more than 3x the lock's own stated
    expectation (floored, see _MIN_EXPECTED_SECONDS) past its start.
    Separated from is_stale so this half of the staleness rule is
    testable with no process on the machine at all. A missing or
    non-numeric `started`/`expected_seconds` also reads True (stale) --
    a malformed lock is real and dangerous to leave forever, and crashing
    here would abort the caller's whole acquire instead of letting it
    steal (announced) and move on."""
    try:
        started = float(info["started"])
        expected_seconds = float(info["expected_seconds"])
    except (KeyError, TypeError, ValueError):
        return True
    return (now - started) > 3 * max(expected_seconds, _MIN_EXPECTED_SECONDS)


def is_stale(info: dict, now: float | None = None) -> bool:
    """A lock is stale when its holding process is gone, or its start
    time is older than three times its own stated expectation -- a
    crashed session must not wedge the machine forever. A missing or
    non-numeric `pid` also reads True (stale), for the same reason
    _lock_age_exceeds_budget hardens its own fields: judge, don't crash.
    Public: a session can check a lock it's looking at by hand before
    deciding whether to steal it."""
    try:
        pid = int(info["pid"])
    except (KeyError, TypeError, ValueError):
        return True
    if not _pid_alive(pid):
        return True
    return _lock_age_exceeds_budget(info, time.time() if now is None else now)


def _db_activity_present(port: int) -> bool | None:
    """The database probe: measures the RESOURCE the lock exists to
    protect, not a proxy for it -- a process id and an age both only
    answer "does the holder LOOK alive"; this asks Postgres itself
    whether anything is actually connected and active on `port`. Shells
    out to `psql` (stdlib subprocess, at most one call) if it's on PATH.

    Returns None -- PROBE UNAVAILABLE, never read as "clear" -- when the
    client isn't installed or the connection attempt itself fails (auth,
    refusal, timeout): a refused connection is ambiguous (nothing there,
    vs. something wrong with how this asked) and must not be read as a
    confident "no activity" by the caller."""
    psql = shutil.which("psql")
    if psql is None:
        return None
    query = (
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname NOT IN ('postgres', 'template0', 'template1') "
        "AND state = 'active'"
    )
    env = dict(os.environ)
    env.setdefault("PGPASSWORD", "farabunker")  # docs/DEV.md's documented local default
    try:
        result = subprocess.run(
            [psql, "-h", "localhost", "-p", str(port), "-U", "farabunker",
             "-d", "postgres", "-tAc", query],
            capture_output=True, text=True, timeout=5, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return None


def _write_lock_exclusively(path: Path, info: dict) -> None:
    """Write `info` to `path` with no window where the path exists but is
    empty or partially written. os.open(O_CREAT|O_EXCL) alone has one: it
    creates a zero-byte file, THEN the content gets written -- a second
    waiter's read_lock in that gap sees empty text, fails to parse it in
    EITHER format, and gets None back, which used to be treated as
    "vanished" and stolen even though a live writer was mid-create. Write
    the full content to a private temp file first, then os.link it into
    place: link() is atomic and fails with FileExistsError if the
    destination exists already -- the same exclusivity os.O_EXCL gives,
    but only reachable once the content is already durable on disk, so
    nothing can ever observe a half-written file at `path`."""
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{os.urandom(6).hex()}")
    tmp.write_text(json.dumps(info), encoding="utf-8")
    try:
        os.link(str(tmp), str(path))
    finally:
        tmp.unlink(missing_ok=True)


def acquire_lock(path: Path, *, holder: str, running: str, expected_seconds: float,
                  db_port: int | None = None) -> None:
    """Exclusively create the lock file -- the loser of a race FAILS TO
    CREATE rather than reading stale state and deciding to proceed
    anyway, which is the whole reason this beats a poll: checking and
    starting are two separate steps for a poll, racy in the gap between
    them, and one atomic step here (see _write_lock_exclusively for how
    "exclusive" and "never half-written" are both guaranteed at once).
    Raises LockHeld if a live, non-stale session already holds it.

    STALENESS IS A CONJUNCTION, not the process-and-age check alone: a
    lock whose recorded process looks gone or aged out is stolen only
    after also asking the database itself (`db_port`, see
    _db_activity_present) whether anything is actually connected and
    active. A process id or an age both only answer "does the holder
    LOOK alive"; the database probe answers "is the resource the lock
    protects actually in use", which is the thing that matters. If the
    probe can't run at all (no `db_port` recorded, `psql` missing, the
    connection attempt itself fails), this does NOT silently fall back
    to the process-only check -- it announces, loudly, that it is
    stealing on evidence that can be structurally meaningless (see the
    comment on the `pid` field below) before doing so, so a session
    reading that line can stop it.

    A stale lock is stolen by ATOMIC RENAME, and CAPTURED BEFORE it is
    JUDGED, never the reverse: reading first and renaming second leaves a
    gap between the read and the act, and a plain unlink is aimed at
    whatever sits at the path NOW regardless -- either way, a second
    thief can act on content that changed underneath the first thief's
    judgement (a lock that was stale when read can become someone else's
    fresh, live lock by the time an unlink or a judged-then-rename
    actually runs). Renaming unconditionally to a uniquely-named
    tombstone FIRST, then reading and judging the CAPTURED copy, closes
    that gap: nothing else can be racing for a tombstone only this call
    named, so there is no read-then-act window left to fall into. If the
    captured copy turns out to be live after all, it is renamed straight
    back and refused, exactly as it was. The loser of the capture-rename
    gets a plain FileNotFoundError, turned into the same LockHeld a live
    holder would have given, naming the lost race -- not a retry loop.

    The lock's contents answer what a count never could: which session
    (`holder`), which process, what it's running in recognisable terms
    (`running`), when it started, and how long it expects to take
    (`expected_seconds`, floored to `_MIN_EXPECTED_SECONDS` so a live
    chain can't go instantly stale under its own budget) -- enough for a
    peer to decide between waiting ten minutes and doing something else
    for two hours."""
    info = {
        "holder": holder,
        # This field is meaningful ONLY if the process that writes it is
        # the same process that holds the lock for the WHOLE run -- its
        # death has to mean the hold ended, or the field is worse than
        # useless. In an agent harness, EVERY SHELL CALL IS A SEPARATE
        # PROCESS: acquiring the lock in one call and running the suite
        # in a second call cannot ever satisfy this, because the process
        # that wrote this pid exits the moment its own call returns,
        # before the run it was supposed to describe even starts -- that
        # is not a bug in the code below, it is a structural fact about
        # split-call acquisition, and no amount of care in this function
        # can fix a pid recorded by a process that was never going to
        # live that long. This script's own main() satisfies the
        # requirement because acquire_lock and the entire run to
        # release_lock happen in the ONE process that calls main() --
        # verified by reading it, not assumed. THE SPECIFIC WAY TO BREAK
        # THIS: split acquisition into a separate setup step (e.g. "first
        # acquire the lock, then run the suite" as two calls) -- that
        # reintroduces this exact defect without changing a single line
        # of the locking logic above or below, which is why this note is
        # here at the write site and not in the module docstring, where
        # an editor of the call site would not see it.
        "pid": os.getpid(),
        "running": running,
        "started": time.time(),
        "expected_seconds": max(float(expected_seconds), _MIN_EXPECTED_SECONDS),
    }
    if db_port is not None:
        info["db_port"] = db_port

    try:
        _write_lock_exclusively(path, info)
        return
    except FileExistsError:
        pass

    # CAPTURE FIRST, JUDGE SECOND -- never the reverse. Judging on a read
    # taken before an unconditional rename leaves the same gap the
    # unlink-based steal had: whatever this judged can have already been
    # replaced by the time the rename actually runs, so a slow thief can
    # rename away a holder that became live (or freshly-stolen-by-someone-
    # else) AFTER the read but BEFORE the rename. Renaming unconditionally
    # first, and judging the CAPTURED copy -- which nothing else can be
    # racing for any more, since its tombstone name is ours alone -- means
    # there is no read-then-act gap left for a second thief to fall into.
    tombstone = path.with_name(f"{path.name}.captured-{os.getpid()}-{os.urandom(4).hex()}")
    try:
        os.rename(str(path), str(tombstone))
    except FileNotFoundError:
        # Nothing there to capture (released, or someone else's capture
        # already in flight) -- try a fresh create; if that's also lost,
        # it's the same refusal a live holder would give.
        try:
            _write_lock_exclusively(path, info)
            return
        except FileExistsError:
            raise LockHeld("lost the race to acquire the lock -- retry")

    try:
        captured = read_lock(tombstone)
        if captured is not None and not is_stale(captured):
            # We captured something that turns out to be live, not stale
            # -- it existed at `path` when we renamed it away, but the
            # judgement had to happen on OUR copy (see above), not a
            # pre-rename read. Put it back exactly as it was and refuse.
            os.rename(str(tombstone), str(path))
            holder_desc = captured.get("holder", "?")
            pid_desc = captured.get("pid", "?")
            running_desc = captured.get("running", "?")
            try:
                expected_m = float(captured.get("expected_seconds", 0)) / 60
            except (TypeError, ValueError):
                expected_m = 0.0
            raise LockHeld(
                f"held by {holder_desc!r} (pid {pid_desc}), running "
                f"{running_desc!r}, expecting {expected_m:.0f}m"
            )

        # captured is None (vanished/unparseable in either format) or
        # is_stale(captured) is True (process gone or aged out). Before
        # treating the latter as truly stale, ask the database -- the
        # conjunction.
        if captured is not None:
            recorded_port = captured.get("db_port")
            activity = (
                # A hand-written (key=value) lock's db_port round-trips
                # through _parse_keyvalue_lock's numeric coercion as a
                # float ("5433" -> 5433.0); psql's -p wants an integer.
                _db_activity_present(int(recorded_port))
                if isinstance(recorded_port, (int, float)) else None
            )
            if activity is True:
                os.rename(str(tombstone), str(path))
                raise LockHeld(
                    f"held by {captured.get('holder', '?')!r} (pid "
                    f"{captured.get('pid', '?')}) -- process evidence looked gone "
                    f"or aged out, but port {recorded_port} shows an active "
                    f"database connection; refusing to steal"
                )
            if activity is None:
                print(
                    "WARNING: stealing on process evidence alone -- the database "
                    "probe could not run (no db_port recorded, psql missing, or "
                    "the connection attempt itself failed), and a recorded "
                    "process id can be STRUCTURALLY invalid rather than merely "
                    "stale (see the pid field's own comment in acquire_lock) when "
                    "acquiring and running were separate calls -- 'process gone' "
                    "may be meaningless here, not just weak evidence",
                    file=sys.stderr,
                )
            # activity is False (confirmed clear), or None (degraded,
            # already warned above) -- proceed to steal.

        holder_desc = captured.get("holder", "?") if captured else "?"
        pid_desc = captured.get("pid", "?") if captured else "?"
        try:
            age_s = time.time() - float(captured.get("started", 0)) if captured else 0.0
        except (TypeError, ValueError):
            age_s = 0.0
        try:
            expected_s = float(captured.get("expected_seconds", 0)) if captured else 0.0
        except (TypeError, ValueError):
            expected_s = 0.0
        print(
            f"stealing stale lock: held by {holder_desc!r} (pid {pid_desc}), "
            f"started {age_s:.0f}s ago, expected {expected_s:.0f}s",
            file=sys.stderr,
        )
        try:
            _write_lock_exclusively(path, info)
        except FileExistsError:
            raise LockHeld("a fresh lock appeared while stealing -- retry")
    finally:
        Path(tombstone).unlink(missing_ok=True)


def release_lock(path: Path) -> None:
    """Remove the lock file -- but only if it's still ours. A paused
    (SIGSTOP'd) run can be outlived by its own staleness budget and get
    stolen while stopped; when it's later resumed and finishes, its
    release must not delete the NEW holder's lock. Safe to call when the
    lock is already gone."""
    info = read_lock(path)
    if info is not None and info.get("pid") != os.getpid():
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _install_release_handlers(path: Path) -> None:
    """Release on SIGTERM and SIGINT (termination, interrupt) so a killed
    run doesn't wedge the machine for whoever's waiting. Deliberately NOT
    on SIGSTOP: that's this runner's own documented pause mechanism (see
    module docstring), and a paused run still owns the machine -- it must
    NOT release. Nothing here even tries: SIGSTOP can't be caught by any
    handler, by the OS's own design, so there is no hook that could fire
    on it by accident."""
    def _handler(signum, _frame):
        release_lock(path)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _wait_for_the_machine(max_others: int, poll_seconds: float = 5.0) -> None:
    last_print = 0.0
    while True:
        matches = _other_pytest_matches()
        if len(matches) <= max_others:
            return
        now = time.monotonic()
        if now - last_print >= 60:
            named = ", ".join(f"{pid}:{_invocation_label(args)}" for pid, args in matches)
            print(f"waiting: {len(matches)} other pytest processes -- {named}", file=sys.stderr)
            last_print = now
        time.sleep(poll_seconds)


def _last_summary_line(log_path: Path) -> str:
    lines = [l for l in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    return lines[-1] if lines else ""


def _run_one(name: str, argv: list[str], env: dict, worktree: Path, outdir: Path,
             *, max_others: int = 1, wait: bool = True) -> int:
    if wait:
        _wait_for_the_machine(max_others)
    log_path = outdir / f"{name}.log"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(argv, cwd=str(worktree), env=env, stdout=log, stderr=subprocess.STDOUT)
    with (outdir / "SUMMARY").open("a", encoding="utf-8") as summary:
        summary.write(f"{name} | {proc.returncode} | {_last_summary_line(log_path)}\n")
    return proc.returncode


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    max_others = 1
    if "--max-others" in args:
        i = args.index("--max-others")
        try:
            max_others = int(args[i + 1])
        except (IndexError, ValueError):
            print("--max-others needs an integer")
            return 2
        del args[i:i + 2]

    expect_minutes = None
    if "--expect-minutes" in args:
        i = args.index("--expect-minutes")
        try:
            expect_minutes = float(args[i + 1])
        except (IndexError, ValueError):
            print("--expect-minutes needs a number")
            return 2
        del args[i:i + 2]

    if len(args) < 4:
        print("usage: scripts/ladder.py <worktree> <db_url> <outdir> <full|hotfix> "
              "[--max-others N] [--expect-minutes N] [touched-modules...]")
        return 2

    worktree, db_url, outdir_raw, mode = args[:4]
    touched_modules = args[4:]
    if mode not in ("full", "hotfix"):
        print(f"mode must be 'full' or 'hotfix', got {mode!r}")
        return 2

    outdir = Path(outdir_raw)
    outdir.mkdir(parents=True, exist_ok=True)
    worktree_path = Path(worktree)

    base_env = os.environ.copy()
    base_env["DATABASE_URL"] = db_url

    # The slot lock, held for this whole chain -- see LOCK_PATH's comment
    # for the authoritative/advisory split with the process check below.
    if expect_minutes is None:
        expect_minutes = 120.0 if mode == "full" else 20.0
    holder = worktree_path.name
    running = f"{mode} ladder: " + " ".join(touched_modules or FULL_MODULES)
    db_port = urlparse(db_url).port  # for the staleness conjunction's db probe
    try:
        acquire_lock(LOCK_PATH, holder=holder, running=running,
                     expected_seconds=expect_minutes * 60, db_port=db_port)
    except LockHeld as exc:
        print(f"ladder: machine locked -- {exc}", file=sys.stderr)
        return 2
    _install_release_handlers(LOCK_PATH)

    try:
        failures = 0
        for run in build_run_plan(mode, touched_modules):
            env = dict(base_env)
            env["FARABUNKER_FEATURES"] = run["features"]
            if run["posture"] is not None:
                env["FARABUNKER_TEST_POSTURE"] = run["posture"]
            rc = _run_one(run["name"], [".venv/bin/pytest", "-q", *run["modules"]],
                          env, worktree_path, outdir, max_others=max_others)
            failures += rc != 0

        rc = _run_one(
            "makemigrations-check",
            [".venv/bin/python", "manage.py", "makemigrations", "--check", "--dry-run"],
            base_env, worktree_path, outdir, wait=False,
        )
        failures += rc != 0
        rc = _run_one("check", [".venv/bin/python", "manage.py", "check"],
                       base_env, worktree_path, outdir, wait=False)
        failures += rc != 0

        (outdir / "LADDER_DONE").touch()
        return 1 if failures else 0
    finally:
        # Release on normal completion AND on any exception -- the
        # signal handlers above cover SIGTERM/SIGINT; SIGSTOP (pause)
        # never reaches here or them, by design.
        release_lock(LOCK_PATH)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
