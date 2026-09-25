#!/usr/bin/env python3
"""scripts/ladder.py -- the pre-merge test ladder, as one runnable script.

See .claude/skills/test-ladder/SKILL.md for the full procedure this encodes.

Usage:
    scripts/ladder.py <worktree> <db_url> <outdir> <full|hotfix> [--max-others N] [touched-modules...]

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
machine-wide, polled with `pgrep -f pytest`.
# ponytail: name-substring match, not a lock -- a false positive just costs
# a few extra seconds of waiting, never a wrong result.
Default --max-others is 1 (AGENTS.md: at most two full suites across the
machine, this run plus one other); pass --max-others 0 when peers have
agreed a stricter cap for a period. A line is printed to stderr every 60s
while waiting: "waiting: N other pytest processes".

Pausing: SIGSTOP this script's own pid (not its process group). Its
in-flight pytest subprocess is a separate process and keeps running to
completion regardless -- the result lands in that run's .log and gets
appended to SUMMARY normally the moment SIGCONT lets the parent resume.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

FULL_MODULES = ("scripts", "identity", "agents", "foundation", "models", "tools")


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


def _other_pytest_count() -> int:
    result = subprocess.run(["pgrep", "-f", "pytest"], capture_output=True, text=True)
    if result.returncode != 0:
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _wait_for_the_machine(max_others: int, poll_seconds: float = 5.0) -> None:
    last_print = 0.0
    while True:
        count = _other_pytest_count()
        if count <= max_others:
            return
        now = time.monotonic()
        if now - last_print >= 60:
            print(f"waiting: {count} other pytest processes", file=sys.stderr)
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

    if len(args) < 4:
        print("usage: scripts/ladder.py <worktree> <db_url> <outdir> <full|hotfix> "
              "[--max-others N] [touched-modules...]")
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


if __name__ == "__main__":
    sys.exit(main(sys.argv))
