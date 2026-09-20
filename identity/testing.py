"""Test support shared across every column -- not test code itself.

FIX-NOW 1 (consolidation round 2): four `_helpers.py` copies --
`identity/tests/_helpers.py`, `models/queue/tests/_helpers.py`,
`tools/rag/tests/_helpers.py`, `tools/vision/tests/_helpers.py` -- each
typed the SAME six functions (`posture`, `seed_sweep_posture`,
`make_user`, `make_admin`, `user_principal`, `sign_in`), kept
byte-similar by round 1's realignment pass. Round 1 treated the
no-conftest rule as forbidding a shared import; it does not -- a
`conftest.py` is a magic, autouse-discovered file, and this is neither:
every caller imports it BY NAME, exactly like any other module. All
three non-identity copies already imported `identity.models` and
`identity.contracts.principals` directly (legal: `test_import_law.py`
exempts test files), so they were already coupled to identity -- they
were retyping its helpers, not avoiding the dependency.

Importable by any column's TEST files. Production code must never
import it: `foundation/ops/tests/test_import_law.py`'s
`IDENTITY_PERMITTED` allowlist does not name this module, so a
production import of it is caught the same way a production import of
`identity.services` already is.

Each package's own `_helpers.py` re-exports what it uses from here and
keeps only what is genuinely package-specific (row builders, fakes,
fixtures for that column's own models) -- so `from tools.rag.tests.
_helpers import make_admin` keeps working exactly as before; only the
body it resolves to moved.

`posture`'s `library_posture=` keyword has real callers only in
`identity/tests/test_services.py` and `identity/tests/test_settings_page.py`
-- it was dead weight in the three copies that carried it with no
caller (round-2 FIX-NOW 6); here there is exactly one copy of the
function, and it is the one column that actually uses the keyword.
"""
from __future__ import annotations

import contextlib
import itertools
import os

from django.contrib.auth import get_user_model

from identity.contracts.postures import LIBRARY_OPEN, POSTURE_OPEN
from identity.contracts.principals import Principal
from identity.models import IdentitySettings

_names = itertools.count()

# The sweep variable, read HERE and nowhere else in the tree. Production
# code must never read it: that would reintroduce the second truth the
# database row exists to avoid (spec section 3.2).
SWEEP_POSTURE_ENV = "FARABUNKER_TEST_POSTURE"


@contextlib.contextmanager
def posture(name: str, *, admin_sees_content: bool | None = None,
            library_posture: str | None = None):
    """Run the block with the box in `name` posture, then restore.

    Writes the singleton row directly rather than going through
    `identity.services.set_posture`, deliberately: `set_posture` REFUSES
    a switch away from `open` with no active superuser, with `DEBUG` on,
    or with a default `SECRET_KEY` (spec section 14) -- and a test that
    merely needs the box to BE in a posture should not have to satisfy
    three production refusals to get there. The tests that exercise
    those refusals call `set_posture` on purpose.
    """
    row = IdentitySettings.get_solo()
    before = (row.posture, row.library_posture, row.admin_sees_content)
    row.posture = name
    if admin_sees_content is not None:
        row.admin_sees_content = admin_sees_content
    if library_posture is not None:
        row.library_posture = library_posture
    row.save()
    try:
        yield row
    finally:
        row.posture, row.library_posture, row.admin_sees_content = before
        row.save()


def seed_sweep_posture() -> None:
    """Seed the settings row from `FARABUNKER_TEST_POSTURE` when it is
    set. Called from each package's autouse fixture; a test that pins
    its own posture always wins, the same precedence the feature flags
    already use."""
    name = os.environ.get(SWEEP_POSTURE_ENV, "").strip()
    if not name:
        return
    row = IdentitySettings.get_solo()
    if row.posture != name:
        row.posture = name
        row.save()


def make_user(**overrides):
    """An ordinary active account."""
    fields = {"username": f"member-{next(_names)}", "password": "not-a-real-password"}
    fields.update(overrides)
    password = fields.pop("password")
    user = get_user_model()(**fields)
    user.set_password(password)
    user.save()
    return user


def make_admin(**overrides):
    """An active superuser -- the principal every `S` route answers to."""
    overrides.setdefault("username", f"admin-{next(_names)}")
    overrides["is_superuser"] = True
    overrides["is_staff"] = True
    return make_user(**overrides)


def user_principal(user) -> Principal:
    """The principal `principal_for_request` mints for `user`: the
    PRIMARY KEY as a string, never the username."""
    return Principal("user", str(user.pk))


def sign_in(client, user, password: str = "not-a-real-password") -> None:
    """Sign `user` in on `client` through the real login machinery, so
    the session cookie a test carries is the one a browser would."""
    assert client.login(username=user.username, password=password)


def reset_settings() -> None:
    """Put the singleton back to shipped defaults. Used by autouse
    fixtures in every package whose tests write it directly.

    MOVED HERE from `identity/tests/_helpers.py` in IA-2: four more
    packages need it now, and `identity/tests/` is one package's private
    scaffolding. `identity/tests/_helpers.py` re-exports it, so every
    existing `from identity.tests._helpers import reset_settings` keeps
    working and only the body it resolves to moved -- the same move
    `posture`/`make_user`/`sign_in` already made in IA-1.
    """
    row = IdentitySettings.get_solo()
    row.posture = POSTURE_OPEN
    row.library_posture = LIBRARY_OPEN
    row.admin_sees_content = False
    row.save()


def make_group(**overrides):
    """An `auth.Group`, unchanged -- this platform uses groups for
    MEMBERSHIP only and never reads `Group.permissions`."""
    from django.contrib.auth.models import Group
    fields = {"name": f"group-{next(_names)}"}
    fields.update(overrides)
    return Group.objects.create(**fields)


def make_entitlement(**overrides):
    """A named permission. Written directly, not through
    `identity.services.create_entitlement`, for the same reason `posture`
    writes the settings row directly: a test that merely needs an
    entitlement to EXIST should not have to satisfy the service's
    refusals to get one."""
    from identity.models import Entitlement
    fields = {"name": f"entitlement-{next(_names)}"}
    fields.update(overrides)
    return Entitlement.objects.create(**fields)


def grant(entitlement, *, user=None, group=None, role="member"):
    """One `EntitlementGrant`. Exactly one of `user`/`group`, or the
    XOR check constraint refuses the row."""
    from identity.models import EntitlementGrant
    return EntitlementGrant.objects.create(
        entitlement=entitlement, user=user, group=group, role=role)


def make_queue_job(**overrides):
    """A `jobs.InferenceJob` row, resolved through `apps.get_model` rather
    than imported directly -- `identity/testing.py` may not import
    `models.queue` (import law rule 4: identity imports no other column),
    and this is generic scaffolding, not a package-specific row builder --
    any column's tests may reach for a raw queue row.

    MOVED HERE from `identity/tests/_helpers.py` (IA-2 T17 follow-up): it
    was already a plain `InferenceJob` builder with no route-matrix-specific
    shape, so it belongs beside `make_group`/`make_entitlement` rather than
    with that module's genuinely package-specific row builders.
    `identity/tests/_helpers.py` re-exports it, so
    `from identity.tests._helpers import make_queue_job` keeps working --
    only the body it resolves to moved.

    `priority` is `PositiveIntegerField()` with NO default
    (`models/queue/models.py:89`): it is resolved at enqueue time by the
    priority chain, so a row built directly must supply one or the insert
    raises IntegrityError. `state` takes the module-level `QUEUED`
    constant's value -- this app has no `InferenceJob.State` inner class.
    """
    from django.apps import apps
    fields = dict(kind="rag.ask", payload={}, state="queued", priority=100)
    fields.update(overrides)
    return apps.get_model("jobs.InferenceJob").objects.create(**fields)
