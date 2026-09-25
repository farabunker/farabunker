#!/usr/bin/env python3
"""scripts/ladder.py -- the pre-merge test ladder, as one runnable script.

See .claude/skills/test-ladder/SKILL.md for the full procedure this encodes.

Usage:
    scripts/ladder.py <worktree> <db_url> <outdir> <full|hotfix> [touched-modules...]

Each named run writes <outdir>/<run>.log, appends
"<run> | <returncode> | <last summary line>" to <outdir>/SUMMARY, and
<outdir>/LADDER_DONE is touched once every run (the trailing
makemigrations/check pair included) has finished.

One test database per runner: point <db_url> at a database name nobody else
is using (docs/DEV.md rung 1) -- never two pytest runs sharing one database
name, which has produced a false red before.

Before EVERY run this waits for no other machine-wide pytest process (the
interim one-suite rule), polled with `pgrep -f pytest`.
# ponytail: name-substring match, not a lock -- a false positive just costs
# a few extra seconds of waiting, never a wrong result.

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
    """Pure: the named runs for `mode`, in order. No subprocess, no I/O --
    scripts/tests/test_ladder.py imports this directly."""
    runs = [
        {"name": "r1-forward", "features": "vision,media", "posture": None, "modules": FULL_MODULES},
        {"name": "r1-reverse", "features": "vision,media", "posture": None, "modules": tuple(reversed(FULL_MODULES))},
    ]
    if mode == "hotfix":
        return runs
    scoped = tuple(touched_modules)
    runs += [
        {"name": "r2-vm-full", "features": "vision,media", "posture": None, "modules": FULL_MODULES},
        {"name": "r2-v-full", "features": "vision", "posture": None, "modules": FULL_MODULES},
        {"name": "r2-vm-scoped", "features": "vision,media", "posture": None, "modules": scoped},
        {"name": "r2-v-scoped", "features": "vision", "posture": None, "modules": scoped},
        {"name": "r3-personal", "features": "vision,media", "posture": "personal", "modules": ()},
        {"name": "r3-enterprise", "features": "vision,media", "posture": "enterprise", "modules": ()},
    ]
    return runs


def _other_pytest_running() -> bool:
    result = subprocess.run(["pgrep", "-f", "pytest"], capture_output=True)
    return result.returncode == 0


def _wait_for_the_machine(poll_seconds: float = 5.0) -> None:
    while _other_pytest_running():
        time.sleep(poll_seconds)


def _last_summary_line(log_path: Path) -> str:
    lines = [l for l in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    return lines[-1] if lines else ""


def _run_one(name: str, argv: list[str], env: dict, worktree: Path, outdir: Path) -> int:
    _wait_for_the_machine()
    log_path = outdir / f"{name}.log"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(argv, cwd=str(worktree), env=env, stdout=log, stderr=subprocess.STDOUT)
    with (outdir / "SUMMARY").open("a", encoding="utf-8") as summary:
        summary.write(f"{name} | {proc.returncode} | {_last_summary_line(log_path)}\n")
    return proc.returncode


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        print("usage: scripts/ladder.py <worktree> <db_url> <outdir> <full|hotfix> [touched-modules...]")
        return 2

    worktree, db_url, outdir_raw, mode = argv[1:5]
    if mode not in ("full", "hotfix"):
        print(f"mode must be 'full' or 'hotfix', got {mode!r}")
        return 2

    outdir = Path(outdir_raw)
    outdir.mkdir(parents=True, exist_ok=True)
    worktree_path = Path(worktree)

    base_env = os.environ.copy()
    base_env["DATABASE_URL"] = db_url

    for run in build_run_plan(mode, argv[5:]):
        env = dict(base_env)
        env["FARABUNKER_FEATURES"] = run["features"]
        if run["posture"] is not None:
            env["FARABUNKER_TEST_POSTURE"] = run["posture"]
        _run_one(run["name"], [".venv/bin/pytest", "-q", *run["modules"]], env, worktree_path, outdir)

    _run_one(
        "makemigrations-check",
        [".venv/bin/python", "manage.py", "makemigrations", "--check", "--dry-run"],
        base_env, worktree_path, outdir,
    )
    _run_one("check", [".venv/bin/python", "manage.py", "check"], base_env, worktree_path, outdir)

    (outdir / "LADDER_DONE").touch()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
