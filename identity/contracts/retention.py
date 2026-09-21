"""The deletion vocabulary: what kinds exist, what the shipped policy
is, and every word a person reads.

PURE -- no Django, no database, no I/O -- pinned by
`identity/tests/test_purity.py`, a subprocess with no settings module
configured at all. That is what lets `models/queue` name
`QUEUE_RETENTION_DAYS_DEFAULT` across the `identity.contracts` seam
without importing anything that touches a connection.

THE COPY LIVES HERE, not in a template and not in a form, because
user-facing sentences are declared once in Python (`AGENTS.md`, house
style) and these particular sentences have four readers apiece: the
form label, the page, the help card and the test that pins them. The
word "purge" appears in this module, in the design spec and in the date
line's verb -- and nowhere else a person reads: there is no "purge
queue", no "retention cliff" and no "ticket" in anything rendered.
"""
from __future__ import annotations

import datetime

# -- the kinds ----------------------------------------------------------
# A CLOSED TUPLE, validated in `DeletionTicket.save()` exactly as
# `AuditEvent.save()` validates its action against `AUDIT_ACTIONS`, and
# for the identical reason: a typo'd kind is a construction error at the
# call site, not a category that silently splits a page in two.
#
# `workstream` is deliberately absent (spec section 10.2): a stream is a
# container whose contents each have their own cliff, and
# `agents.visibility.delete_workstream` is unchanged.
KIND_CONVERSATION = "conversation"
KIND_DOCUMENT = "document"
KIND_ASK = "ask"
KIND_VISION_JOB = "vision_job"
RETENTION_KINDS = (KIND_CONVERSATION, KIND_DOCUMENT, KIND_ASK, KIND_VISION_JOB)

# What each kind is CALLED on the Deleted page. Plain nouns: a person is
# never asked to know that a generated image is a "vision job".
KIND_LABELS = {
    KIND_CONVERSATION: "Conversation",
    KIND_DOCUMENT: "Document",
    KIND_ASK: "Ask record",
    KIND_VISION_JOB: "Generated image",
}

# -- the shipped policy -------------------------------------------------
# THE WHOLE POINT OF THESE TWO CONSTANTS: a box that is installed and
# never configured deletes content correctly, purges it thirty days
# later, and keeps its finished queue rows for a day. No field filled
# in, no command scheduled, no posture decided (spec section 3.0).
RETENTION_DAYS_DEFAULT = 30
QUEUE_RETENTION_DAYS_DEFAULT = 1

# RANGE LIMITS LIVE IN THE WRITER, never as a database constraint
# (`foundation/settings_bounds.py`) -- named here so the writer, the
# form and the help card all spell the same numbers.
#
# `retention_days` may be 0: "no grace period" must stay expressible,
# and it is the posture-independent way to say it. `queue_retention_days`
# may NOT be 0 -- blank (null) is how "no age cliff" is said there, the
# same "honestly unknown, never silently assumed" convention
# `JobSettings.memory_budget_bytes` already documents.
RETENTION_DAYS_MIN = 0
RETENTION_DAYS_MAX = 3650
QUEUE_RETENTION_DAYS_MIN = 1
QUEUE_RETENTION_DAYS_MAX = 3650

# -- the copy -----------------------------------------------------------
# ONE constant for the page's name AND its first section's heading: they
# are the same word because they name the same thing, and two constants
# holding "Deleted" would be two places for it to stop being the same.
PAGE_TITLE = "Deleted"
TAB_PURGED = "Purged"
ACTION_RESTORE = "Restore"
ACTION_PURGE = "Delete permanently"
LABEL_RETENTION_DAYS = "Keep deleted items for"
LABEL_QUEUE_RETENTION_DAYS = "Keep finished queue jobs for"
LABEL_AUDIT_DETAIL = "Show item names in the deletion log"

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def purge_on_line(day: datetime.date) -> str:
    """"Purge on 21 October 2026" -- the promise the page prints.

    Built from `day.day` and a month NAME rather than a `strftime`
    format, because the un-padded day directive (`%-d`) is
    platform-specific and this sentence must read identically wherever
    the box runs.
    """
    return f"Purge on {day.day} {_MONTHS[day.month - 1]} {day.year}"


# -- the one refusal ----------------------------------------------------
class RetentionRefused(Exception):
    """A purge this box will not perform right now, with an
    operator-readable reason.

    RAISED BY A RETENTION HANDLER, CAUGHT BY THE DELETED PAGE'S POST
    VIEW, which renders `str(exc)` as a flashed sentence and leaves the
    ticket in place -- the never-500 shape every mutation on this box
    has. Today exactly one handler raises it:
    `models.queue.retention.forget_conversation`, when a worker still
    holds one of the conversation's jobs.

    IT LIVES IN THIS PURE MODULE RATHER THAN BESIDE
    `identity.services.ServiceRefused`, and that is not a stylistic
    call. `models/queue` may import `identity.contracts` and may NOT
    import `identity.services`:
    `foundation/ops/tests/test_import_law.py`'s `IDENTITY_PERMITTED`
    allowlist names the first and not the second, and
    `test_no_column_imports_identitys_private_modules` fails the build
    on it. A distinct type, not a bare `RuntimeError`, so the view
    catches a refusal deliberately rather than catching every
    programming error a handler might contain.
    """
