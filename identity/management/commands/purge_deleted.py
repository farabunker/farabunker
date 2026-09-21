"""Purge every deleted item whose promised date has arrived.

FOR AN OPERATOR WHO WANTS A CRON RATHER THAN RELYING ON
PRUNE-ON-WRITE. It is not required: `identity.retention.delete_content`
runs a bounded sweep at the end of every delete and the Deleted page
runs one on GET, so a box that is used at all keeps itself clean. This
exists for a box that is not -- one that was deleted from and then left
alone -- and for an operator who would rather the cliff be a scheduled
thing than an incidental one.

ACTS AS THE SERVICE PRINCIPAL, like the sweep itself, and records
`source="cli"`: the cliff is the box's own act, not any person's.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from identity.contracts.actions import SOURCE_CLI
from identity.retention import SWEEP_LIMIT, sweep


class Command(BaseCommand):
    help = "Purge deleted items whose retention period has ended."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit", type=int, default=SWEEP_LIMIT,
            help=f"How many items to purge in this run (default {SWEEP_LIMIT}).")

    def handle(self, *args, **options) -> None:
        purged = sweep(limit=options["limit"], source=SOURCE_CLI)
        noun = "item" if purged == 1 else "items"
        self.stdout.write(f"Purged {purged} {noun}.")
