"""Running the entitlement cascades against a live Django.

`identity/contracts/cascades.py` stays PURE -- pinned by
`identity/tests/test_purity.py`, which imports the whole `contracts/`
package with no `DJANGO_SETTINGS_MODULE` set at all -- so it names
handlers as dotted-path STRINGS. Resolving one needs
`django.utils.module_loading.import_string`, which is Django, which
rule 4 allows; this module is where that resolution lives, exactly as
`identity/ownership.py` is where `apps.get_model` lives.

NEVER SWALLOWS. A cascade whose handler cannot be imported, or which
raises, takes the whole delete down with it -- inside
`identity.services.delete_entitlement`'s `transaction.atomic()`, so
nothing is half-deleted. A delete that reported success while leaving
orphan labels and a stale chunk cache behind is the one failure mode
this registry exists to prevent.
"""
from __future__ import annotations

from django.utils.module_loading import import_string

from identity.contracts.cascades import all_entitlement_cascades


def _run(entitlement_id: int, *, commit: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in all_entitlement_cascades():
        handler = import_string(spec.handler)
        counts[spec.label] = handler(entitlement_id, commit=commit)
    return counts


def cascade_counts(entitlement_id: int) -> dict[str, int]:
    """`{label: count}` -- what each column WOULD remove. Writes nothing.
    This is what the delete confirmation names."""
    return _run(entitlement_id, commit=False)


def run_cascades(entitlement_id: int) -> dict[str, int]:
    """`{label: count}` -- what each column DID remove, having also
    repaired its own caches. Call inside the delete's transaction."""
    return _run(entitlement_id, commit=True)
