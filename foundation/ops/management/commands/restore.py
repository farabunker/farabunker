"""`python manage.py restore <src> [--force]` -- see `foundation/ops/
restore.py` for the actual implementation; this command is a thin
argument-parsing wrapper, same division as `foundation/ops/management/
commands/backup.py`."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from foundation.ops import restore


class Command(BaseCommand):
    help = (
        "Restore files from <src>/files/ into DOCUMENTS_DIR/GENERATED_DIR, verify <src>/db.dump against "
        "the manifest, and print the database restore command. See docs/OPERATIONS.md."
    )

    def add_arguments(self, parser):
        parser.add_argument("src", type=str, help="Backup directory written by `manage.py backup`.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Restore into a non-empty target, and/or despite RUNNING jobs in the current database.",
        )

    def handle(self, *args, **options):
        restore.run_restore(
            options["src"],
            force=options["force"],
            stdout=self.stdout,
            style=self.style,
        )
