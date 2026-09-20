"""One-time `django_migrations` bookkeeping repair for a database that
ran `django.contrib.admin`'s migrations before IA-1 existed.

THE SHAPE THIS FIXES. Any box that ever ran `manage.py migrate` before
this column landed already has `admin.0001_initial` recorded as
applied. That migration carries
`migrations.swappable_dependency(settings.AUTH_USER_MODEL)`, which at
IA-1's HEAD resolves to `identity.0001_initial` -- a migration such a
box has never run. `manage.py migrate` calls
`MigrationLoader.check_consistent_history` on every invocation, before
any migration is applied, and that check raises the moment it finds an
applied migration whose dependency is unapplied. So the refusal fires
before `identity.0001_initial` -- and therefore
`identity.0002_repoint_admin_log_fk`, which depends on it -- ever gets a
chance to run. A fresh install never hits this: nothing has applied
`admin.0001_initial` yet, so the loader's plan applies
`identity.0001_initial` FIRST, exactly as the swappable dependency
demands, and this command has nothing to do (say so, `nothing to
repair`).

THE ONLY SHAPE THIS COMMAND REPAIRS: an applied `admin.*` row exists and
NO `identity.*` row of any kind is applied. Everything else refuses by
name rather than guessing, including three shapes that are easy to
mistake for the repairable one:

- `identity.0001_initial` itself already applied -- consistent already,
  `nothing to repair`.
- some OTHER `identity.*` migration applied (e.g. `identity.
  0002_repoint_admin_log_fk`) with `identity.0001_initial` itself
  missing -- inconsistent in a way this command was not written to
  reason about, since it does not know what state `0002` and anything
  after it left the schema in.
- `identity_user` physically exists with NO `identity.*` row applied at
  all -- a half-run migration, not the pre-IA-1 shape: re-running
  `identity.0001_initial`'s `CreateModel` operations against tables that
  already exist raises `ProgrammingError: relation ... already exists`
  rather than doing anything useful, so this is refused up front instead
  of discovered mid-repair. Checked against the live catalogue via
  `connection.introspection.table_names()`, never inferred from
  `django_migrations` alone.

DATA WRITTEN. This command writes no row to `auth_user` or `django_
admin_log` -- repointing `django_admin_log`'s foreign key stays
`identity.0002_repoint_admin_log_fk`'s job, run by a plain `manage.py
migrate` afterwards (this command's last line says so). It is NOT,
however, free of data writes: step 3 below is a real `migrate`, and
Django's own `post_migrate` signal fires from inside it exactly as it
would from any other `migrate` run, via `django.contrib.contenttypes`'s
`create_contenttypes` and `django.contrib.auth`'s `create_permissions`
-- so this command creates `ContentType` and `Permission` rows for
`identity`'s three new models, the same rows a fresh install's first
`migrate` creates for every app. Confirmed empirically while writing
this module: on a database with no `identity.*` content types or
permissions yet, the repair adds exactly 3 `ContentType` rows and 12
`Permission` rows (`add`/`change`/`delete`/`view`, times three models)
and zero rows to `django_admin_log`.

Detection reads `django_migrations` exclusively through
`MigrationRecorder` -- never a raw query against a guessed table shape
-- and the repair itself:

1. records the currently-applied `admin.*` rows, `app`/`name`/`applied`
   all three, in a local list (the ledger, not the schema, is what's
   wrong: the `admin` app's tables genuinely exist);
2. deletes those rows via the recorder;
3. applies `identity.0001_initial` for real, now that nothing in
   `django_migrations` blocks it;
4. re-creates every `admin.*` row step 2 forgot, with its ORIGINAL
   `applied` timestamp preserved -- not `record_applied`, which stamps
   `now()` and would silently rewrite history -- the `--fake`
   equivalent of `migrate`, since steps 1-2 never touched the real
   `admin` tables and there is nothing left to apply.

ONE `transaction.atomic()`, deliberately, not two. Nesting
`call_command("migrate", ...)` inside an outer `atomic()` is only safe
if two things hold, and both do here: Postgres's `can_rollback_ddl` is
`True` (its DDL is fully transactional, unlike MySQL's), and
`identity.0001_initial` sets no operation to `atomic = False`. Given
both, Django's own executor wraps the migration in
`connection.schema_editor(atomic=migration.atomic)`
(`django/db/migrations/executor.py`), which opens a plain
`transaction.atomic()` around the DDL
(`django/db/backends/base/schema.py`) -- and `atomic()` entered while a
transaction is already open becomes a SAVEPOINT, not a second top-level
transaction, and Postgres can roll a savepoint's DDL back losslessly. A
failure anywhere in the four steps above -- the delete, the migrate
call, or a re-record -- unwinds the whole repair, leaving
`django_migrations` exactly as this command found it. `skip_checks=True`
is passed to the nested `migrate` deliberately: `manage.py check`
includes DB-touching system checks (`identity.E001`/`E002` read
`IdentitySettings`, which may not even exist yet at this point), and
none of them belong inside a transaction this command is still holding
open.

NO `AuditEvent` ROW. This command may run before `identity.0001_
initial` has ever applied -- i.e. before the `identity_auditevent`
table exists to hold one -- so it writes none, on purpose, in every
mode including a successful repair.
"""
from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder

_APP_ADMIN = "admin"
_APP_IDENTITY = "identity"
_MIGRATION_FIRST = "0001_initial"
_TABLE_USER = "identity_user"


class Command(BaseCommand):
    help = ("Repair django_migrations on a database that applied "
            "admin's migrations before identity.0001_initial existed, "
            "so `manage.py migrate` can run at all.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report the detected shape and the repair plan; write nothing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        recorder = MigrationRecorder(connection)
        recorder.ensure_schema()

        admin_rows = list(
            recorder.migration_qs.filter(app=_APP_ADMIN).order_by("id")
        )
        identity_rows = list(
            recorder.migration_qs.filter(app=_APP_IDENTITY).order_by("id")
        )
        identity_names = {m.name for m in identity_rows}

        if _MIGRATION_FIRST in identity_names:
            raise CommandError(
                f"{_APP_IDENTITY}.{_MIGRATION_FIRST} is already applied on this "
                "database -- nothing to repair. Run `manage.py migrate` directly."
            )

        if identity_names:
            other = ", ".join(
                f"{_APP_IDENTITY}.{name}" for name in sorted(identity_names)
            )
            raise CommandError(
                f"{other} recorded as applied without {_APP_IDENTITY}."
                f"{_MIGRATION_FIRST} itself -- this is not a shape this command "
                "can safely repair. A human needs to look at django_migrations "
                "by hand before anything runs `migrate` again."
            )

        with connection.cursor() as cursor:
            existing_tables = set(connection.introspection.table_names(cursor))
        if _TABLE_USER in existing_tables:
            raise CommandError(
                f"{_TABLE_USER} exists but no {_APP_IDENTITY}.* migration is "
                "recorded as applied -- this looks like a half-run migration, "
                "not the pre-IA-1 shape this command repairs. A human needs to "
                "look at django_migrations and the schema by hand before "
                "anything runs `migrate` again."
            )

        if not admin_rows:
            raise CommandError(
                f"This database has no applied {_APP_ADMIN}.* migrations, so it "
                "is not the pre-IA-1 shape this command repairs -- nothing to "
                "repair (a fresh install applies "
                f"{_APP_IDENTITY}.{_MIGRATION_FIRST} before {_APP_ADMIN}'s own "
                "migrations, with no help needed). Run `manage.py migrate` "
                "directly."
            )

        applied_display = ", ".join(f"{m.app}.{m.name}" for m in admin_rows)

        if dry_run:
            self.stdout.write(
                f"Detected the pre-IA-1 shape: {len(admin_rows)} applied "
                f"{_APP_ADMIN}.* migration(s) ({applied_display}), no applied "
                f"{_APP_IDENTITY}.* migration at all."
            )
            self.stdout.write(
                f"Would forget those {len(admin_rows)} {_APP_ADMIN}.* row(s), "
                f"apply {_APP_IDENTITY}.{_MIGRATION_FIRST} (creating its tables, "
                "content types and permissions), then re-create the "
                f"{_APP_ADMIN}.* row(s) with their original `applied` timestamps."
            )
            self.stdout.write(self.style.WARNING("Dry run -- nothing was written."))
            return

        with transaction.atomic():
            recorder.migration_qs.filter(app=_APP_ADMIN).delete()
            # `skip_checks=True`: `manage.py check` includes DB-touching
            # system checks (identity.E001/E002 read IdentitySettings),
            # and none of those belong inside a transaction this command
            # is still holding open.
            call_command(
                "migrate", _APP_IDENTITY, _MIGRATION_FIRST,
                verbosity=options["verbosity"], skip_checks=True,
            )
            # `record_applied` stamps `now()` -- recreating the rows
            # directly is what preserves each migration's ORIGINAL
            # `applied` timestamp, which is what "re-record ... exactly
            # as it found it" has to mean.
            recorder.migration_qs.bulk_create(
                recorder.Migration(app=m.app, name=m.name, applied=m.applied)
                for m in admin_rows
            )

        self.stdout.write(self.style.SUCCESS(
            f"Forgot and re-recorded {len(admin_rows)} {_APP_ADMIN}.* "
            f"migration(s) ({applied_display}) and applied "
            f"{_APP_IDENTITY}.{_MIGRATION_FIRST}."
        ))
        self.stdout.write(
            f"Next: run `manage.py migrate` to apply "
            f"{_APP_IDENTITY}.0002_repoint_admin_log_fk and everything after it."
        )
