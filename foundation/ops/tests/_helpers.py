"""Shared test helpers for foundation/ops/tests.

Plain importable module -- **not** a `conftest.py` (the repo forbids
those anywhere). Each test module imports what it needs from here
explicitly, by name.

`_is_test_file` and `REPO_ROOT` used to be declared identically in both
`test_import_law.py` and `test_column_boundaries.py` (CQ-14).
`test_column_boundaries.py`'s own copy justified the duplication by the
cross-app `_helpers.py`-duplication rule -- but that rule polices app
and COLUMN boundaries (one column's test scaffolding becoming
load-bearing for another's), and these two modules are siblings in one
directory, not two columns. Every other test package in the repo solves
exactly this with a `_helpers.py`; this one had none until now.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings

REPO_ROOT = Path(settings.BASE_DIR)


def _is_test_file(relative: str) -> bool:
    """Test modules are exempt from the gates in this package -- anything
    under a `tests/` directory, any `test_*.py`/`*_test.py` module, or a
    `conftest.py` (none exist in this repo, but the check costs
    nothing)."""
    parts = Path(relative).parts
    if "tests" in parts:
        return True
    name = Path(relative).name
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
