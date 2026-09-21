"""What `tools/rag` destroys when a deleted item's date arrives.

REGISTERED, NEVER CALLED DIRECTLY -- `tools/rag/apps.py::ready()`
registers the dotted paths and `identity/cascades.py` resolves them,
the same mechanism this column already uses for its entitlement cascade
and its six attachment seams.
"""
from __future__ import annotations

import logging
import uuid

from django.conf import settings

from tools.rag import services
from tools.rag.models import Document

logger = logging.getLogger(__name__)


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

    A MISSING FILE IS NOT AN ERROR (`missing_ok=True`): every file
    removal in this feature is best-effort and idempotent, because a
    purge that failed because somebody had already cleaned up would be a
    purge nobody could finish.

    Idempotent: a second run finds no file and no document and returns
    zero.
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
    # note plus a document, and 0 for a re-run.
    existed = path.exists()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Structural, never content -- the logging shape this column
        # uses throughout. A file this box cannot unlink is an operator
        # problem, not a reason to leave the rows standing.
        logger.exception(
            "tools.rag.retention: could not remove the staging note for %s",
            conversation_id)
    else:
        if existed:
            removed += 1

    for document in Document.objects.filter(notes_conversation_id=conversation_id):
        services.delete_document(document)
        removed += 1
    return removed
