"""Shared test helpers for `models/queue/tests`.

NEW IN IA-1. The six functions this module used to type as its own
COPY of `identity/tests/_helpers.py`'s (`posture`, `seed_sweep_posture`,
`make_user`, `make_admin`, `user_principal`, `sign_in`) moved to
`identity/testing.py` (consolidation round 2, FIX-NOW 1) -- a shared,
importable module, not a `conftest.py` (the repo forbids those
anywhere; this is neither magic nor autouse). Re-exported here so every
existing `from models.queue.tests._helpers import ...` keeps working
unchanged.

`make_queue_job` joins the block in IA-2 (T17 follow-up): it is a plain
`InferenceJob` row builder with no route-matrix-specific shape (it used
to live only in `identity/tests/_helpers.py`, reached for a second time
via that module's private import path -- which
`foundation/ops/tests/test_import_law.py` legally permits test files to
do, but which is one private-scaffolding reach too many for a helper
this generic), so it moved beside its five siblings in
`identity/testing.py` and is re-exported here BY NAME, same as them.

`registry_reset_fixture` joins the block in C-59: the snapshot/clear/
restore `reset_registry` fixture this app's own test files carried
inline (`test_backend.py`, `test_worker.py`) is one instance of a shape
`models/registry/tests/` also carries over a different dict -- the
factory lives in `models/contracts/testing.py`, not `identity/testing.py`,
because both registries it resets are `models.contracts`'s own (see that
module's docstring for why).
"""
from __future__ import annotations

from identity.testing import (  # noqa: F401 -- re-exported for existing imports
    make_admin, make_queue_job, make_user, posture, seed_sweep_posture, sign_in,
    user_principal,
)
from models.contracts.testing import registry_reset_fixture  # noqa: F401 -- re-exported
