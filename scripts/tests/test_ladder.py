"""Tests for scripts/ladder.py's run-plan builder, process matcher, and
slot lock.

build_run_plan and the matcher (other_pytest_matches et al.) are pure
functions over already-collected input, imported directly with no
subprocess -- see their own docstrings in ladder.py for what each
predicate does and why it's structural rather than a sharper pattern.

The slot lock's exclusive-create, capture-then-judge steal, and
database-conjunction decision logic are exercised for real (temp lock
files under `tmp_path`, never `LOCK_PATH` itself) rather than reasoned
about -- see test_two_simultaneous_stealers_cannot_both_win_the_same_
stale_lock in particular, which races real threads against the real
os.rename syscall the fix uses.

_db_activity_present and _shared_db_presence are mocked in most tests
here (decision-logic coverage, not the subprocess call); their real
subprocess path is instead exercised directly against a live Postgres
in test_shared_db_presence_reads_clear_with_a_real_application_connection
and its busy-direction sibling, which manufacture real connections on
the shared port -- safe to do because every ladder run serializes on
the slot lock (see SKILL.md, "The lock protects the resource, not the
ceremony of running a suite").
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ladder import (  # noqa: E402
    FULL_MODULES,
    DatabaseNeverClear,
    LockHeld,
    acquire_lock,
    build_run_plan,
    is_stale,
    other_pytest_matches,
    read_lock,
    release_lock,
)
import ladder  # noqa: E402  -- needed to mock ladder._db_activity_present in place

REVERSED = tuple(reversed(FULL_MODULES))


@pytest.fixture
def lock_path(tmp_path):
    """Every lock in these tests lives under pytest's own per-test temp
    directory, never LOCK_PATH -- cleaned up by pytest itself, so a test
    body only needs to acquire/release, never unlink or rmdir."""
    return tmp_path / "test.lock"


@pytest.fixture
def dead_pid():
    """A genuinely dead pid -- asking the OS directly (os.kill(pid, 0))
    rather than pattern-matching a name is the "ask the thing itself"
    doctrine several tests below exist to follow, so this is a real
    process that has actually exited, not a fabricated number."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@pytest.fixture
def stale_lock(dead_pid):
    """The stale-lock shape repeated across the acquire_lock tests below:
    a genuinely dead pid, started long enough ago and with a generous
    enough expected_seconds that only the process check -- not the age
    check -- is what makes it stale. Tests that need a `db_port` add one
    with `{**stale_lock, "db_port": 5433}`."""
    return {
        "holder": "dead-session", "pid": dead_pid, "running": "full ladder: tools",
        "started": time.time() - 10, "expected_seconds": 600,
    }


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
    assert len(other_pytest_matches(lines, exclude_pids=set())) == 0


def test_count_other_pytest_counts_a_real_python_process_running_pytest():
    # The positive case that must still fire: a real interpreter whose
    # arguments name the runner is another session's test run and belongs
    # in the count.
    lines = [
        "200 150 Python /venv/bin/python .venv/bin/pytest -q tools models",
    ]
    assert len(other_pytest_matches(lines, exclude_pids=set())) == 1


def test_count_other_pytest_excludes_its_own_pid():
    # A run this script launched itself has a real interpreter and real
    # runner arguments, so the predicate correctly matches it -- it must be
    # excluded by pid (this script's own process tree), not by the
    # predicate misclassifying it.
    lines = [
        "300 1 Python .venv/bin/pytest -q scripts",
    ]
    assert len(other_pytest_matches(lines, exclude_pids={300})) == 0


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
    assert len(other_pytest_matches(lines, exclude_pids=set())) == 1


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
    assert len(other_pytest_matches(lines, exclude_pids=set())) == 0


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
    assert len(other_pytest_matches(lines, exclude_pids=set())) == 0


def test_count_other_pytest_ignores_an_equals_joined_option_whose_value_ends_in_pytest():
    # A different, sharper phantom than the substring-inside-a-component
    # case above: here the option's VALUE is a path whose final component
    # is exactly "pytest" (not a substring inside a larger component), so
    # the basename check alone would match it -- that's the crack. The
    # "=" skip closes this specific route: an executed runner token never
    # contains "=", so any token that does is never the runner itself.
    lines = [
        "500 1 Python /usr/bin/python3 manage.py test --outdir=/tmp/results/pytest",
    ]
    assert len(other_pytest_matches(lines, exclude_pids=set())) == 0


def test_count_other_pytest_counts_a_transient_package_install_naming_the_runner():
    # Documentation of an accepted residual, not a defect: "pytest" named
    # as a bare install target has no "=" to skip and its own basename is
    # exactly "pytest", so it matches like a real invocation would. This
    # is deliberately left open (see _runner_token_index) -- it is a
    # phantom in the safe direction and lasts only as long as the
    # install, so it is not worth positional-vs-flag-value parsing to
    # close.
    lines = [
        "510 1 Python /usr/bin/python3 -m pip install pytest",
    ]
    assert len(other_pytest_matches(lines, exclude_pids=set())) == 1


def test_is_stale_true_when_age_exceeds_three_times_expected_seconds():
    # Pure half of staleness: no process on the machine matters here --
    # the holder is this test itself (alive), only the clock math is
    # under test. started 31 minutes ago, expected 10 minutes -> 3x
    # budget is 30 minutes, so this is just past it.
    info = {"pid": os.getpid(), "started": 1_000_000.0, "expected_seconds": 600}
    assert is_stale(info, now=1_000_000.0 + 31 * 60) is True


def test_is_stale_false_when_alive_and_within_budget():
    info = {"pid": os.getpid(), "started": 1_000_000.0, "expected_seconds": 600}
    assert is_stale(info, now=1_000_000.0 + 5 * 60) is False


def test_is_stale_true_when_the_holding_process_is_gone_even_if_fresh(dead_pid):
    info = {"pid": dead_pid, "started": time.time(), "expected_seconds": 999999}
    assert is_stale(info, now=time.time()) is True


def test_acquire_lock_is_exclusive_then_release_lets_the_next_acquire_through(lock_path):
    # The behaviour a pure function can't stand in for: os.O_EXCL itself.
    # Acquire, attempt a second acquire and assert it fails, release,
    # assert the next acquire succeeds -- actually exercised, not reasoned
    # about.
    acquire_lock(lock_path, holder="session-a", running="full ladder: tools",
                 expected_seconds=600)

    with pytest.raises(LockHeld):
        acquire_lock(lock_path, holder="session-b", running="hotfix ladder: identity",
                     expected_seconds=300)
    assert read_lock(lock_path)["holder"] == "session-a"  # untouched by the loser

    release_lock(lock_path)
    assert not lock_path.exists()

    acquire_lock(lock_path, holder="session-c", running="full ladder: models",
                 expected_seconds=600)
    assert read_lock(lock_path)["holder"] == "session-c"


def test_acquire_lock_steals_a_stale_lock_with_an_announcement_not_silently(lock_path, stale_lock):
    lock_path.write_text(json.dumps(stale_lock), encoding="utf-8")

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        acquire_lock(lock_path, holder="rescuer", running="full ladder: models",
                     expected_seconds=600)
    assert "stealing stale lock" in stderr.getvalue()  # never silent
    assert read_lock(lock_path)["holder"] == "rescuer"


def test_release_lock_does_not_delete_a_lock_it_no_longer_owns(lock_path):
    # The pause+steal+resume race this guards against: a session paused
    # past its own staleness budget can be stolen from while stopped;
    # when it resumes and finishes, its release must not delete whoever
    # holds the lock now. Simulated here by planting a lock with a
    # different pid than this test's own.
    someone_else = {
        "holder": "someone-else", "pid": os.getpid() + 1, "running": "full ladder: tools",
        "started": time.time(), "expected_seconds": 600,
    }
    lock_path.write_text(json.dumps(someone_else), encoding="utf-8")
    release_lock(lock_path)  # must be a no-op -- not our pid in the file
    assert lock_path.exists()
    assert read_lock(lock_path)["holder"] == "someone-else"


def test_two_simultaneous_stealers_cannot_both_win_the_same_stale_lock(lock_path, stale_lock):
    # Two waiters judging the SAME stale lock stale on nearby ticks must
    # not both end up holding it. Real threads racing the real os.rename
    # syscall, lined up as closely as a barrier can put them -- exactly
    # one may win; the loser must get LockHeld, not a silent second
    # holder. This is the exercise that caught the capture-then-judge
    # defect directly: an earlier version read as fixed after a related
    # change, passed dozens of runs, and then failed on iteration 33 of a
    # stress loop -- run it both directions, don't trust that it reads
    # correctly.
    lock_path.write_text(json.dumps(stale_lock), encoding="utf-8")

    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def steal(name: str) -> None:
        barrier.wait()
        try:
            acquire_lock(lock_path, holder=name, running="full ladder: tools",
                         expected_seconds=600)
            outcomes[name] = "won"
        except LockHeld as exc:
            outcomes[name] = f"lost: {exc}"

    threads = [threading.Thread(target=steal, args=(n,)) for n in ("A", "B")]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)

    winners = [v for v in outcomes.values() if v == "won"]
    losers = [v for v in outcomes.values() if v.startswith("lost")]
    assert len(winners) == 1 and len(losers) == 1, outcomes
    final = read_lock(lock_path)
    assert final is not None and final["holder"] in ("A", "B")
    # No tombstone left behind by either the winner or the loser.
    leftovers = [p.name for p in lock_path.parent.iterdir() if p != lock_path]
    assert leftovers == [], leftovers


def test_read_lock_parses_the_verbatim_fleet_lock_line(lock_path):
    # The earlier version of this test used this script's OWN schema
    # (holder/running/expected_seconds) transliterated into key=value
    # form -- a fixture built from the same misunderstanding as the code,
    # which cannot catch that misunderstanding. This is the ACTUAL
    # byte-for-byte line off a live lock on the review machine, copied,
    # not reconstructed:
    #     session=farabunker-2b pid=4520 purpose=deletion-semantics-fix-wave-5-dirty-tree-gate-flag2 started=2026-09-26T14:46:15Z expect_min=15
    #
    # This proves we SPEAK their language -- one real session's line,
    # correctly aliased. It is a sample of ONE hold, not of the format:
    # its session name, pid length and purpose length are all incidental
    # to that one capture. See the next test for the general case.
    lock_path.write_text(
        "session=farabunker-2b pid=4520 "
        "purpose=deletion-semantics-fix-wave-5-dirty-tree-gate-flag2 "
        "started=2026-09-26T14:46:15Z expect_min=15\n",
        encoding="utf-8",
    )
    assert read_lock(lock_path) == {
        "holder": "farabunker-2b",
        "pid": 4520,
        "running": "deletion-semantics-fix-wave-5-dirty-tree-gate-flag2",
        "started": 1790433975.0,  # 2026-09-26T14:46:15Z, computed independently
        "expected_seconds": 900,  # expect_min=15 * 60
    }


def test_read_lock_parses_awkward_values_within_the_same_field_contract(lock_path):
    # This proves we PARSE the language, not just that one sentence --
    # hand-built (not captured) with deliberately awkward values the
    # single verbatim capture above happens not to exercise: a short pid
    # (single digit), a session name mixing digits and hyphens, a long
    # purpose, an embedded "=" inside a value (partition-on-first-"=" must
    # keep the rest of the value intact, not truncate at it), and TWO
    # unrecognised trailing keys that must both be ignored rather than
    # break anything after them or leak into the result -- the fleet's own
    # suggestion, deliberate tolerance: a parser that skips fields it
    # doesn't recognise survives the next field the fleet adds (the
    # now-gone two-process variant would have been read correctly by this
    # parser instead of mis-parsed by a stricter one). Run directly
    # against the parser before being written here as a fixture, per
    # doctrine -- it parses clean; no value shape here defeated it.
    lock_path.write_text(
        "session=fb-9-x2-alpha pid=1 supervisor_pid=4519 "
        "purpose=a=very-long-purpose-with-an-embedded-equals-sign-to-stress-"
        "separator-and-length-assumptions-in-the-parser "
        "started=2026-09-26T14:46:15Z expect_min=999 unexpected_future_key=zzz\n",
        encoding="utf-8",
    )
    result = read_lock(lock_path)
    assert result == {
        "holder": "fb-9-x2-alpha",
        "pid": 1,
        "running": (
            "a=very-long-purpose-with-an-embedded-equals-sign-to-stress-"
            "separator-and-length-assumptions-in-the-parser"
        ),
        "started": 1790433975.0,
        "expected_seconds": 59940,  # expect_min=999 * 60
    }
    assert "supervisor_pid" not in result and "unexpected_future_key" not in result


def test_acquire_lock_refuses_a_live_holder_written_in_the_plain_format(lock_path):
    # read_lock returning None for a foreign format used to route straight
    # into the vanished-lock steal, so this script would unlink a LIVE
    # holder. Same field names/order/spacing as the verbatim fixture
    # above; only pid and started necessarily vary here (this test's own
    # live pid, a fresh timestamp), since testing liveness needs a real
    # live pid rather than the static one on record.
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock_path.write_text(
        f"session=hand-session pid={os.getpid()} purpose=some-task "
        f"started={now_iso} expect_min=10\n",
        encoding="utf-8",
    )
    with pytest.raises(LockHeld):
        acquire_lock(lock_path, holder="script-session", running="full ladder: tools",
                     expected_seconds=600)
    assert lock_path.exists()  # never unlinked out from under it
    assert read_lock(lock_path)["holder"] == "hand-session"


def test_acquire_lock_treats_a_malformed_lock_as_stale_not_a_crash(lock_path):
    # Well-formed JSON, wrong shape (no pid/started/expected_seconds) --
    # must be judged stale and stolen with an announcement, not raise out
    # of is_stale/_lock_age_exceeds_budget on a missing field.
    lock_path.write_text(json.dumps({"holder": "weird-session"}), encoding="utf-8")
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        acquire_lock(lock_path, holder="rescuer", running="full ladder: tools",
                     expected_seconds=600)
    assert "stealing stale lock" in stderr.getvalue()  # announced, not silent
    assert read_lock(lock_path)["holder"] == "rescuer"


@pytest.mark.parametrize("content", [
    # A lock whose pairs are ALL unrecognised (a full field rename, a
    # foreign tool's lock, or corruption that happens to contain "=")
    # used to parse as an EMPTY dict, not None -- an empty dict is not
    # nothing, so it slipped past both treat-as-held gates and got
    # silently stolen with only the degraded warning.
    "foo=bar baz=qux unrelated_key=zzz\n",
    # json.loads succeeds on bare JSON that isn't an object at all --
    # returning it directly used to crash later the first time something
    # called .get() on it, with a tombstone already captured and the path
    # already empty.
    "42",
])
def test_acquire_lock_treats_unparseable_content_as_held_not_stealable(lock_path, content):
    # Both shapes are epistemically identical to unparseable-in-either-
    # format, which the fleet's own rule says must be HELD, never
    # silently stolen or crashed through.
    lock_path.write_text(content, encoding="utf-8")
    assert read_lock(lock_path) is None
    with pytest.raises(LockHeld):
        acquire_lock(lock_path, holder="thief", running="full ladder: tools",
                     expected_seconds=600)
    assert lock_path.exists()


def test_sweep_runs_on_the_uncontended_fast_path_too(lock_path):
    # The sweep used to run only after the fast-path write failed, i.e.
    # only on a CONTENDED acquisition -- but a capturer killed mid-capture
    # leaves the path EMPTY, so the very NEXT acquirer wins the fast path
    # and would return before a later-positioned sweep ever ran. Moved to
    # the top of acquire_lock so it fires on every acquisition, matching
    # what the skill claims.
    stray = lock_path.parent / "test.lock.captured-99999-deadbeef"
    stray.write_text("orphaned", encoding="utf-8")
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        acquire_lock(lock_path, holder="x", running="y", expected_seconds=600)
    assert "orphaned lock artefact" in stderr.getvalue()


def test_restore_captured_lock_attributes_dispossession_to_the_restored_party(lock_path, dead_pid):
    # The message used to say the party now AT `path` (the fresh,
    # legitimate acquirer, whose claim stands untouched) was
    # "dispossessed". The actually-dispossessed party is the one being
    # restored, whose hold cannot be returned. Exercised via the
    # db-conjunction restore site (process evidence says stale, but the
    # mocked probe says active -- the pre-read gate lets this one
    # through, unlike a genuinely live pid, which the gate would refuse
    # before ever reaching capture): a genuine third party wins the path
    # during the capture window, via a real os.rename hook, before the
    # restore's link-back runs.
    stale_but_db_active = {
        "holder": "restored-party", "pid": dead_pid, "running": "full ladder: tools",
        "started": time.time() - 10, "expected_seconds": 600, "db_port": 5433,
    }
    lock_path.write_text(json.dumps(stale_but_db_active), encoding="utf-8")

    real_rename = os.rename
    injected = {"done": False}

    def rename_then_let_a_third_party_win(src, dst):
        real_rename(src, dst)
        if not injected["done"]:
            injected["done"] = True
            lock_path.write_text(json.dumps({
                "holder": "third-party", "pid": os.getpid(),
                "running": "full ladder: models", "started": time.time(),
                "expected_seconds": 600,
            }), encoding="utf-8")

    stderr = io.StringIO()
    with mock.patch.object(os, "rename", side_effect=rename_then_let_a_third_party_win), \
         mock.patch.object(ladder, "_db_activity_present", return_value=True):
        with contextlib.redirect_stderr(stderr):
            with pytest.raises(LockHeld):
                acquire_lock(lock_path, holder="stealer", running="full ladder: tools",
                             expected_seconds=600)
    output = stderr.getvalue()
    assert "'restored-party'" in output and "DISPOSSESSED" in output
    # The attribution: the dispossessed party named BEFORE the word
    # "DISPOSSESSED" is restored-party (being restored), not third-party
    # (whose claim stands and is described as such).
    dispossessed_clause = output.split("DISPOSSESSED")[0]
    assert "restored-party" in dispossessed_clause
    assert "stands, untouched" in output
    assert read_lock(lock_path)["holder"] == "third-party"  # never overwritten


def test_acquire_lock_refuses_to_steal_when_the_db_probe_finds_activity(lock_path, stale_lock):
    # The conjunction, positive case: process evidence alone says stale
    # (a genuinely dead pid), but the database probe says otherwise --
    # must refuse, not steal, and must put the lock back untouched.
    stale = {**stale_lock, "db_port": 5433}
    lock_path.write_text(json.dumps(stale), encoding="utf-8")

    with mock.patch.object(ladder, "_db_activity_present", return_value=True):
        with pytest.raises(LockHeld):
            acquire_lock(lock_path, holder="thief", running="full ladder: tools",
                         expected_seconds=600)
    assert read_lock(lock_path) == stale  # put back exactly as it was
    assert not any(lock_path.parent.glob("*.captured-*"))  # no leftover tombstone


def test_acquire_lock_warns_loudly_and_proceeds_when_the_db_probe_is_unavailable(lock_path, stale_lock):
    # Degraded case: process evidence says stale, and the probe can't run
    # at all (mocked here as None, the same outcome psql-missing or a
    # refused connection produces) -- must NOT silently fall back to the
    # process-only check; it steals, but announces the weaker evidence.
    stale = {**stale_lock, "db_port": 5433}
    lock_path.write_text(json.dumps(stale), encoding="utf-8")

    stderr = io.StringIO()
    with mock.patch.object(ladder, "_db_activity_present", return_value=None):
        with contextlib.redirect_stderr(stderr):
            acquire_lock(lock_path, holder="rescuer", running="full ladder: tools",
                         expected_seconds=600)
    output = stderr.getvalue()
    assert "WARNING" in output and "STRUCTURALLY invalid" in output
    assert read_lock(lock_path)["holder"] == "rescuer"


def test_acquire_lock_steals_quietly_on_confirmed_no_db_activity(lock_path, stale_lock):
    # activity is False (probe ran, found nothing) -- steal proceeds with
    # the ordinary steal announcement only, no extra warning.
    stale = {**stale_lock, "db_port": 5433}
    lock_path.write_text(json.dumps(stale), encoding="utf-8")

    stderr = io.StringIO()
    with mock.patch.object(ladder, "_db_activity_present", return_value=False):
        with contextlib.redirect_stderr(stderr):
            acquire_lock(lock_path, holder="rescuer", running="full ladder: tools",
                         expected_seconds=600)
    output = stderr.getvalue()
    assert "WARNING" not in output
    assert "stealing stale lock" in output
    assert read_lock(lock_path)["holder"] == "rescuer"


def test_acquire_lock_probes_an_int_port_even_from_a_plain_format_lock(lock_path, dead_pid):
    # db_port isn't part of the fleet's own field contract (it's this
    # script's own conjunction metadata), but a hand-added one on a
    # plain-format lock must still come back as an int for psql's -p,
    # not a bare string -- assert the probe is actually called with one.
    started_iso = datetime.fromtimestamp(time.time() - 10, tz=timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    lock_path.write_text(
        f"session=dead-session pid={dead_pid} purpose=some-task "
        f"started={started_iso} expect_min=10 db_port=5433\n",
        encoding="utf-8",
    )
    assert isinstance(read_lock(lock_path)["db_port"], int)  # confirms the setup

    with mock.patch.object(ladder, "_db_activity_present", return_value=False) as probe:
        acquire_lock(lock_path, holder="rescuer", running="full ladder: tools",
                     expected_seconds=600)
    probe.assert_called_once_with(5433)  # int, not a string


def test_acquire_lock_warns_when_no_db_port_was_ever_recorded(lock_path, stale_lock):
    # The other degraded trigger: a lock (e.g. a hand-written peer lock)
    # with no db_port at all -- must warn and proceed, never crash on
    # the missing field and never silently treat it as a clean probe.
    lock_path.write_text(json.dumps(stale_lock), encoding="utf-8")

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        acquire_lock(lock_path, holder="rescuer", running="full ladder: tools",
                     expected_seconds=600)
    assert "WARNING" in stderr.getvalue()
    assert read_lock(lock_path)["holder"] == "rescuer"


def test_acquire_lock_floors_a_tiny_expected_duration_so_it_cannot_self_stale(lock_path):
    # expected_seconds=0 would make even a fresh lock's own 3x budget
    # zero, so it goes stale under itself the instant a waiter checks.
    acquire_lock(lock_path, holder="quick", running="hotfix ladder: identity",
                 expected_seconds=0)
    info = read_lock(lock_path)
    assert info["expected_seconds"] >= 60
    assert is_stale(info, now=info["started"] + 1) is False


def _require_reachable_postgres(port: int) -> None:
    """VISIBLE skip, not a silent one -- a reader must be able to tell
    "this did not run" from "this passed" in the suite's own output.
    Used only by the two _shared_db_presence tests below, which need a
    real Postgres to open real connections against; the rest of this
    file needs no live database at all."""
    if shutil.which("psql") is None:
        pytest.skip("psql not on PATH -- cannot exercise _shared_db_presence for real")
    env = dict(os.environ)
    env.setdefault("PGPASSWORD", "farabunker")
    try:
        result = subprocess.run(
            ["psql", "-h", "localhost", "-p", str(port), "-U", "farabunker",
             "-d", "postgres", "-tAc", "SELECT 1"],
            capture_output=True, text=True, timeout=5, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip(f"no reachable Postgres on port {port} in this environment")
    if result.returncode != 0:
        pytest.skip(f"no reachable Postgres on port {port} in this environment")


def test_shared_db_presence_reads_clear_with_a_real_application_connection():
    # Manufactures REAL state and drives _shared_db_presence through its
    # REAL subprocess path -- not a synthetic row, not a re-implementation
    # of the filter, not a mock standing in for the database client. A
    # test that only re-states the query cannot fail when the query is
    # wrong, which is exactly how the "every non-system database" defect
    # shipped in this same commit's own permit test. Reproduces the
    # measured failure shape directly: a real connection to a real
    # APPLICATION-named database (not test-prefixed) must read CLEAR --
    # a preview stack's own web/worker/watcher hold idle connections like
    # this one on every port with a preview stack up, which is every box
    # this repository runs on, so this is the case that must never read
    # busy.
    #
    # Port 5433 only, never 5432 (the live application's own port,
    # untouchable by any test). Runs FOR REAL in the suite, not
    # hand-verified and parked: hand verification is true once, at the
    # moment somebody runs it, while this assertion is true every run --
    # and the busy-direction test below is the one that would catch the
    # NEXT over-widening or filter typo, the mirror image of the test
    # whose absence let this one through.
    #
    # SAFE TO RUN HERE BECAUSE SERIALIZED BY THE SLOT LOCK, not because
    # of anything about this test's own brevity: every ladder run holds
    # the machine-wide slot lock for its whole chain, including whatever
    # test module is executing right now, so no OTHER session that has
    # adopted the lock protocol can be sampling this database at its own
    # run gate while this connection is open -- that peer is parked at
    # lock ACQUISITION, the correct and already-announced state for a
    # machine whose slot is taken, and never reaches its probe loop at
    # all. A session that has not adopted the protocol has no gate here
    # to mislead. The unmistakable scratch name, the brief hold, and the
    # crash-surviving teardown below are defence in depth against a
    # PROTOCOL VIOLATION (something touching this port outside the slot
    # lock) -- not the primary containment, which is the lock itself.
    #
    # Teardown: a crashed test needs no invented timeout, because a
    # connection dies with its process and Postgres reaps the socket on
    # its own -- the residual hazard is a HANG (this process wedged,
    # holding the connection AND the slot lock), and that is exactly the
    # state the staleness conjunction and the orphan-visibility sweep
    # already handle, not a new failure mode. A leftover scratch
    # DATABASE, if the DROP below is ever missed, does not matter to the
    # gate at all -- presence is judged on CONNECTIONS, never on the
    # catalogue, so no future cleanup of the database list is needed
    # either. The connection is TERMINATED the instant the assertion
    # finishes rather than left to run out its own sleep -- the busy
    # window this test manufactures should be exactly as wide as the
    # assertion needs, not as wide as a round number happened to be.
    #
    # PRECONDITION, found by running this in REVERSE module-collection
    # order rather than reasoned about: the run plan executes its whole
    # module list in ONE process, forward order runs this before any
    # database test ever opens a connection, but reverse order runs it
    # LAST -- by which point the enclosing test session's own
    # session-persistent connection to ITS test database is open on this
    # same port, and this probe (by design) matches ANY test-named
    # database, including that one. The lock's containment doesn't
    # reach this case: it parks PEER sessions, not the suite this test
    # is running inside, which holds its own test-database connection
    # within the very slot this test holds. So: check for a
    # pre-existing test-named connection FIRST, before manufacturing
    # anything, and skip visibly (naming what was found) rather than
    # asserting through it -- weakening the assertion to tolerate a
    # test-named row would stop proving the permit outcome at all and
    # make the over-widening defect this exists to catch nondeterministic
    # whenever both an application and a test row happen to coexist.
    _require_reachable_postgres(5433)
    preexisting = ladder._shared_db_presence(5433)
    if preexisting is not None and preexisting is not ladder._PROBE_UNAVAILABLE:
        pytest.skip(
            f"a test-named database already has a connection on port 5433 "
            f"(pid {preexisting['pid']} on {preexisting['datname']!r}, "
            f"{preexisting['state']!r}) -- the enclosing suite's own test "
            f"database, most likely, in reverse collection order. The clear "
            f"condition cannot be manufactured while that connection exists."
        )
    dbname = f"ladder_gate_probe_clear_scratch_{os.getpid()}"
    env = dict(os.environ)
    env.setdefault("PGPASSWORD", "farabunker")
    psql_base = ["psql", "-h", "localhost", "-p", "5433", "-U", "farabunker"]
    subprocess.run(
        [*psql_base, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"],
        capture_output=True, text=True, timeout=10, env=env,
    )  # self-heal: a survivor of a previous forced kill must not error this one
    subprocess.run([*psql_base, "-d", "postgres", "-c", f"CREATE DATABASE {dbname}"],
                    capture_output=True, text=True, timeout=10, env=env, check=True)
    conn = subprocess.Popen(
        [*psql_base, "-d", dbname, "-c", "SELECT pg_sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    try:
        time.sleep(1.5)  # let the connection register in pg_stat_activity
        assert ladder._shared_db_presence(5433) is None
    finally:
        conn.terminate()
        conn.wait(timeout=10)
        subprocess.run(
            [*psql_base, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"],
            capture_output=True, text=True, timeout=10, env=env,
        )


def test_shared_db_presence_reads_busy_with_a_real_test_db_connection():
    # The mirror image of the test above, and the one the reviewer
    # specifically required run for real rather than be parked as
    # hand-verified -- see that test's own comment for why this is safe
    # to do on the shared port (serialized by the slot lock) and what
    # its teardown does and doesn't need to guard against.
    #
    # This is the one real side effect worth naming plainly: for as long
    # as this connection is open, it manufactures the exact condition
    # that makes every session's run gate decline -- which is fine under
    # the slot-lock reasoning above, but is why the scratch name must be
    # unmistakably this test's own (never a plausible real suite name)
    # and the hold as brief as this file's style allows.
    _require_reachable_postgres(5433)
    dbname = f"test_ladder_gate_probe_busy_scratch_{os.getpid()}"
    env = dict(os.environ)
    env.setdefault("PGPASSWORD", "farabunker")
    psql_base = ["psql", "-h", "localhost", "-p", "5433", "-U", "farabunker"]
    subprocess.run(
        [*psql_base, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"],
        capture_output=True, text=True, timeout=10, env=env,
    )  # self-heal: a survivor of a previous forced kill must not error this one
    subprocess.run([*psql_base, "-d", "postgres", "-c", f"CREATE DATABASE {dbname}"],
                    capture_output=True, text=True, timeout=10, env=env, check=True)
    conn = subprocess.Popen(
        [*psql_base, "-d", dbname, "-c", "SELECT pg_sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    try:
        time.sleep(1.5)
        result = ladder._shared_db_presence(5433)
        assert result is not None and result is not ladder._PROBE_UNAVAILABLE
    finally:
        conn.terminate()
        conn.wait(timeout=10)
        subprocess.run(
            [*psql_base, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"],
            capture_output=True, text=True, timeout=10, env=env,
        )


def test_acquire_lock_when_database_clear_permits_the_run_on_a_clear_machine(lock_path):
    # THE PERMIT PATH, not the refuse path -- worth calling out by name,
    # because the refusal path announces, releases and retries and is
    # loud and easy to exercise by construction, while the permit path
    # on a genuinely clear machine produces no output worth noticing and
    # is exactly the case a test suite can quietly stop covering. A gate
    # that wrongly refuses costs waiting; a gate that wrongly permits
    # costs two suites on one machine -- the more expensive failure, and
    # the one this test exists to keep visible. Asserts the actual
    # OBSERVABLE OUTCOME of permitting -- the lock genuinely held, with
    # this process's own pid, ready for the run that follows -- not
    # merely that the probe function returned a clear value on paper,
    # and confirms no decline/retry cycle happened first.
    stderr = io.StringIO()
    with mock.patch.object(ladder, "_shared_db_presence", return_value=None) as probe:
        with contextlib.redirect_stderr(stderr):
            ladder._acquire_lock_when_database_clear(
                lock_path, holder="waiter", running="full ladder: tools",
                expected_seconds=600, db_port=None,
            )
    assert probe.call_count >= 1  # the gate genuinely consulted the probe
    assert "declining" not in stderr.getvalue()  # clean permit, no retry cycle
    info = read_lock(lock_path)
    assert info is not None and info["pid"] == os.getpid()  # lock genuinely held


def test_acquire_lock_when_database_clear_releases_the_won_lock_on_decline(lock_path):
    # The order this whole gate exists to enforce: ACQUIRE, then PROBE,
    # then if busy, RELEASE what was just won (never hold it while
    # waiting) and retry -- confirmed by checking the lock file's
    # existence from inside a mocked time.sleep, i.e. exactly during the
    # window between the decline and the retry.
    observed = {}
    calls = {"n": 0}

    def fake_presence(port):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"pid": 4823, "datname": "farabunker_other",
                     "state": "idle in transaction", "since": "2026-09-26T14:46:15Z"}
        return None

    def fake_sleep(seconds):
        observed["lock_exists_while_waiting"] = lock_path.exists()

    with mock.patch.object(ladder, "_shared_db_presence", side_effect=fake_presence), \
         mock.patch("time.sleep", side_effect=fake_sleep):
        ladder._acquire_lock_when_database_clear(
            lock_path, holder="waiter", running="full ladder: tools",
            expected_seconds=600, db_port=None, retry_seconds=0.01,
        )
    assert observed.get("lock_exists_while_waiting") is False
    assert read_lock(lock_path) is not None  # held after the successful retry


def test_acquire_lock_when_database_clear_warns_after_repeated_declines(lock_path):
    # THE DISTINCT ANNOUNCEMENT must NAME the connection (pid, database,
    # state, since) rather than merely say the resource looks busy --
    # actionable without the recipient having to re-derive anything.
    calls = {"n": 0}

    def fake_presence(port):
        calls["n"] += 1
        if calls["n"] <= ladder._DB_GATE_WARN_THRESHOLD:
            return {"pid": 4823, "datname": "farabunker_other",
                     "state": "idle in transaction", "since": "2026-09-26T14:46:15Z"}
        return None

    stderr = io.StringIO()
    with mock.patch.object(ladder, "_shared_db_presence", side_effect=fake_presence), \
         mock.patch.object(ladder, "_other_pytest_matches", return_value=[]):
        with contextlib.redirect_stderr(stderr):
            ladder._acquire_lock_when_database_clear(
                lock_path, holder="waiter", running="full ladder: tools",
                expected_seconds=600, db_port=None, retry_seconds=0.01,
            )
    output = stderr.getvalue()
    assert output.count("declining to start") == ladder._DB_GATE_WARN_THRESHOLD
    assert "looked busy for" in output
    assert "4823" in output and "farabunker_other" in output
    assert "idle in transaction" in output and "2026-09-26T14:46:15Z" in output


def test_acquire_lock_when_database_clear_gives_up_after_the_bound_rather_than_waiting_forever(lock_path):
    # THE BOUND: release-and-retry is a poll loop by another name: cap it
    # and exit non-zero (via DatabaseNeverClear) rather than waiting
    # forever or, worse, proceeding anyway once the cap is hit.
    def always_busy(port):
        return {"pid": 111, "datname": "db", "state": "active", "since": "t0"}

    with mock.patch.object(ladder, "_shared_db_presence", side_effect=always_busy), \
         mock.patch.object(ladder, "_other_pytest_matches", return_value=["x"]):
        with pytest.raises(DatabaseNeverClear):
            ladder._acquire_lock_when_database_clear(
                lock_path, holder="waiter", running="full ladder: tools",
                expected_seconds=600, db_port=None, retry_seconds=0.001,
                max_attempts=3,
            )
    assert not lock_path.exists()  # never left holding a lock it gave up on


def test_acquire_lock_when_database_clear_names_which_ports_when_partly_unavailable(lock_path):
    # The message used to say the probe "could not run on any shared
    # port" whenever at least one port was unavailable, even if the
    # REST probed clear -- overstating what actually happened. Must name
    # which ports were unavailable and which were confirmed clear, and
    # must keep doing so under the single merged message that now covers
    # both the partial and the total case.
    def mixed(port):
        return ladder._PROBE_UNAVAILABLE if port == 5432 else None

    stderr = io.StringIO()
    with mock.patch.object(ladder, "_shared_db_presence", side_effect=mixed):
        with contextlib.redirect_stderr(stderr):
            ladder._acquire_lock_when_database_clear(
                lock_path, holder="waiter", running="full ladder: tools",
                expected_seconds=600, db_port=None,
            )
    output = stderr.getvalue()
    assert "could not run on port(s) [5432]" in output
    assert "probed clear" in output and "[5433]" in output
    assert "on any shared port" not in output  # the overstated phrasing
