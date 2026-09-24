"""`manage.py identity_repair_migration_history` -- the one-time
`django_migrations` bookkeeping fix for a database that had `django.
contrib.admin`'s migrations applied before IA-1 existed.

MANUFACTURING THE PRE-SWAP SHAPE, on THIS test database, means more than
deleting `identity.*` rows from `django_migrations`: this repository's
test database, like the live preview box the command was written
against, already has `identity.0001_initial` genuinely applied -- its
three tables physically exist. `identity_repair_migration_history`'s own
repair calls `call_command("migrate", "identity", "0001_initial", ...)`
with no `--fake-initial`, exactly as the command's spec requires, and
Django's `CreateModel` issues a plain `CREATE TABLE` with no `IF NOT
EXISTS` -- reapplying it against tables that still physically exist
raises `django.db.utils.ProgrammingError: relation ... already exists`
rather than quietly doing nothing (confirmed against this test database
while writing this module: bookkeeping-only manufacturing made the
repair itself blow up on the `CREATE TABLE`, for both `identity_
identitysettings` and, once that was fixed, the `User` model's own `auth.
Group`/`auth.Permission` M2M through tables). So `_drop_identity_tables`
below drops the five real tables `identity.0001_initial` creates,
`CASCADE`, which is what a box that never ran that migration actually
looks like -- not a lighter stand-in for it. `TestHalfRun` below is the
one place that table stays deliberately in place: it manufactures the
OTHER shape the command must tell apart from the repairable one.

RESTORATION IS THE DATABASE'S OWN JOB, not this module's. Every test
here runs under the plain, non-transactional `pytest.mark.django_db`
(no `transaction=True`): pytest-django already wraps each test body in
one real transaction and rolls it back at teardown, and Postgres's
transactional DDL means a `DROP TABLE ... CASCADE` inside that
transaction is exactly as reversible as an `UPDATE` -- rolled back
whole, tables, constraints and all, whether the test passes or raises.
`TestRepair` proves this two ways: directly
(`test_the_database_was_left_exactly_as_the_repair_left_it`, which
depends on running after `test_it_repairs_the_pre_swap_shape` -- this
module defines no test-ordering plugin, and pytest collects a class's
tests in definition order, so that dependency is safe), and by every
OTHER test in this module still passing once it has run -- if the
rollback had missed anything, the shared test database every other
`identity` test also uses would stay broken. This is more robust than a
manual capture-then-`finally`-restore of the deleted rows would be
(nothing to remember to undo, and the exact `identity.
0002_repoint_admin_log_fk` row this test file finds already applied vs.
not applied is restored either way), which is why no test below tracks
`identity.0002`'s row by hand.
"""
from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

pytestmark = pytest.mark.django_db

# Every table `identity.0001_initial` creates: the three model tables
# plus the two auth.Group/auth.Permission M2M through-tables `AbstractUser`
# carries onto `User`. `CASCADE` also drops `identity.
# 0002_repoint_admin_log_fk`'s `django_admin_log` -> `identity_user`
# foreign key on a database where `0002` has already run -- nothing else
# in the schema references any of these five tables.
_IDENTITY_TABLES = (
    "identity_user_user_permissions",
    "identity_user_groups",
    "identity_auditevent",
    "identity_identitysettings",
    "identity_user",
)


def _drop_identity_tables():
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE {', '.join(_IDENTITY_TABLES)} CASCADE")


def _forget(recorder, app):
    recorder.migration_qs.filter(app=app).delete()


def _forget_identity_dependents(recorder):
    """Forget every OTHER app's migration that depends on `identity`,
    alongside `identity` itself.

    IA-2 Task 4 (`agents/migrations/0003_toolentitlement_and_share.py`)
    made `agents.0003` the repository's first migration OUTSIDE
    `identity` to depend on it (`ToolEntitlement.entitlement` is a
    string FK to `"identity.Entitlement"`). IA-2 Task 9
    (`tools/rag/migrations/0015_documententitlement.py`) added a second:
    `DocumentEntitlement.entitlement` is the same shape of string FK.
    IA-2 Task 14 (`models/registry/migrations/0008_modelset.py`) added a
    third: `ModelSetEntitlement.entitlement` is the same shape again.
    IA-2 Task 15 (`agents/migrations/0004_agent_flow_entitlements.py`)
    added a fourth: `AgentEntitlement`/`FlowEntitlement.entitlement` are
    the same shape once more, and that migration ALSO depends directly
    on `identity.0003_entitlement_and_grant` (`makemigrations`'s own
    dependency, not a hand-added one). The manufactured pre-IA-1 shape
    below forgets `identity`'s own rows but never touched any
    dependent's tables or rows -- fine while nothing outside `identity`
    named it as a dependency, but on a database where every migration up
    to HEAD has already run, leaving `agents.0003`, `agents.0004`,
    `rag.0015` or `inference.0008` recorded as applied while every
    `identity.*` row is forgotten is an `InconsistentMigrationHistory`
    Django's own loader raises the moment the nested `migrate` call in
    `identity_repair_migration_history` starts -- a shape a REAL
    pre-IA-1 box never reaches, because none of the four dependents
    existed in that box's migration history at all until the code that
    ships it (and `identity` alongside it) is deployed. Forgetting them
    here is what makes the manufactured shape match that real one; a
    subsequent `manage.py migrate` (the repair command's own last line
    of advice) re-applies all four for real, in dependency order,
    exactly as it does for `identity.0002` and `identity.0003`.

    THE `agents` CLAUSE IS A RANGE, NOT TWO NAMES, and UI-3b is why.
    `agents.0005_conversation_archived_at` names `identity` nowhere --
    it adds one nullable column to `Conversation` -- but it depends on
    `agents.0004`, which this function forgets, so leaving it recorded
    is the SAME `InconsistentMigrationHistory` by one more hop. Every
    `agents` migration from `0003` onward is transitively downstream of
    `identity` and has to go with it; naming them one at a time made the
    next unrelated migration in this app break a test about the identity
    swap, which is a trap rather than a check. `name__gte="0003"` reads
    the zero-padded prefix Django's own numbering guarantees, so
    `"0010_..."` sorts after `"0003_..."` correctly rather than the way
    a naive numeric-string comparison would.

    IT WOULD ALSO SWEEP IN A HAND-NAMED MIGRATION (a merge migration,
    `"merge_2026..."`, sorts after `"0003"` on any string comparison),
    and that is the SAFE direction here rather than an accident this
    note is glossing: forgetting one migration too many costs nothing,
    because the repair command's own last line of advice is a plain
    `manage.py migrate` that re-applies everything in dependency order.
    Forgetting one too FEW is the failure this range exists to prevent.

    THE `rag` CLAUSE IS A RANGE TOO, FOR THE SAME REASON, since
    Workstreams v1 Task 5 (`tools/rag/migrations/
    0016_document_workstream_and_pins.py`) added a second `rag`
    migration and made it depend on `0015` in the ordinary Django
    migration-dependency sense (`dependencies = [..., ("rag",
    "0015_documententitlement")]`) -- so forgetting `0015` by single
    name while `0016` stayed recorded left `0016` applied before a
    dependency Django's own loader now considers forgotten, the exact
    `InconsistentMigrationHistory` the `agents` range above already
    exists to prevent, just tripped from the `rag` side instead.
    `name__gte="0015"` sweeps `0016` (and any later `rag` migration a
    future task adds) the same way `name__gte="0003"` sweeps
    `agents.0004` and onward.

    THE `inference` CLAUSE IS A RANGE TOO, FOR THE SAME REASON, since
    queue memory governance (2026-09-21) added
    `models/registry/migrations/
    0009_modelconnection_engine_reported_footprint.py` and made it
    depend on `0008_modelset` in the ordinary Django
    migration-dependency sense (`dependencies = [("inference",
    "0008_modelset")]`) -- so forgetting `0008` by single name while
    `0009` stayed recorded left `0009` applied before a dependency
    Django's own loader now considers forgotten, the exact
    `InconsistentMigrationHistory` the `agents` range above already
    exists to prevent, just tripped from the `inference` side instead.
    `name__gte="0008"` sweeps `0009` (and any later `inference`
    migration a future task adds) the same way `name__gte="0003"`
    sweeps `agents.0004` and onward. Forgetting one migration too many
    costs nothing here either, for the reason the `agents` note gives:
    the repair command's own last line of advice is a plain
    `manage.py migrate` that re-applies everything in dependency order.
    """
    recorder.migration_qs.filter(app="agents", name__gte="0003").delete()
    recorder.migration_qs.filter(app="rag", name__gte="0015").delete()
    recorder.migration_qs.filter(app="inference", name__gte="0008").delete()


def _admin_log_row_count() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM django_admin_log")
        return cursor.fetchone()[0]


def _auth_user_snapshot() -> int | None:
    """`None` when `auth_user` doesn't exist at all -- true on THIS test
    database, which was never an upgraded box (see `docs/OPERATIONS.md`'s
    "Expected orphan tables" section) -- or its row count when it does,
    so this assertion is meaningful on both kinds of database."""
    with connection.cursor() as cursor:
        if "auth_user" not in connection.introspection.table_names(cursor):
            return None
        cursor.execute("SELECT count(*) FROM auth_user")
        return cursor.fetchone()[0]


class _ExplodingQuerySet:
    """Proxies every attribute to a real queryset except `bulk_create`,
    which raises -- used to prove step 4 (re-recording `admin.*`) is
    inside the SAME `transaction.atomic()` as steps 1-3, not a separate
    one bolted on afterward."""

    def __init__(self, real_qs):
        self._real = real_qs

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __iter__(self):
        return iter(self._real)

    def bulk_create(self, *args, **kwargs):
        raise RuntimeError("simulated failure while re-recording admin.*")


class TestRefusals:
    def test_it_refuses_on_an_already_consistent_database(self):
        """The fixture database is already fully migrated -- `identity.
        0001_initial` is applied -- so this is the plain "nothing to
        repair" case, with no manufacturing at all."""
        with pytest.raises(CommandError) as exc:
            call_command("identity_repair_migration_history")
        message = str(exc.value)
        assert "nothing to repair" in message
        assert "identity.0001_initial" in message

    def test_it_refuses_when_neither_admin_nor_identity_has_applied(self):
        """The other honest-refusal shape named in the spec: no applied
        `admin.*` row and no applied `identity.*` row either, AND no
        `identity_user` table -- what a fresh install's `django_
        migrations` and schema both look like before the first
        `migrate`. Dropping the tables here (not just forgetting the
        rows) is what tells this apart from `TestHalfRun` below, which
        manufactures the same forgotten rows but leaves the table in
        place on purpose."""
        recorder = MigrationRecorder(connection)
        _drop_identity_tables()
        _forget(recorder, "admin")
        _forget(recorder, "identity")
        with pytest.raises(CommandError) as exc:
            call_command("identity_repair_migration_history")
        message = str(exc.value)
        assert "nothing to repair" in message
        assert "fresh install" in message


class TestInconsistentIdentityRows:
    def test_it_refuses_when_0002_is_applied_without_0001(self):
        """A shape this command was never asked to reason about: some
        LATER `identity.*` migration recorded as applied with `identity.
        0001_initial` itself missing. Bookkeeping-only -- the table
        check never even runs, because the `identity.*`-row check fires
        first regardless of what the schema looks like."""
        recorder = MigrationRecorder(connection)
        recorder.migration_qs.filter(
            app="identity", name="0001_initial"
        ).delete()
        with pytest.raises(CommandError) as exc:
            call_command("identity_repair_migration_history")
        message = str(exc.value)
        assert "identity.0002_repoint_admin_log_fk" in message
        assert "identity.0001_initial" in message


class TestHalfRun:
    """`identity_user` physically exists but no `identity.*` row is
    applied -- deliberately NOT `_drop_identity_tables()`'d. Before this
    check existed, this exact shape was mistaken for the repairable one
    and the repair blew up mid-transaction on `CREATE TABLE ...  already
    exists` instead of refusing up front."""

    def test_it_refuses_naming_the_table(self):
        recorder = MigrationRecorder(connection)
        _forget(recorder, "identity")
        with pytest.raises(CommandError) as exc:
            call_command("identity_repair_migration_history")
        message = str(exc.value)
        assert "identity_user" in message
        assert "half-run" in message

    def test_dry_run_refuses_the_same_way(self):
        """The refusal fires before the `--dry-run` branch is even
        reached, so both modes give the operator the same honest
        answer."""
        recorder = MigrationRecorder(connection)
        _forget(recorder, "identity")
        with pytest.raises(CommandError) as exc:
            call_command("identity_repair_migration_history", "--dry-run")
        assert "half-run" in str(exc.value)


class TestDryRun:
    def test_it_reports_the_plan_and_writes_nothing(self, capsys):
        """The tables must actually be gone here too: with `identity_
        user` still present, this would now hit the half-run refusal
        (`TestHalfRun`) instead of ever reaching the dry-run report."""
        recorder = MigrationRecorder(connection)
        admin_before = set(
            recorder.migration_qs.filter(app="admin").values_list("app", "name")
        )
        _drop_identity_tables()
        _forget(recorder, "identity")

        call_command("identity_repair_migration_history", "--dry-run")

        out = capsys.readouterr().out
        assert "identity.0001_initial" in out
        assert "Dry run" in out
        # Nothing written: identity is still forgotten, admin is
        # untouched, byte for byte.
        assert not recorder.migration_qs.filter(app="identity").exists()
        after = set(
            recorder.migration_qs.filter(app="admin").values_list("app", "name")
        )
        assert after == admin_before


class TestRepair:
    def test_it_repairs_the_pre_swap_shape(self, capsys):
        recorder = MigrationRecorder(connection)
        admin_before = sorted(
            recorder.migration_qs.filter(app="admin")
            .values_list("app", "name", "applied")
        )
        assert admin_before, "fixture database must have applied admin.* rows"
        admin_log_before = _admin_log_row_count()
        auth_user_before = _auth_user_snapshot()

        _drop_identity_tables()
        _forget(recorder, "identity")
        _forget_identity_dependents(recorder)

        call_command("identity_repair_migration_history")

        assert recorder.migration_qs.filter(
            app="identity", name="0001_initial"
        ).exists()

        # Re-recorded with their ORIGINAL `applied` timestamps, not
        # `now()` -- `record_applied` would have rewritten history here.
        admin_after = sorted(
            recorder.migration_qs.filter(app="admin")
            .values_list("app", "name", "applied")
        )
        assert admin_after == admin_before

        # This command writes no row to auth_user or django_admin_log --
        # only the nested `migrate`'s own content types and permissions.
        assert _admin_log_row_count() == admin_log_before
        assert _auth_user_snapshot() == auth_user_before

        # The whole point: the loader Django's own `migrate` consults no
        # longer sees an applied migration ahead of an unapplied
        # dependency.
        MigrationLoader(connection).check_consistent_history(connection)

        out = capsys.readouterr().out
        assert "identity.0001_initial" in out
        assert "manage.py migrate" in out

    def test_the_database_was_left_exactly_as_the_repair_left_it(self):
        """Runs after `test_it_repairs_the_pre_swap_shape` (see the
        module docstring on why that ordering is safe to rely on): the
        rollback-based restoration claim proved directly, not just
        inferred from the rest of the suite still passing."""
        recorder = MigrationRecorder(connection)
        assert recorder.migration_qs.filter(
            app="identity", name="0001_initial"
        ).exists()
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('identity_user')")
            assert cursor.fetchone()[0] is not None

    def test_a_failure_while_re_recording_admin_rows_rolls_back_everything(
        self, monkeypatch
    ):
        """Step 4 raising must undo steps 1-3 too -- the whole point of
        ONE `transaction.atomic()` rather than two. `_ExplodingQuerySet`
        only touches `bulk_create`, so detection, the delete, and the
        nested `migrate` all run for real; only the final re-record
        step fails."""
        recorder = MigrationRecorder(connection)
        admin_before = sorted(
            recorder.migration_qs.filter(app="admin")
            .values_list("app", "name", "applied")
        )
        assert admin_before

        _drop_identity_tables()
        _forget(recorder, "identity")
        _forget_identity_dependents(recorder)

        real_migration_qs = type(recorder).migration_qs

        def exploding_migration_qs(self):
            return _ExplodingQuerySet(real_migration_qs.fget(self))

        monkeypatch.setattr(
            MigrationRecorder, "migration_qs", property(exploding_migration_qs)
        )

        with pytest.raises(RuntimeError):
            call_command("identity_repair_migration_history")

        # Everything the atomic block touched is back exactly as it was
        # BEFORE the command ran -- including the nested `migrate`'s own
        # effects, not merely the delete this command issued itself.
        admin_after = sorted(
            recorder.migration_qs.filter(app="admin")
            .values_list("app", "name", "applied")
        )
        assert admin_after == admin_before
        assert not recorder.migration_qs.filter(app="identity").exists()
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('identity_user')")
            assert cursor.fetchone()[0] is None
