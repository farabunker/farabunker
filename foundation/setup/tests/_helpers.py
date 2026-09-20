"""Shared test helpers for foundation/setup/tests.

A plain importable module -- **not** a `conftest.py` (the repo forbids
those anywhere). Each test module imports what it needs from here
explicitly, by name, matching every other test package's own `_helpers.py`
(`tools/vision/tests/_helpers.py`, `tools/rag/tests/_helpers.py`,
`models/registry/tests/_helpers.py`, `agents/chat/tests/_helpers.py`).

Whole-branch review, GC12: `foundation/setup/tests/test_views.py` used to
reach across a column boundary with `from identity.tests._helpers import
...` -- `identity/tests/` is that column's OWN test scaffolding, not a
published seam, and the per-package `_helpers.py`-duplication rule exists
precisely so one package's test fixtures never become load-bearing for
another's. This file is the missing re-export point; every name below
already lives in `identity.testing`, the actual seam.
"""
from __future__ import annotations

from identity.testing import (  # noqa: F401 -- re-exported for existing imports
    make_admin, make_user, posture, seed_sweep_posture, sign_in,
)
