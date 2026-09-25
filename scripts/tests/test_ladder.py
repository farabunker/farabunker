"""Test for scripts/ladder.py's run-plan builder and its other-pytest matcher.

build_run_plan never shells out (its only I/O is one stderr line when the
scoped pair is dropped), so this imports the module directly rather than
shelling out -- contrast scripts/tests/test_preview.py, which must shell
out because scripts/preview is a bash script.

count_other_pytest is likewise pinned as a pure function over a list of
already-collected `ps` lines (no subprocess run here either): it is the
matcher scripts/ladder.py's wait loop used to run by shelling out to
`pgrep -f pytest`, which put the pattern it searched for on the command
line it then searched -- a watcher counting its own reflection. The
predicate has since had to become structural rather than textual a second
time: matching "pytest" anywhere in a process's arguments also counts a
real Python process whose path, --outdir, or --db-name merely CONTAINS
the word -- an editor's test-discovery adapter, or one unlucky peer
argument, starves a strict run exactly like the reflection bug did. The
fix asks whether the runner is what is being EXECUTED (some argument
token's own basename is exactly "pytest", or the adjacent "-m pytest"
pair), not whether the word appears somewhere in the text.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ladder import FULL_MODULES, build_run_plan, count_other_pytest  # noqa: E402

REVERSED = tuple(reversed(FULL_MODULES))


def test_build_run_plan_pins_features_posture_and_modules_per_run():
    full = build_run_plan("full", ["identity", "tools"])
    assert [run["name"] for run in full] == [
        "r1-forward", "r1-reverse",
        "r2-v-forward", "r2-v-reverse", "r2-vm-scoped", "r2-v-scoped",
        "r3-personal", "r3-enterprise",
    ]
    by_name = {run["name"]: run for run in full}

    # A swapped features/posture/modules value must fail this, not just a
    # renamed run -- each field is pinned explicitly.
    assert by_name["r1-forward"]["features"] == "vision,media"
    assert by_name["r1-forward"]["posture"] is None
    assert by_name["r1-forward"]["modules"] == FULL_MODULES

    assert by_name["r1-reverse"]["features"] == "vision,media"
    assert by_name["r1-reverse"]["modules"] == REVERSED

    assert by_name["r2-v-forward"]["features"] == "vision"
    assert by_name["r2-v-forward"]["modules"] == FULL_MODULES

    assert by_name["r2-v-reverse"]["features"] == "vision"
    assert by_name["r2-v-reverse"]["modules"] == REVERSED

    assert by_name["r2-vm-scoped"]["features"] == "vision,media"
    assert by_name["r2-vm-scoped"]["modules"] == ("identity", "tools")

    assert by_name["r2-v-scoped"]["features"] == "vision"
    assert by_name["r2-v-scoped"]["modules"] == ("identity", "tools")

    assert by_name["r3-personal"]["features"] == "vision,media"
    assert by_name["r3-personal"]["posture"] == "personal"
    assert by_name["r3-personal"]["modules"] == FULL_MODULES

    assert by_name["r3-enterprise"]["features"] == "vision,media"
    assert by_name["r3-enterprise"]["posture"] == "enterprise"
    assert by_name["r3-enterprise"]["modules"] == FULL_MODULES

    # Posture is a r3-only concern -- every other run leaves it unset.
    assert all(run["posture"] is None for run in full if not run["name"].startswith("r3-"))


def test_build_run_plan_full_with_no_touched_modules_drops_the_scoped_pair():
    assert [run["name"] for run in build_run_plan("full", [])] == [
        "r1-forward", "r1-reverse", "r2-v-forward", "r2-v-reverse", "r3-personal", "r3-enterprise",
    ]


def test_build_run_plan_hotfix_is_only_the_r1_pair():
    hotfix = build_run_plan("hotfix", [])
    assert [run["name"] for run in hotfix] == ["r1-forward", "r1-reverse"]
    assert all(run["features"] == "vision,media" and run["posture"] is None for run in hotfix)
    assert hotfix[0]["modules"] == FULL_MODULES
    assert hotfix[1]["modules"] == REVERSED


def test_count_other_pytest_ignores_a_shell_whose_arguments_merely_mention_pytest():
    # This is the self-referential case: a wait loop's own polling command
    # (or the old `pgrep -f pytest` call itself) has "pytest" sitting right
    # there in its arguments. A predicate that matched on arguments alone
    # would count it -- a sharper pattern doesn't fix that, since any
    # pattern is still just text the match would also see. This line is
    # rejected because its EXECUTABLE (ucomm) is a shell, not because the
    # pattern was tuned to miss it.
    lines = [
        "100 1 zsh /bin/zsh -c eval 'pgrep -f pytest' < /dev/null",
    ]
    assert count_other_pytest(lines, exclude_pids=set()) == 0


def test_count_other_pytest_counts_a_real_python_process_running_pytest():
    # The positive case that must still fire: a real interpreter whose
    # arguments name the runner is another session's test run and belongs
    # in the count.
    lines = [
        "200 150 Python /venv/bin/python .venv/bin/pytest -q tools models",
    ]
    assert count_other_pytest(lines, exclude_pids=set()) == 1


def test_count_other_pytest_excludes_its_own_pid():
    # A run this script launched itself has a real interpreter and real
    # runner arguments, so the predicate correctly matches it -- it must be
    # excluded by pid (this script's own process tree), not by the
    # predicate misclassifying it.
    lines = [
        "300 1 Python .venv/bin/pytest -q scripts",
    ]
    assert count_other_pytest(lines, exclude_pids={300}) == 0


def test_count_other_pytest_counts_the_module_invocation_form():
    # `python -m pytest` is a real, common invocation, and a plain
    # substring match "happened to" catch it before -- nothing pinned that
    # the tokenised replacement keeps catching it. Here "pytest" is its
    # own argument token (not a path), so the basename check alone already
    # covers it; the pinned behaviour is what matters, not which branch of
    # the predicate fires.
    lines = [
        "400 1 Python /usr/bin/python3 -m pytest -q tools models",
    ]
    assert count_other_pytest(lines, exclude_pids=set()) == 1


def test_count_other_pytest_ignores_a_waiting_peer_runner_with_no_runner_in_its_arguments():
    # The two-watchers case this whole predicate exists for: a real Python
    # process (another session's own wait loop, or any other real
    # interpreter) whose arguments never name the runner at all. Must
    # count zero -- until now this was only proven indirectly, through a
    # shell line rejected by the executable check, not through a genuine
    # Python process rejected by _runs_pytest.
    lines = [
        "410 1 Python /usr/bin/python3 manage.py check",
    ]
    assert count_other_pytest(lines, exclude_pids=set()) == 0


def test_count_other_pytest_ignores_a_real_python_process_whose_path_merely_contains_pytest():
    # The phantom this round is about: a real interpreter running a real
    # *other* command, where some argument -- an --outdir, a --db-name, a
    # coincidental path -- happens to CONTAIN the substring "pytest". A
    # plain "pytest" in args match would count this forever; the
    # tokenised basename check rejects it because no token's basename is
    # exactly "pytest", however the substring is spelled inside a larger
    # one.
    lines = [
        "420 1 Python /usr/bin/python3 manage.py test --outdir=/tmp/mypytest_results/run1",
    ]
    assert count_other_pytest(lines, exclude_pids=set()) == 0
