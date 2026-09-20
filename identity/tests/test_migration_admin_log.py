"""`0002_repoint_admin_log_fk` -- the precondition and the repoint.

The precondition is exercised BOTH WAYS (a row present raises with the
operator-readable message; no rows repoints), because a migration that
assumes it is safe without checking is a migration that eventually is
not.

These tests call the migration's own module-level functions directly
against the live test database rather than driving `migrate`: the test
database is already fully migrated by the time a test runs, so
re-running the migration through the executor would be a no-op. The
functions are the unit; the four-way `migrate --plan` gate in Task 16 is
what proves they are wired into the graph.

`TestTheConstraintLookup.test_it_finds_and_repoints_a_preexisting_fk`
does not merely read the catalogue on this box: on a test database,
`admin.0001_initial`'s own `swappable_dependency` already built
`django_admin_log.user_id` against `identity_user`, so there is no
pre-swap FK sitting around to find. The test manufactures the pre-swap
situation by hand -- a stand-in `auth_user` table and a hand-pointed FK
-- and then proves `repoint` moves it back to `identity_user`, because a
test that only ever sees the post-repoint state never exercises the
repoint path at all.

`test_it_leaves_a_correctly_pointed_fk_untouched_on_a_fresh_install`
covers the other direction: `repoint` must not merely return early on a
box that needs nothing done, it must leave the existing constraint
byte-for-byte alone (same name, same target) -- because `repoint_is_needed`
now asks the catalogue what the FK actually references (not just whether
`auth_user` exists), and that check has to be proven honest on the one
kind of box where it matters most: one that already has real audit rows.
"""
from __future__ import annotations

import pytest
from django.db import connection

from identity.migrations import _0002_helpers as helpers

pytestmark = pytest.mark.django_db


class _FakeSchemaEditor:
    """Just enough of Django's `SchemaEditor` for `helpers.repoint`,
    which only ever reaches through `.connection`."""

    def __init__(self, connection):
        self.connection = connection


class TestThePrecondition:
    def test_it_passes_on_an_empty_box(self):
        """Neither table can hold a row, because no login has ever
        existed on this platform. Checked anyway."""
        helpers.refuse_if_rows_exist(connection)

    def test_it_raises_with_an_operator_readable_instruction(self, monkeypatch):
        monkeypatch.setattr(helpers, "_row_count", lambda conn, table: 3)
        with pytest.raises(RuntimeError) as exc:
            helpers.refuse_if_rows_exist(connection)
        message = str(exc.value)
        assert "auth_user" in message or "django_admin_log" in message
        assert "3" in message
        # It must tell the operator what to DO, not merely that it
        # stopped -- a migration that refuses without an instruction is
        # a migration an operator forces past.
        assert "backup" in message.lower()


class TestTheConstraintLookup:
    def test_it_finds_and_repoints_a_preexisting_fk(self):
        """Recreate the pre-swap state this migration exists to fix --
        `django_admin_log.user_id` pointed at a stand-in `auth_user`
        table -- then prove the catalogue lookup finds that real
        constraint (never a hard-coded name) and `repoint` moves it to
        `identity_user`. Runs inside the test's own transaction, so the
        DDL below is rolled back with everything else at teardown."""
        original_name = helpers.admin_log_user_fk_name(connection)
        assert original_name is not None

        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE "auth_user" (id bigserial PRIMARY KEY)')
            cursor.execute(
                f'ALTER TABLE "django_admin_log" DROP CONSTRAINT "{original_name}"'
            )
            cursor.execute(
                'ALTER TABLE "django_admin_log" '
                'ADD CONSTRAINT "django_admin_log_user_id_fake_fk" '
                'FOREIGN KEY (user_id) REFERENCES "auth_user" (id) '
                'DEFERRABLE INITIALLY DEFERRED'
            )

        # The precondition still passes: the stand-in `auth_user` and
        # `django_admin_log` are both empty.
        helpers.refuse_if_rows_exist(connection)

        found = helpers.admin_log_user_fk_name(connection)
        assert found is not None and found.startswith("django_admin_log_")
        assert helpers._referenced_table(connection, found) == "auth_user"
        assert helpers.repoint_is_needed(connection) is True

        helpers.repoint(apps=None, schema_editor=_FakeSchemaEditor(connection))

        repointed_name = helpers.admin_log_user_fk_name(connection)
        assert repointed_name is not None
        assert helpers._referenced_table(connection, repointed_name) == "identity_user"

    def test_a_fresh_install_is_a_no_op(self, monkeypatch):
        """On a fresh database `admin.0001_initial`'s own
        `swappable_dependency` already built the FK against
        `identity_user`, so there is nothing to repoint and the
        migration must do nothing rather than fail."""
        monkeypatch.setattr(helpers, "_table_exists",
                            lambda conn, table: table != "auth_user")
        assert helpers.repoint_is_needed(connection) is False

    def test_it_leaves_a_correctly_pointed_fk_untouched_on_a_fresh_install(self):
        """`repoint` on a box that needs nothing done must not merely
        return early -- it must leave the existing FK exactly as it
        found it. This is the honesty check on `repoint_is_needed`
        reading the catalogue rather than inferring from table
        existence: on an upgraded box `auth_user` is never dropped, so
        the ONLY thing telling a done box from an undone one is what the
        FK actually references."""
        before = helpers.admin_log_user_fk_name(connection)
        assert before is not None
        assert helpers._referenced_table(connection, before) == "identity_user"
        assert helpers.repoint_is_needed(connection) is False

        helpers.repoint(apps=None, schema_editor=_FakeSchemaEditor(connection))

        after = helpers.admin_log_user_fk_name(connection)
        assert after == before
        assert helpers._referenced_table(connection, after) == "identity_user"
