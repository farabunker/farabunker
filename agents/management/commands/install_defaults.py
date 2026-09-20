"""`python manage.py install_defaults [--reset <slug>]` -- the CLI
equivalent of the "Add the default X" button.

THE EXPLICIT PATH, not an automatic one (ruling 2). It replaces
`manage.py sync_agents`, which created three agents on every deploy
that ran it. Nothing about a deploy now writes the operator's rows:
this command exists so a person who prefers a shell to a button has
one, and it does exactly what the button does.

Idempotent by construction -- it calls `install_default`, which is
create-if-absent -- so running it twice is a no-op and running it after
an operator edited a row leaves the edit alone. `--reset <slug>` is the
one destructive option and it names its target.

Runs as `OPEN_PRINCIPAL`, which it IMPORTS, NEVER CONSTRUCTS
(`from identity.contracts.principals import OPEN_PRINCIPAL`), ON PURPOSE
AND UNCONDITIONALLY -- even once accounts are on and the shell operator
is a real, identifiable administrator. This is plan Decision 9: a
shipped default is the PLATFORM's offer, not the installing operator's
private row, and `agents.visibility`'s `resident=True` rows are visible
to every principal regardless of who owns them (`Q(resident=True)` in
`visible_agents`/`visible_flows`, unconditional in the OR) -- so which
principal owns a resident row never decides who can see it. What owning
it as `OPEN_PRINCIPAL` DOES decide is whether it stays ADOPTABLE:
`manage.py adopt_open_rows` only claims rows still owned by the open
box, and a shipped default that this command stamped with a specific
administrator's identity instead would be permanently exempt from that
claiming pass for no reason tied to visibility. (The page's own install
action, `agents/chat/views/defaults.py`, uses the real
`principal_for_request(request)` instead -- a person clicking "Add the
default X" from a signed-in session is a deliberate choice by that
person, not the platform's own offer, and is owned accordingly.) Task
4's AST guard is what keeps `OPEN_PRINCIPAL` here honest: this module is
not in `_PRINCIPAL_CONSTRUCTORS`
(`foundation/ops/tests/test_import_law.py`).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from agents.defaults import catalogue, default_for, install_default
from identity.contracts.principals import OPEN_PRINCIPAL

# Every kind the catalogue knows about, in report order.
_KINDS = ("agent", "flow")


class Command(BaseCommand):
    help = "Install the shipped default agents and flows that are not yet installed."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--reset", metavar="SLUG", default=None,
            help=(
                "Restore SLUG's shipped text, overwriting the existing row. "
                "The ONE destructive option this command has."
            ),
        )

    def handle(self, *args, **options) -> None:
        reset_slug = options.get("reset")
        if reset_slug:
            self._reset(reset_slug)
            return

        for kind in _KINDS:
            created: list[str] = []
            present: list[str] = []
            for spec in catalogue(kind):
                _, was_created = install_default(kind, spec.slug, OPEN_PRINCIPAL)
                (created if was_created else present).append(spec.slug)
            self.stdout.write(
                f"{kind}: created {', '.join(created) or 'none'}; "
                f"already present {', '.join(present) or 'none'}"
            )

    def _reset(self, slug: str) -> None:
        for kind in _KINDS:
            try:
                spec = default_for(kind, slug)
            except ValueError:
                continue
            install_default(kind, slug, OPEN_PRINCIPAL, reset=True)
            self.stdout.write(
                f"{kind} {slug!r}: reset to the shipped default {spec.name!r}"
            )
            return
        raise CommandError(
            f"No shipped default named {slug!r} in agent or flow catalogue "
            f"(known agents: {sorted(s.slug for s in catalogue('agent'))}; "
            f"known flows: {sorted(s.slug for s in catalogue('flow'))})"
        )
