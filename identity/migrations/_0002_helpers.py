"""The bodies `0002_repoint_admin_log_fk` runs.

A SIBLING MODULE, not inline in the migration, for one reason: a
migration's `RunPython` callables are not importable by a test without
driving the migration executor, and this migration's precondition is
exactly the kind of code that must be exercised BOTH ways before it runs
against a box holding a document library.

Underscore-prefixed so Django's migration loader never mistakes it for a
migration.
"""
from __future__ import annotations

_ADMIN_LOG = "django_admin_log"
_OLD_USER = "auth_user"
_NEW_USER = "identity_user"

_HALF_APPLIED = (
    "This box's identity.0001_initial has already applied, so "
    "AUTH_USER_MODEL is identity.User -- but django_admin_log.user_id "
    "still points at the old auth_user table until this migration "
    "completes."
)


def _table_exists(connection, table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [f"public.{table}"])
        return bool(cursor.fetchone()[0])


def _row_count(connection, table: str) -> int:
    if not _table_exists(connection, table):
        return 0
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM "{table}"')  # noqa: S608 -- fixed literals
        return int(cursor.fetchone()[0])


def _referenced_table(connection, constraint_name: str) -> str | None:
    """The table a foreign-key constraint's `REFERENCES` points at, read
    from the catalogue by constraint name.

    This is what makes `repoint_is_needed`/`repoint` honest about WHAT a
    found constraint points at rather than just THAT one exists: a
    constraint on `django_admin_log.user_id` that already references
    `identity_user` (because a previous run of this migration already
    repointed it, and `auth_user` was never dropped) must never be
    mistaken for one still needing to move.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rel2.relname
              FROM pg_constraint con
              JOIN pg_class rel2 ON rel2.oid = con.confrelid
             WHERE con.conname = %s
            """,
            [constraint_name],
        )
        row = cursor.fetchone()
    return row[0] if row else None


def refuse_if_rows_exist(connection) -> None:
    """Refuse to repoint if either table holds a row.

    NEITHER CAN, on any box this platform has ever shipped: no login has
    ever existed, so `auth_user` is empty and nothing has ever written a
    `django_admin_log` entry. The check exists because a migration that
    assumes it is safe without checking is a migration that eventually
    is not -- and because the failure mode if it IS wrong is silent
    referential nonsense in an audit-adjacent table.

    The two tables get two DIFFERENT, honest messages: a row in
    `auth_user` is a pre-swap account this migration's user-model swap
    abandons (not merely an empty legacy table), while a row in
    `django_admin_log` is an audit entry this migration cannot reassign
    to a new user id. Each message states the actual remedy, not just
    that the migration stopped.
    """
    auth_user_rows = _row_count(connection, _OLD_USER)
    if auth_user_rows:
        raise RuntimeError(
            f"Cannot move this box to the new user model: {_OLD_USER!r} holds "
            f"{auth_user_rows} row(s) -- a pre-swap account that this "
            f"migration's user-model swap abandons, not an empty legacy "
            f"table. {_HALF_APPLIED} Take a backup, then decide "
            f"deliberately: migrate that account by hand into "
            f"{_NEW_USER!r} before re-running `migrate`, or accept it is "
            f"gone -- delete the {_OLD_USER!r} row(s), re-run `migrate`, "
            f"and `manage.py createsuperuser` against the new user model."
        )
    admin_log_rows = _row_count(connection, _ADMIN_LOG)
    if admin_log_rows:
        raise RuntimeError(
            f"Cannot move this box to the new user model: {_ADMIN_LOG!r} "
            f"holds {admin_log_rows} row(s) still referencing "
            f"{_OLD_USER!r} -- this migration only knows how to repoint "
            f"an EMPTY admin log, not reassign existing entries to a new "
            f"user id. {_HALF_APPLIED} Take a backup, then decide "
            f"deliberately whether those entries (audit trail, not "
            f"application data) should be cleared from {_ADMIN_LOG!r} "
            f"before re-running `migrate`."
        )


def admin_log_user_fk_name(connection) -> str | None:
    """The name of `django_admin_log.user_id`'s foreign-key constraint,
    read from the catalogue, whatever table it currently references.

    NEVER A LITERAL. Django hash-suffixes constraint names, so the name
    differs between boxes and nobody may hard-code it.
    """
    if not _table_exists(connection, _ADMIN_LOG):
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT con.conname
              FROM pg_constraint con
              JOIN pg_class rel ON rel.oid = con.conrelid
              JOIN pg_attribute att
                ON att.attrelid = rel.oid AND att.attnum = ANY (con.conkey)
             WHERE rel.relname = %s
               AND rel.relnamespace = 'public'::regnamespace
               AND con.contype = 'f'
               AND att.attname = 'user_id'
             LIMIT 1
            """,
            [_ADMIN_LOG],
        )
        row = cursor.fetchone()
    return row[0] if row else None


def repoint_is_needed(connection) -> bool:
    """Whether `django_admin_log.user_id`'s FK actually still references
    `auth_user`, read from the catalogue rather than inferred from table
    existence alone.

    False on a fresh install, where `admin.0001_initial`'s own
    `swappable_dependency(settings.AUTH_USER_MODEL)` already built
    `django_admin_log.user_id` against `identity_user` -- `auth_user`
    never exists at all there.

    ALSO FALSE after this migration has already repointed the FK on an
    upgraded box: `auth_user` is deliberately never dropped (see the
    migration's own docstring), so table existence alone cannot tell a
    box that still needs repointing from one that already got it. Only
    asking the catalogue what the FK currently points at makes a replay
    -- another `manage.py migrate`, a `--fake` dance, anything that
    re-invokes this function -- an honest no-op instead of a refusal
    once real audit rows have since accumulated.
    """
    if not (_table_exists(connection, _OLD_USER) and _table_exists(connection, _ADMIN_LOG)):
        return False
    name = admin_log_user_fk_name(connection)
    if name is None:
        return False
    return _referenced_table(connection, name) == _OLD_USER


def repoint(apps, schema_editor):
    """Drop the old FK (found by catalogue, and only dropped once
    confirmed to reference `auth_user`) and add one at `identity_user`.
    A no-op on a fresh install, and a no-op on a replay after a previous
    run already repointed it.
    """
    connection = schema_editor.connection
    if not repoint_is_needed(connection):
        return
    refuse_if_rows_exist(connection)
    name = admin_log_user_fk_name(connection)
    with connection.cursor() as cursor:
        if name is not None and _referenced_table(connection, name) == _OLD_USER:
            cursor.execute(f'ALTER TABLE "{_ADMIN_LOG}" DROP CONSTRAINT "{name}"')
        cursor.execute(
            f'ALTER TABLE "{_ADMIN_LOG}" '
            f'ADD CONSTRAINT "django_admin_log_user_id_identity_user_fk" '
            f'FOREIGN KEY (user_id) REFERENCES "{_NEW_USER}" (id) '
            f'DEFERRABLE INITIALLY DEFERRED'
        )


def noop(apps, schema_editor):
    """The reverse.

    A DELIBERATE NO-OP, and the docstring says so plainly rather than
    leaving a reader to wonder: the reversal of a user-model swap is
    restoring the backup, the same recovery path every deploy already
    has for anything this platform cannot itself undo.
    """
