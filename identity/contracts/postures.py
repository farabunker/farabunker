"""The posture names, as pure data.

Three postures, and they are a DATABASE ROW's value, never an
environment variable (spec section 3.2): `compose.yaml` starts three
processes from one image with independently supplied environments, so a
security posture carried in the environment could be set on the web
process and unset on the worker -- and the worker is where turns
actually run tools. The database is the one thing all three processes
provably share.

This module holds only the NAMES and one pure predicate, so a form, a
template tag or a management command can name a posture without
importing a Django model.
"""
from __future__ import annotations

POSTURE_OPEN = "open"
POSTURE_PERSONAL = "personal"
POSTURE_ENTERPRISE = "enterprise"

# In order of strictness, which is also the order the posture page
# renders them in.
POSTURES = (POSTURE_OPEN, POSTURE_PERSONAL, POSTURE_ENTERPRISE)

POSTURE_CHOICES = (
    (POSTURE_OPEN, "Open — no accounts, no login"),
    (POSTURE_PERSONAL, "Personal — a few local accounts, every one an administrator"),
    (POSTURE_ENTERPRISE, "Organisation — accounts, groups and entitlements"),
)

# Whether documents carrying NO label are readable by everyone signed in
# (`open`, the default) or by administrators only (`locked`). Stored and
# rendered in IA-1; consulted by nothing until IA-2 gives labels a body.
LIBRARY_OPEN = "open"
LIBRARY_LOCKED = "locked"
LIBRARY_CHOICES = (
    (LIBRARY_OPEN, "Open — an unlabelled document is readable by everyone signed in"),
    (LIBRARY_LOCKED, "Locked — an unlabelled document is readable by administrators only"),
)

# Twelve hours, as a ROLLING idle window (spec section 14). Zero means
# "expire when the browser closes", which is why one column covers both
# behaviours and no `SESSION_EXPIRE_AT_BROWSER_CLOSE` setting is added.
SESSION_IDLE_MINUTES_DEFAULT = 720


def accounts_required(posture: str) -> bool:
    """Whether `posture` is one in which a request must be authenticated.

    Pure, and separate from `identity.access.accounts_on()`, which reads
    the row. This one lets a caller that ALREADY has the posture string
    -- a form validating a submitted value, a management command
    printing what a switch would do -- answer the question without a
    second database read.
    """
    return posture != POSTURE_OPEN
