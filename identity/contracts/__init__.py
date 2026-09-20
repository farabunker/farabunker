"""The IDENTITY CONTRACT -- pure, Django-free, universally importable
(rule 1).

Four modules, no more:

- `principals.py` -- `Principal`, `PRINCIPAL_KINDS`, `OPEN_PRINCIPAL`,
                      `SERVICE_PRINCIPAL`, the `ANONYMOUS` sentinel, and
                      the job-payload round trip.
- `postures.py`    -- the posture and library-posture names, and the
                      one pure predicate (`accounts_required`).
- `actions.py`     -- the closed audit-action catalogue.
- `ownership.py`   -- the owned-rows registry.

NOTHING HERE IMPORTS DJANGO -- not transitively either. `identity/tests/
test_purity.py` pins this in a subprocess with no DJANGO_SETTINGS_MODULE
set at all, because "pure" that is only asserted in a docstring is a
hope, not a property.
"""
