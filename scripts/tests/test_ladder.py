"""Test for scripts/ladder.py's run-plan builder.

build_run_plan is pure (no subprocess, no I/O), so this imports the module
directly rather than shelling out -- contrast scripts/tests/test_preview.py,
which must shell out because scripts/preview is a bash script.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ladder import build_run_plan  # noqa: E402


def test_build_run_plan_returns_eight_runs_full_and_two_hotfix():
    full_names = [run["name"] for run in build_run_plan("full", ["identity"])]
    assert full_names == [
        "r1-forward", "r1-reverse",
        "r2-vm-full", "r2-v-full", "r2-vm-scoped", "r2-v-scoped",
        "r3-personal", "r3-enterprise",
    ]

    hotfix_names = [run["name"] for run in build_run_plan("hotfix", [])]
    assert hotfix_names == ["r1-forward", "r1-reverse"]
