"""Shared test helpers for foundation/landing/tests.

A plain importable module -- **not** a `conftest.py` (the repo forbids
those anywhere). Matches every other test package's own `_helpers.py`
(`foundation/setup/tests/_helpers.py`, `models/registry/tests/
_helpers.py`, and the rest): identity fixtures are re-exported from
`identity.testing`, the actual seam, rather than reached for in another
column's test scaffolding.

The two binding helpers are DEFINED in `foundation/tests/_helpers.py` and
merely re-exported here: they reach into `models.registry.models`, which
is a TEST-only reach and a sanctioned one (import law rule 2 is pinned by
`foundation/ops/tests/test_import_law.py`, which exempts everything under
a `tests/` directory by design), and one definition beats two identical
ones. The landing view itself imports nothing from `models/` -- it
renders off the availability context processor.
"""
from __future__ import annotations

from identity.testing import (  # noqa: F401 -- re-exported for existing imports
    make_admin, make_user, posture, seed_sweep_posture, sign_in,
)
# ONE DEFINITION, RE-EXPORTED -- not a second copy. `foundation/tests/
# _helpers.py` is the same column, so no import-law question arises, and
# `identity/testing.py`'s own docstring is a long argument against exactly
# the duplication this used to be (four `_helpers.py` copies each typing
# the same functions). The per-package `_helpers.py` convention is about
# where a test module IMPORTS FROM, not about typing the body again.
from foundation.tests._helpers import (  # noqa: F401 -- re-exported
    bind_role, clear_bindings,
)
