"""An open box's first administrator claims the rows nobody owns.

A box that ran open for a year has conversations, agents, flows, ask
records and generated images owned by `("open", "box")` or by nothing at
all. Switching the posture does not move them -- that is `set_posture`'s
whole "a switch, not a migration" property -- so this command exists to
move them, once, deliberately, and with a record.

REVERSIBILITY IS THE POSTURE SWITCH. There is deliberately no flag to
undo this: switching back to `open` makes every visibility function
return everything, so the reassigned owner stops mattering, and
rewriting owners back to the open principal would be a second, lossier
operation that also erased any ownership recorded after adoption.
"""
from __future__ import annotations

import contextlib

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from identity import audit, services
from identity.contracts import actions
from identity.contracts.actions import SOURCE_CLI
from identity.contracts.principals import SERVICE_PRINCIPAL
from identity.ownership import owned_models

# The two states a row can be in that mean "nobody in particular owns
# this": what `agents/` stamped on an open box, and what `vision`/`rag`
# rows written before IA-1 carry, because those columns had no owner
# columns at all.
#
# `("service", "local")` IS DELIBERATELY NOT HERE (spec section 10.4).
# A row a shell path made is not a row the open box made: sweeping it
# into a person's name would attribute an automated action to somebody
# who did not perform it, and the audit trail would then be wrong in a
# way nothing could detect. Service-owned rows stay service-owned and
# remain visible to `is_admin`, which is where an operator looks for
# them anyway.
_UNOWNED = Q(owner_kind="open", owner_key="box") | Q(owner_kind="", owner_key="")


class Command(BaseCommand):
    help = "Claim every unowned row for one administrator."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True,
                            help="The superuser who will own the claimed rows.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print the per-table counts and write nothing.")

    def handle(self, *args, **options):
        try:
            user = services.resolve_owner_target(options["user"], must_be_admin=True)
        except services.ServiceRefused as exc:
            raise CommandError(str(exc)) from exc
        key = str(user.pk)
        dry_run = options["dry_run"]

        # ONE LOOP for both branches -- they differed in exactly one
        # expression (`.count()` vs `.update(...)`). A dry run enters no
        # transaction at all (`nullcontext()`): it is reads only, so
        # there is nothing to roll back and nothing to hold a lock over.
        counts: dict[str, int] = {}
        context = contextlib.nullcontext() if dry_run else transaction.atomic()
        with context:
            for spec, model in owned_models():
                qs = model.objects.filter(_UNOWNED)
                counts[spec.label] = (
                    qs.count() if dry_run
                    else qs.update(owner_kind="user", owner_key=key)
                )
            if not dry_run:
                # SAME transaction as the row updates: a failing audit
                # write must roll the ownership change back too, or the
                # database and the trail would disagree about who owns
                # what. ONE row, not one per claimed row -- the
                # interesting fact is the event and the counts, not five
                # thousand line items.
                audit.record(SERVICE_PRINCIPAL, actions.ADOPTED, target_type="user",
                             target_key=user.pk, target_label=user.username,
                             source=SOURCE_CLI, counts=counts)

        for label, count in counts.items():
            self.stdout.write(f"{label}: {count}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing was written."))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Claimed {sum(counts.values())} row(s) for {user.username!r}."
        ))
        # The ordering the operator wants, printed where they will read
        # it. Switching the posture FIRST leaves the new admin looking
        # at their own empty box until they adopt -- confusing, not
        # dangerous, and worth one line.
        self.stdout.write(
            "Next: switch the posture with `manage.py identity_posture personal` "
            "(or `enterprise`). Doing that BEFORE adopting is what leaves a new "
            "administrator looking at an empty box."
        )
