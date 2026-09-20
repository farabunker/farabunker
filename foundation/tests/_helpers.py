"""Shared test helpers for foundation/tests.

A plain importable module -- **not** a `conftest.py` (the repo forbids
those anywhere). Every other test package has one of these
(`foundation/setup/tests/_helpers.py`, `foundation/landing/tests/
_helpers.py`, `models/registry/tests/_helpers.py`, and the rest), and
each re-exports the identity fixtures from `identity.testing` -- the
actual seam -- rather than reaching into another column's own test
scaffolding.

`bind_role` reaches into `models.registry.models` directly, which is a
TEST-only reach and a sanctioned one: production code in `foundation/`
may never import it (import law rule 2, pinned by
`foundation/ops/tests/test_import_law.py`, which exempts everything under
a `tests/` directory by design).
"""
from __future__ import annotations

import itertools

from identity.testing import (  # noqa: F401 -- re-exported for existing imports
    make_admin, make_user, posture, seed_sweep_posture, sign_in,
)
from models.registry.models import ModelConnection, RoleBinding

_names = itertools.count()


def bind_role(role_key: str) -> RoleBinding:
    """Bind `role_key` to a freshly registered connection.

    Nothing about the connection matters beyond its existing: the
    availability signal asks whether a binding HAS one, never whether the
    engine behind it answers.
    """
    connection = ModelConnection.objects.create(
        name=f"a connection {next(_names)}",
        engine="ollama",
        endpoint="http://localhost:11434",
        model_id="an-identifier",
        capabilities=["chat"],
    )
    return RoleBinding.objects.create(role_key=role_key, connection=connection)


def clear_bindings() -> None:
    """No binding rows at all -- migration 0002 seeds a connection+binding
    pair for every role an operator pinned from the environment, so
    whether a fresh database starts out with something bound depends on
    the machine."""
    RoleBinding.objects.all().delete()
    ModelConnection.objects.all().delete()
