"""
Shared upper-bound checks for settings integer fields (S1, Coherence Wave
B backend audit).

The only ceiling checks on any settings field used to be
`tools.rag.views._BIGINT_FIELD_MAX`/`_POSITIVE_INT_FIELD_MAX` (T10 review
MINOR 5, added after an operator-typed value large enough to overflow a
column -- e.g. `1e308` GB -- reached `.save()` uncaught, a `django.db.
utils.DataError` on a surface this codebase's convention is never-500).
That fix could not propagate: it lived in a `tools/rag`-private module,
and a private helper cannot legally cross a column boundary (import
law) -- so `tools/rag`'s OWN `history_limit` field, in the very same
module, and every `models.queue` `JobSettings` field next door, all kept
the exact same unguarded shape. `foundation/` is the sanctioned
cross-column home for something every column needs (columns -> foundation
is a lawful import in every direction; see `foundation/format.py`'s own
docstring for the identical reasoning applied to GB<->bytes conversion).

NO Django import, NO project import -- pure, dependency-free, like
`foundation/format.py`.
"""
from __future__ import annotations

# A Django `BigIntegerField` is Postgres `bigint`: signed 8-byte.
BIGINT_FIELD_MAX = 2**63 - 1

# A Django `PositiveIntegerField` is Postgres `integer` (signed 4-byte)
# plus a `>= 0` CHECK constraint -- NOT unsigned, so the true ceiling is
# still 2**31-1, not 2**32-1.
POSITIVE_INT_FIELD_MAX = 2**31 - 1


def exceeds_field_ceiling(stored: int | float, *, max_stored: int) -> bool:
    """True if `stored` -- the value a settings write is about to save
    into an integer column -- exceeds that column's real ceiling.

    Callers pass `BIGINT_FIELD_MAX`/`POSITIVE_INT_FIELD_MAX` above for
    `max_stored` (or a narrower, field-specific ceiling such as
    `RagSettings.RETRIEVAL_TOP_K_MAX`, which is a real range limit, not a
    column-overflow guard) and reject with their own copy/redirect before
    ever calling `.save()` -- every settings write on this platform owns
    its own error message and its own never-500 shape; this function only
    answers the yes/no question every one of them needs answered the same
    way.
    """
    return stored > max_stored
