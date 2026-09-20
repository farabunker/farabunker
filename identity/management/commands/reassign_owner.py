"""One owner's rows move to another, by explicit operator instruction.

The documented follow-up to deactivating somebody who owned rows
(`identity.services.deactivate_user` leaves an inactive account's rows
in place, on purpose -- see that function's docstring): the account
survives, and this command is how an operator hands its rows to
somebody else, on purpose, one instruction at a time.

`--from service` is DELIBERATELY reachable only here, never from
`adopt_open_rows` (spec section 10.4), which skips `("service",
"local")` rows on purpose. An operator who genuinely wants a
shell-made row to belong to a person says so explicitly, by naming
`service`, rather than having it swept up by a bulk command that was
asked to do something else (spec section 10.5).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from identity import audit, services
from identity.contracts import actions
from identity.contracts.actions import SOURCE_CLI
from identity.contracts.ownership import all_owned_rows
from identity.contracts.principals import SERVICE_PRINCIPAL
from identity.models import User
from identity.ownership import owned_models

# The two `--from` values that are not usernames (spec section 10.5).
# `("service", "local")` is NOT reachable from `adopt_open_rows` --
# only from an operator naming it here, deliberately, once.
_FROM_SENTINELS = {
    "open": ("open", "box"),
    "service": ("service", "local"),
}


class Command(BaseCommand):
    help = "Move one owner's rows to another account."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="from_", required=True,
                            help="A username, or the literal 'open' or 'service'.")
        parser.add_argument("--to", required=True,
                            help="The username that will own the moved rows.")
        parser.add_argument("--kind", default=None,
                            help="One registered owned-rows key. Omit to move every table.")

    def handle(self, *args, **options):
        from_kind, from_key = self._resolve_from(options["from_"])
        try:
            to_user = services.resolve_owner_target(options["to"])
        except services.ServiceRefused as exc:
            raise CommandError(str(exc)) from exc

        if options["from_"] == options["to"]:
            # A username can name both --from and --to (nobody can spell
            # 'open' or 'service' as their own username). There is
            # nothing to move and nothing worth an audit row about.
            self.stdout.write(
                f"{options['to']!r} already owns those rows; nothing to move."
            )
            return

        wanted = {spec.key for spec in self._resolve_specs(options["kind"])}

        counts: dict[str, int] = {}
        with transaction.atomic():
            for spec, model in owned_models():
                if spec.key not in wanted:
                    continue
                qs = model.objects.filter(owner_kind=from_kind, owner_key=from_key)
                counts[spec.label] = qs.update(owner_kind="user", owner_key=str(to_user.pk))

            # SAME transaction as the row updates: a failing audit write
            # must roll the ownership change back too, or the database
            # and the trail would disagree about who owns what. ONE row,
            # not one per moved row -- the same shape `adopt_open_rows`
            # writes, and for the same reason.
            audit.record(SERVICE_PRINCIPAL, actions.OWNER_REASSIGNED, target_type="user",
                         target_key=to_user.pk, target_label=to_user.username,
                         source=SOURCE_CLI,
                         **{"from": options["from_"], "to": to_user.username,
                            "kind": options["kind"], "counts": counts})
        self.stdout.write(self.style.SUCCESS(
            f"Moved {sum(counts.values())} row(s) from {options['from_']!r} "
            f"to {to_user.username!r}."
        ))

    def _resolve_from(self, value: str) -> tuple[str, str]:
        if value in _FROM_SENTINELS:
            return _FROM_SENTINELS[value]
        user = User.objects.filter(username=value).first()
        if user is None:
            raise CommandError(
                f"{value!r} is not a known username, and not 'open' or 'service' either."
            )
        return "user", str(user.pk)

    def _resolve_specs(self, kind: str | None):
        specs = all_owned_rows()
        if kind is None:
            return specs
        narrowed = [spec for spec in specs if spec.key == kind]
        if not narrowed:
            valid = ", ".join(sorted(spec.key for spec in specs))
            raise CommandError(
                f"{kind!r} is not a registered owned-rows key. Valid keys: {valid}"
            )
        return narrowed
