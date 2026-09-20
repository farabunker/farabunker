"""Walking the owned-rows registry against a live Django.

`identity/contracts/ownership.py` stays PURE -- pinned by
`identity/tests/test_purity.py`, which imports the whole `contracts/`
package with no `DJANGO_SETTINGS_MODULE` set at all -- so it names
models as `app_label.ModelName` STRINGS and never touches
`django.apps`. Resolving those strings needs `apps.get_model`, which
needs a configured Django; this module is where that resolution lives,
so `identity.contracts.ownership` never has to import it.

FOUR CALL SITES used to open-code the same walk: `for spec in
all_owned_rows(): try: model = apps.get_model(spec.model) except
LookupError: continue`, once in `identity.services.owned_row_counts`
and twice more in each of `adopt_open_rows`/`reassign_owner`. A
registered model whose app is not installed on this box (`vision` with
its feature off) is SKIPPED rather than raising -- the registration is
flag-gated at `AppConfig.ready()`, so this is belt-and-braces for a
stale entry, not a path any of today's registrations actually take.
"""
from __future__ import annotations

from collections.abc import Iterator

from django.apps import apps
from django.db.models import Model

from identity.contracts.ownership import OwnedRows, all_owned_rows


def owned_models() -> Iterator[tuple[OwnedRows, type[Model]]]:
    """Every registered `OwnedRows` spec paired with its resolved model,
    skipping a spec whose app is not installed on this box."""
    for spec in all_owned_rows():
        try:
            model = apps.get_model(spec.model)
        except LookupError:
            continue
        yield spec, model
