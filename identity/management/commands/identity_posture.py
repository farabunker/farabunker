"""Read or change this box's posture from a shell.

THE BREAK-GLASS PATH for the case where the posture page is unreachable
-- and it reaches `identity.services.set_posture`, exactly as the page
does, so it is NOT a way around any of that function's three refusals.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from identity import services
from identity.contracts.actions import SOURCE_CLI
from identity.contracts.postures import POSTURES
from identity.contracts.principals import SERVICE_PRINCIPAL
from identity.models import IdentitySettings


class Command(BaseCommand):
    help = "Show this box's posture, or switch it."

    def add_arguments(self, parser):
        parser.add_argument("posture", nargs="?", choices=list(POSTURES),
                            help="The posture to switch to. Omit to print the current one.")
        parser.add_argument("--admin-sees-content", choices=["on", "off"],
                            help="Whether administrators may read other people's content.")

    def handle(self, *args, **options):
        row = IdentitySettings.get_solo()
        target = options["posture"]
        content = options["admin_sees_content"]
        if target is None and content is None:
            self.stdout.write(f"posture: {row.posture}")
            self.stdout.write(f"library posture: {row.library_posture}")
            self.stdout.write(f"administrators read content: "
                              f"{'yes' if row.admin_sees_content else 'no'}")
            return
        try:
            row = services.set_posture(
                SERVICE_PRINCIPAL,
                posture=target,
                admin_sees_content=None if content is None else content == "on",
                source=SOURCE_CLI,
            )
        except services.ServiceRefused as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"posture: {row.posture}"))
        if target is not None and target != "open":
            # The ordering guidance the adoption command also prints.
            self.stdout.write(
                "If this box has rows created before accounts existed, run "
                "`manage.py adopt_open_rows --user <username>` so they belong to "
                "somebody."
            )
