"""Test for scripts/ladder.py's run-plan builder.

build_run_plan never shells out (its only I/O is one stderr line when the
scoped pair is dropped), so this imports the module directly rather than
shelling out -- contrast scripts/tests/test_preview.py, which must shell
out because scripts/preview is a bash script.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ladder import FULL_MODULES, build_run_plan  # noqa: E402

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
