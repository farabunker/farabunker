"""`python manage.py backup <dest> [--dump PATH] [--force]` -- see
`foundation/ops/backup.py` for the actual implementation; this command is a
thin argument-parsing wrapper, same division as `models/registry/
management/commands/reencode.py`."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from foundation.ops import backup


class Command(BaseCommand):
    help = (
        "Copy DOCUMENTS_DIR + GENERATED_DIR into <dest>/files/, write a manifest, and (with --dump) fold "
        "in a pg_dump. See docs/OPERATIONS.md."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "dest",
            type=str,
            nargs="?",
            default=None,
            help=(
                "Destination directory (must not be inside DOCUMENTS_DIR/GENERATED_DIR). "
                "Omit it for a fresh, timestamped directory under settings.BACKUP_DIR (S14: "
                "FARABUNKER_BACKUP_DIR, default <FARABUNKER_DATA_DIR>/backups) -- so running "
                "this again produces a second backup, not a --force collision with the first."
            ),
        )
        parser.add_argument(
            "--dump",
            type=str,
            default=None,
            help="Path to a `pg_dump --format=custom` file to fold in as <dest>/db.dump.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Back up into a non-empty <dest>, and/or despite RUNNING jobs or PROCESSING documents. "
                "Only ever adds/overwrites at <dest>/files/ -- never prunes; reusing a <dest> that held a "
                "different, earlier backup can leave it holding files this run refuses to leave "
                "unaccounted for."
            ),
        )

    def handle(self, *args, **options):
        backup.run_backup(
            options["dest"],
            dump_arg=options["dump"],
            force=options["force"],
            stdout=self.stdout,
            style=self.style,
        )
