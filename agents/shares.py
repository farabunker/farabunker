"""Reading `Share` rows -- the two small queries every visibility body
needs.

IT IS A SEPARATE MODULE FROM `agents/visibility.py` because
`foundation/ops/tests/test_column_boundaries.py` forbids any module under
`agents/chat` from touching `Conversation`/`Agent`/`Flow` `.objects`
directly, and IA-2 adds `Share` to that same set. One module owns the
manager; everything else asks it a question. A guard with an exception is
a guard somebody widens.
"""
from __future__ import annotations

import logging

from django.db.models import Q

from agents.models import TARGET_KEY_PARSERS, Share

logger = logging.getLogger(__name__)


def _subject_q(principal) -> Q | None:
    """`Q` matching shares that reach `principal`, or None for a
    principal a share can never name."""
    if getattr(principal, "kind", None) != "user":
        return None
    key = principal.key
    if not isinstance(key, str) or not key.isdecimal():
        return None
    return Q(user_id=int(key)) | Q(group__user__id=int(key))


def shared_keys(target_type: str, principal) -> list[str]:
    """The `target_key` strings this principal reaches through a `Share`,
    directly or through one of their groups.

    MATERIALISED into a list rather than left as a `Subquery`,
    deliberately: `Share.target_key` is text and the three target tables
    have three different primary-key types, so a subquery would need a
    per-type cast and would be a silent type mismatch waiting to happen.
    Two small queries on a single-box install beat one clever one.

    FILTERED THROUGH THE TARGET'S OWN KEY PARSER on the way out -- an
    unparseable row is dropped and logged rather than reaching
    `Q(pk__in=[...])`, where it would raise inside the queryset and turn
    a listing page into a 500.
    """
    subject = _subject_q(principal)
    if subject is None:
        return []
    parse = TARGET_KEY_PARSERS[target_type]
    out = []
    for key in Share.objects.filter(target_type=target_type).filter(subject) \
                            .values_list("target_key", flat=True):
        if parse(key) is None:
            logger.warning("agents: share row for %s carries an unusable key %r; ignored.",
                           target_type, key)
            continue
        out.append(key)
    return out


def share_level(principal, target_type: str, target_key: str) -> str | None:
    """The widest level `principal` reaches this one row at, or None.

    THE WIDEST WINS when a direct share and a group share both reach:
    the answer has to be ONE level, and anything but the widest would
    mean adding somebody to a group silently REMOVED an ability they
    already had.
    """
    subject = _subject_q(principal)
    if subject is None:
        return None
    levels = set(Share.objects.filter(target_type=target_type, target_key=str(target_key))
                              .filter(subject).values_list("level", flat=True))
    if Share.Level.USE in levels:
        return Share.Level.USE
    if Share.Level.VIEW in levels:
        return Share.Level.VIEW
    return None


def shares_for(target_type: str, target_key: str):
    """Every share on one row, for the owner's own share list."""
    return Share.objects.filter(target_type=target_type, target_key=str(target_key)) \
                        .select_related("user", "group").order_by("id")
