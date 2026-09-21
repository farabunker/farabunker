"""What `tools/rag` destroys when a deleted item's date arrives.

REGISTERED, NEVER CALLED DIRECTLY -- `tools/rag/apps.py::ready()`
registers the dotted paths and `identity/cascades.py` resolves them,
the same mechanism this column already uses for its entitlement cascade
and its six attachment seams.
"""
from __future__ import annotations

import uuid

from django.conf import settings

from tools.rag import services
from tools.rag.models import Document


def purge_conversation_notes(key: str) -> int:
    """Remove a deleted conversation's staging note and its note
    document. Returns how many things went.

    TWO THINGS, and they are not the same thing. The FILE is the
    deterministic `<NOTES_DIR>/<conversation-uuid>.md` original
    `tools/rag/jobs.py` writes when a stream is consolidated; the
    DOCUMENT is the ingested copy, found by `notes_conversation_id` -- a
    UUID BY VALUE, never a foreign key, because `tools/rag` may not
    import `agents.models`. The document goes through
    `services.delete_document`, which already tears down chunks, the
    managed store directory and the row together.

    A MISSING FILE IS NOT AN ERROR (`missing_ok=True`): the ONE
    best-effort case this function forgives is "somebody already cleaned
    this up" -- a purge that failed for that reason would be a purge
    nobody could ever finish.

    ANY OTHER FAILURE TO REMOVE THE FILE IS RAISED, NOT SWALLOWED. The
    `Document` row is the only remaining handle on the file; deleting
    that row while the file itself is still stuck on disk (a
    permissions problem, a full or read-only volume, any other genuine
    `OSError`) would report a purge that never actually happened --
    leaving a person's notes sitting on disk with nothing left pointing
    at them. Left to propagate, the caller that runs every registered
    handler (`identity/cascades.py::run_retention`, which never swallows
    either) fails the whole purge: the deletion ticket survives, and the
    next sweep retries this handler from the top rather than reporting a
    false success.

    Idempotent: once the file really is gone, a re-run finds no file and
    no document and returns zero.
    """
    try:
        conversation_id = uuid.UUID(str(key))
    except (ValueError, AttributeError, TypeError):
        return 0

    removed = 0
    path = settings.NOTES_DIR / f"{conversation_id}.md"
    # READ BEFORE THE UNLINK: `missing_ok=True` returns nothing whether
    # or not the file was there, and the count has to distinguish "one
    # note removed" from "nothing to remove" -- the test asserts 2 for a
    # note plus a document, and 0 for a re-run. A NON-MISSING failure
    # (any other `OSError`) is NOT caught here -- it propagates, and the
    # row below is never touched.
    existed = path.exists()
    path.unlink(missing_ok=True)
    if existed:
        removed += 1

    for document in Document.objects.filter(notes_conversation_id=conversation_id):
        services.delete_document(document)
        removed += 1
    return removed
