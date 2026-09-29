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
# The page's second section: the content-free log of items deleted,
# restored and permanently deleted -- the same "deletion log"
# `LABEL_AUDIT_DETAIL` names, so the tab heading and the setting that
# controls what it shows use one word for one thing.
TAB_LOG = "Deletion log"
ACTION_RESTORE = "Restore"
ACTION_PURGE = "Delete permanently"
# WHAT THE PAGE SAYS INSTEAD OF THE RESTORE BUTTON, for a ticket
# `identity.retention.may_restore` refuses: a purge already destroyed
# some of this item's content before it failed, so the words say what
# happened and the one thing left to do about it, in the same plain
# register as `purge_refused_line` below -- no "ticket", no "purge",
# no mechanism, just what a person can act on. NO APOSTROPHE, ON
# PURPOSE: Django's autoescape turns one into `&#x27;` in rendered
# HTML, which breaks a plain substring check against the raw response
# body -- the same reason every other sentence in this module already
# avoids contractions.
RESTORE_REFUSED_LINE = (
    "The content of this item could not be fully removed, so it cannot "
    "be restored. Delete permanently to finish removing what is left."
)
# WHAT A CONVERSATION SHOWS IN PLACE OF A PICTURE ITS OWN BYTES ARE
# GONE FOR (placeholder wave, 2026-09-29) -- `agents/chat/templates/
# chat/_artifact_images.html`'s one reader, in place of the browser's
# own bare broken-image icon. TWO SENTENCES, keyed off the SAME
# `content_unrecoverable` column `RESTORE_REFUSED_LINE` and `may_
# restore` already read, because the two cases are not the same fact:
# an item this box finished deleting is gone on purpose, and an item
# `identity.retention.record_failed_purge` marked is one whose removal
# stalled halfway -- content gone, ticket still standing, restorable to
# nobody. Saying the second one "was deleted" would be the SAME
# mistake `RESTORE_REFUSED_LINE`'s own docstring already refuses to
# make about the Deleted page's Restore button, applied to a picture
# instead of a control. NO APOSTROPHE, same reason as above.
CONTENT_DELETED_LINE = "This item was deleted."
CONTENT_UNRECOVERABLE_LINE = (
    "The deletion of this item could not be completed, so it cannot "
    "be recovered."
)
# WHAT `identity.views.deleted_restore` ADDS TO "Restored." when the
# ticket it just deleted left a MARKED CHILD detached behind it
# (`identity.retention.restore_content`'s own docstring: a child
# `record_failed_purge` marked is skipped, never put back), AND ONLY
# WHEN THE VIEWER CAN SEE THAT CHILD (`identity.retention.
# visible_tickets`, the same predicate the Deleted page itself is
# filtered by -- restore-notice-scope, 2026-09-29). One sentence, the
# same "deletion could not be completed" words as
# `CONTENT_UNRECOVERABLE_LINE` above, so a person who restores a
# conversation and then opens it and finds this sentence's picture are
# the same fact stated twice, not two different-sounding claims about
# one thing.
RESTORE_PARTIAL_LINE = (
    "Part of this could not be restored because its deletion could "
    "not be completed."
)
LABEL_RETENTION_DAYS = "Keep deleted items for"
LABEL_QUEUE_RETENTION_DAYS = "Keep finished queue jobs for"
LABEL_AUDIT_DETAIL = "Show item names in the deletion log"

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _date_words(day: datetime.date) -> str:
    """"21 October 2026" -- an un-padded day and a month NAME, because
    the un-padded directive (`%-d`) is platform-specific and these
    sentences must read identically wherever the box runs."""
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def purge_on_line(day: datetime.date) -> str:
    """"Purge on 21 October 2026" -- the promise the page prints."""
    return f"Purge on {_date_words(day)}"


def purge_refused_line(day: datetime.date, *, marked: bool = False) -> str:
    """"This item is kept until 21 October 2026. It can be restored,
    not destroyed early." -- or, for a MARKED ticket, `RESTORE_REFUSED_
    LINE` verbatim.

    WHAT THE ORGANISATION POSTURE SAYS INSTEAD OF DESTROYING SOMETHING.
    It names the date the page already printed and the door that is
    still open, and it claims nothing about records obligations, legal
    holds or who could override it -- none of which this box builds.

    `marked`, KEYWORD-ONLY: whether the ticket this sentence is about is
    one `identity.retention.may_restore` already refuses -- a purge
    destroyed some of its content and then failed. THE UNMARKED SENTENCE
    IS BACKWARDS FOR THAT TICKET, in both of its own claims at once: the
    item cannot in fact be restored, and (an owner ruling, 2026-09-28) it
    CAN now be destroyed early on this posture too, because the enforced
    period exists to protect content this item no longer fully has. So a
    marked ticket gets the SAME words `RESTORE_REFUSED_LINE` already
    says -- content partly gone, restoring no longer possible, finishing
    the removal is -- rather than a second sentence claiming the same
    thing in different words.
    """
    if marked:
        return RESTORE_REFUSED_LINE
    return (f"This item is kept until {_date_words(day)}. "
            f"It can be restored, not destroyed early.")


# -- the one refusal ----------------------------------------------------
class RetentionRefused(Exception):
    """A purge this box will not perform right now, with an
    operator-readable reason.

    RAISED BY A RETENTION HANDLER, CAUGHT BY THE DELETED PAGE'S POST
    VIEW, which renders `str(exc)` as a flashed sentence and leaves the
    ticket in place -- the never-500 shape every mutation on this box
    has. Once the queue half lands, `models.queue.retention.
    forget_conversation` will raise it when a worker still holds one of
    the conversation's jobs -- that handler is not yet in the tree, one
    of the residues ADR 0020 (decision 7) names.

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
