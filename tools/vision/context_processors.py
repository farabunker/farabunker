"""Template context processors for the vision feature.

Registered in `TEMPLATES[0]["OPTIONS"]["context_processors"]` (config/settings.py)
so the SHARED shell (foundation/templates/_shell.html) can decide whether to render its
nav link to `/vision/`: with the feature off there is no route at all, and a
nav entry pointing at a URL that does not exist would 404. A context
processor is the one mechanism that reaches every rendered page -- including
/rag/ and /inference/, which must not import this module for anything else.

No DB, no HTTP: it hands back the already-parsed setting, nothing more.
"""
from __future__ import annotations

from django.conf import settings


def features(request) -> dict:
    """`{"farabunker_features": settings.FARABUNKER_FEATURES}` -- the
    frozenset of enabled feature names, under the one key every template
    reads it by."""
    return {"farabunker_features": settings.FARABUNKER_FEATURES}
