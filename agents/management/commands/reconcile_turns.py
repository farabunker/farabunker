"""`python manage.py reconcile_turns [--dry-run]` -- the operator-facing
half of owner decision 7 (a turn whose job row vanished recovers).

The poll path (`agents.chat.views.turns._queued_body`/`_running_body`)
already reconciles the one row a live operator is actually watching, on
every GET. This command exists for the row nobody is polling -- a
conversation nobody has reopened since its job row vanished -- and it is
run by hand, not on a schedule: no periodic background sweeper exists,
because the condition is rare and the read surface already visits
exactly the row that matters. Thin on purpose, the same division
`models/queue/management/commands/run_jobs.py` draws between itself and
`models.queue.worker.Worker`: this command owns none of the actual
reconciliation logic, it only calls `agents.reconcile.
reconcile_stranded_turns` (or, for `--dry-run`, its read-only public
counterpart `count_stranded_turns`) and reports what happened.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from agents.reconcile import count_stranded_turns, reconcile_stranded_turns


class Command(BaseCommand):
    help = "Close every assistant turn whose job row is gone (owner decision 7)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count the stranded turns and write nothing.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            count = count_stranded_turns()
            self.stdout.write(f"{count} turn(s) would be closed.")
            self.stdout.write(self.style.WARNING("Dry run — nothing was written."))
            return

        count = reconcile_stranded_turns()
        self.stdout.write(f"{count} turn(s) closed.")
