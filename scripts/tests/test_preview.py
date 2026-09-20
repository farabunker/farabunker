"""Tests for scripts/preview, the branch-preview launcher.

These tests shell out to the actual script with subprocess.run and assert
on exit codes and output — they never invoke docker. Validation (branch
name checks, worktree existence, the --yes confirmation for `reset`) all
happens before any docker command in the script, so those paths are safe
to exercise directly against the real repo. The one test that needs an
*existing* worktree (`up`'s dry-run path) builds a throwaway git repo
under tmp_path instead of depending on any worktree that happens to exist
in this checkout — worktrees come and go as branches merge, so the test
must not rely on one sticking around.
"""
from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "preview"
REPO_ROOT = SCRIPT.parent.parent


def run_script(
    *args: str,
    cwd: Path | None = None,
    env: dict | None = None,
    script: Path = SCRIPT,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(script), *args],
        cwd=str(cwd) if cwd else str(REPO_ROOT),
        env=env if env is not None else os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )


def make_fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Build a throwaway git repo with the same shape as the real one:
    scripts/preview (a copy of the real script) plus a fake worktree under
    .claude/worktrees/. No `git worktree` linkage is needed — the script
    only cares about the directory layout and `git rev-parse
    --git-common-dir`, both of which a plain `git init` satisfies.
    """
    repo = tmp_path / "main-repo"
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    script_copy = scripts_dir / "preview"
    shutil.copy(SCRIPT, script_copy)
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IEXEC)

    (repo / "compose.preview.yaml").write_text("services: {}\n")
    (repo / ".env").write_text("SECRET_KEY=test\n")

    worktree = repo / ".claude" / "worktrees" / "fake-branch"
    worktree.mkdir(parents=True)
    (worktree / "compose.preview.yaml").write_text("services: {}\n")

    return repo, worktree


def make_docker_stub(tmp_path: Path, info_exit_code: int = 1) -> Path:
    """A fake `docker` executable that fails `docker info` (simulating the
    daemon not running) and succeeds for anything else. Returned as a
    directory to prepend to PATH so it shadows the real `docker`. This is
    the only way to exercise require_docker_running()'s failure message
    without invoking real docker: PREVIEW_DRY_RUN skips that check
    entirely, so it can't be used here.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "info" ]; then exit {info_exit_code}; fi\n'
        "exit 0\n"
    )
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC)
    return bin_dir


class TestHelp:
    def test_help_exits_zero_and_lists_subcommands(self):
        result = run_script("--help")
        assert result.returncode == 0
        for subcommand in ("up", "down", "reset"):
            assert subcommand in result.stdout

    def test_no_args_shows_help(self):
        result = run_script()
        assert result.returncode == 0
        assert "Usage" in result.stdout


class TestUnknownSubcommand:
    def test_unknown_subcommand_exits_nonzero_with_usage(self):
        result = run_script("frobnicate")
        assert result.returncode != 0
        assert "unknown command" in result.stderr
        assert "Usage" in result.stderr


class TestUpMissingWorktree:
    def test_up_nonexistent_worktree_exits_nonzero_and_names_path(self):
        branch = "this-branch-definitely-does-not-exist-12345"
        result = run_script("up", branch)
        assert result.returncode != 0
        assert branch in result.stderr
        # The script must say exactly where it looked, not just that it failed.
        assert f".claude/worktrees/{branch}" in result.stderr


class TestResetRequiresConfirmation:
    def test_reset_without_yes_refuses_before_any_docker_call(self):
        branch = "totally-fake-branch-nothing-to-delete"
        result = run_script("reset", branch)
        assert result.returncode != 0
        assert "--yes" in result.stderr
        # The refusal happens before resolve_worktree_path_soft() or any
        # docker call, so the "no worktree found" / docker-not-running
        # messages must NOT appear — only the confirmation refusal.
        assert "no worktree found" not in result.stderr
        assert "docker" not in result.stderr.lower()


class TestBranchNameValidation:
    def test_branch_name_with_dotdot_is_rejected(self):
        result = run_script("up", "foo..bar")
        assert result.returncode != 0
        assert "must not contain '..'" in result.stderr

    def test_bare_name_with_slash_is_treated_as_a_worktree_path(self):
        # A '/' no longer means "invalid branch name" outright — per the
        # brief, the argument may be a worktree name OR a path. "foo/bar"
        # has no '..' so it's treated as a path candidate; since no such
        # directory exists, it fails there instead, but still safely (no
        # docker call, no filesystem mutation) and still non-zero.
        result = run_script("up", "foo/bar")
        assert result.returncode != 0
        assert "worktree path does not exist: foo/bar" in result.stderr

    def test_branch_name_with_dotdot_is_rejected_for_reset(self):
        # Also exercised via reset --yes, so the rejection can't be
        # confused with the missing-confirmation refusal.
        result = run_script("reset", "../../etc", "--yes")
        assert result.returncode != 0
        assert "must not contain '..'" in result.stderr

    def test_reset_dot_is_rejected_before_touching_anything(self):
        # A bare "." would make data/preview/. resolve to data/preview/
        # itself — without this check, `reset . --yes` deletes every
        # branch's preview data instead of one branch's. Must refuse via
        # validation, before resolve_worktree_path_soft() or any docker
        # call (both of which would mention "docker" or "worktree" on
        # failure — their absence here proves we never got that far).
        result = run_script("reset", ".", "--yes")
        assert result.returncode != 0
        assert "invalid branch name '.'" in result.stderr
        assert "docker" not in result.stderr.lower()
        assert "no worktree found" not in result.stderr

    def test_symbols_only_branch_name_fails_before_any_side_effect(self, tmp_path):
        # "___" passes validate_branch_name (no '/', no '..', doesn't start
        # with '-') but sanitizes to an empty compose project name. That
        # must be caught before create_data_dirs() or any port check runs.
        repo, worktree = make_fake_repo(tmp_path)
        env = os.environ.copy()
        env["PREVIEW_DRY_RUN"] = "1"

        result = run_script(
            "up", "___", "--port", "18010", "--db-port", "15441",
            cwd=repo, env=env, script=repo / "scripts" / "preview",
        )

        assert result.returncode != 0
        assert "sanitizes to an empty project name" in result.stderr
        assert not (repo / "data" / "preview" / "___").exists()


class TestExplicitWorktreePathArgument:
    def test_up_accepts_explicit_worktree_path_under_dry_run(self, tmp_path):
        # The brief's subcommand header allows "a worktree name or path" —
        # an existing directory containing '/' must be usable directly,
        # with the branch key derived from its basename.
        repo, worktree = make_fake_repo(tmp_path)
        env = os.environ.copy()
        env["PREVIEW_DRY_RUN"] = "1"

        result = run_script(
            "up", str(worktree), "--port", "18004", "--db-port", "15436",
            cwd=repo, env=env, script=repo / "scripts" / "preview",
        )

        assert result.returncode == 0, result.stderr
        assert "farabunker-preview-fake-branch" in result.stdout
        assert f"FARABUNKER_SRC={worktree}" in result.stdout
        # Branch key (data dir, project name) comes from the path's
        # basename, "fake-branch" — same as if a bare branch name had
        # been given.
        data_dir = repo / "data" / "preview" / "fake-branch"
        assert (data_dir / "postgres").is_dir()

    def test_worktree_path_with_dotdot_segment_is_rejected(self, tmp_path):
        repo, worktree = make_fake_repo(tmp_path)
        env = os.environ.copy()
        env["PREVIEW_DRY_RUN"] = "1"
        bogus_path = f"{worktree}/../fake-branch"

        result = run_script(
            "up", bogus_path,
            cwd=repo, env=env, script=repo / "scripts" / "preview",
        )

        assert result.returncode != 0
        assert "must not contain '..'" in result.stderr

    def test_nonexistent_worktree_path_is_rejected(self, tmp_path):
        repo, worktree = make_fake_repo(tmp_path)
        env = os.environ.copy()
        env["PREVIEW_DRY_RUN"] = "1"
        missing_path = str(repo / "nowhere" / "fake-branch")

        result = run_script(
            "up", missing_path,
            cwd=repo, env=env, script=repo / "scripts" / "preview",
        )

        assert result.returncode != 0
        assert "worktree path does not exist" in result.stderr
        assert missing_path in result.stderr


class TestDockerNotRunning:
    def test_up_reports_clear_error_when_docker_daemon_is_down(self, tmp_path):
        # Deliberately NOT PREVIEW_DRY_RUN=1 — dry-run skips the docker
        # check entirely, so it can't prove this failure path works.
        # A stub `docker` on PATH stands in for "docker not invoked for
        # real" while still exercising require_docker_running()'s actual
        # `docker info` call and failure message.
        repo, worktree = make_fake_repo(tmp_path)
        bin_dir = make_docker_stub(tmp_path)
        env = os.environ.copy()
        env.pop("PREVIEW_DRY_RUN", None)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

        result = run_script(
            "up", "fake-branch", "--port", "18005", "--db-port", "15437",
            cwd=repo, env=env, script=repo / "scripts" / "preview",
        )

        assert result.returncode != 0
        assert "Docker does not appear to be running" in result.stderr


class TestPortConflict:
    def test_up_reports_port_conflict_and_suggests_next_free_port(self, tmp_path):
        repo, worktree = make_fake_repo(tmp_path)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            bound_port = sock.getsockname()[1]

            env = os.environ.copy()
            env["PREVIEW_DRY_RUN"] = "1"  # port check runs regardless of dry-run

            result = run_script(
                "up", "fake-branch", "--port", str(bound_port), "--db-port", "15438",
                cwd=repo, env=env, script=repo / "scripts" / "preview",
            )

            assert result.returncode != 0
            assert f"port {bound_port} is already in use" in result.stderr
            assert "--port" in result.stderr
        finally:
            sock.close()


class TestDownLeavesData:
    def test_down_dry_run_leaves_data_directory_in_place(self, tmp_path):
        repo, worktree = make_fake_repo(tmp_path)
        env = os.environ.copy()
        env["PREVIEW_DRY_RUN"] = "1"
        script = repo / "scripts" / "preview"

        up_result = run_script(
            "up", "fake-branch", "--port", "18006", "--db-port", "15440",
            cwd=repo, env=env, script=script,
        )
        assert up_result.returncode == 0, up_result.stderr
        data_dir = repo / "data" / "preview" / "fake-branch"
        assert data_dir.is_dir()

        down_result = run_script(
            "down", "fake-branch",
            cwd=repo, env=env, script=script,
        )

        assert down_result.returncode == 0, down_result.stderr
        assert data_dir.is_dir()
        assert (data_dir / "postgres").is_dir()
        assert "Data left in place" in down_result.stdout


class TestUpDryRunEndToEnd:
    def test_up_dry_run_resolves_seeds_and_prints_compose_command(self, tmp_path):
        repo, worktree = make_fake_repo(tmp_path)
        env = os.environ.copy()
        env["PREVIEW_DRY_RUN"] = "1"

        result = run_script(
            "up", "fake-branch", "--port", "18001", "--db-port", "15433",
            cwd=repo,
            env=env,
            script=repo / "scripts" / "preview",
        )

        assert result.returncode == 0, result.stderr
        assert "[dry-run]" in result.stdout
        assert "farabunker-preview-fake-branch" in result.stdout
        assert "PREVIEW_WEB_PORT=18001" in result.stdout
        assert "PREVIEW_DB_PORT=15433" in result.stdout

        # .env was seeded from the fake main repo's .env, never overwritten.
        assert (worktree / ".env").read_text() == "SECRET_KEY=test\n"

        # data/preview/<branch>/{postgres,documents,inbox,generated} were created.
        data_dir = repo / "data" / "preview" / "fake-branch"
        assert (data_dir / "postgres").is_dir()
        assert (data_dir / "documents").is_dir()
        assert (data_dir / "inbox").is_dir()
        assert (data_dir / "generated").is_dir()

    def test_up_dry_run_does_not_overwrite_existing_env(self, tmp_path):
        repo, worktree = make_fake_repo(tmp_path)
        (worktree / ".env").write_text("PRE_EXISTING=1\n")
        env = os.environ.copy()
        env["PREVIEW_DRY_RUN"] = "1"

        result = run_script(
            "up", "fake-branch", "--port", "18002", "--db-port", "15434",
            cwd=repo, env=env, script=repo / "scripts" / "preview",
        )

        assert result.returncode == 0, result.stderr
        assert (worktree / ".env").read_text() == "PRE_EXISTING=1\n"

    def test_up_dry_run_falls_back_to_main_repo_compose_file(self, tmp_path):
        repo, worktree = make_fake_repo(tmp_path)
        (worktree / "compose.preview.yaml").unlink()
        env = os.environ.copy()
        env["PREVIEW_DRY_RUN"] = "1"

        result = run_script(
            "up", "fake-branch", "--port", "18003", "--db-port", "15435",
            cwd=repo, env=env, script=repo / "scripts" / "preview",
        )

        assert result.returncode == 0, result.stderr
        assert "falling back" in result.stderr
        assert str(repo / "compose.preview.yaml") in result.stdout
