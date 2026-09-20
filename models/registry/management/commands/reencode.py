"""`python manage.py reencode [--role ROLE]` — re-encode a role's materialized
data (run the role's rematerialize callback and update the fingerprint)."""
from django.core.management.base import BaseCommand, CommandError

from models.registry import drift
from models.contracts.roles import RAG_EMBED_ROLE


class Command(BaseCommand):
    help = "Re-encode a role's materialized data (update embeddings, etc)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--role",
            type=str,
            default=RAG_EMBED_ROLE,
            help=f"Role to re-encode (default: {RAG_EMBED_ROLE}).",
        )

    def handle(self, *args, **options):
        role = options["role"]
        try:
            result = drift.run_rematerialize(role)
        except (ValueError, RuntimeError) as exc:
            # ValueError: role misconfigured (no rematerialize callback).
            # RuntimeError: the rematerialize ran but failed to re-encode
            # everything (e.g. reencode_all's raise-on-partial) -- either
            # way the run failed and must exit non-zero, not print SUCCESS.
            raise CommandError(str(exc)) from exc

        # Print a per-run tally using self.style
        tally = drift.format_tally(result)
        self.stdout.write(self.style.SUCCESS(f"Rematerialized {role}: {tally}"))
