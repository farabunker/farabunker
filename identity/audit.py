"""The one writer, and the one reader, of the audit trail.

A NAMED SEAM: every column may import this module. It is also the ONLY
module in the codebase that may perform ANY `AuditEvent.objects`
attribute access -- not `.create`, not `.filter`, not `.update`, not
`.delete` -- pinned by an AST guard in
`foundation/ops/tests/test_column_boundaries.py`.

THE GUARD FORBIDS THE WHOLE MANAGER, NOT JUST `.create`, because
append-only is a code-level property here: `.update()` and `.delete()`
never call `save()`, so a guard that only looked for `create` would let
through the two operations that actually destroy an audit trail. The
readers below exist for the same reason -- an audit page that had to
name `AuditEvent.objects` would need an exception, and a guard with an
exception is a guard somebody widens.
"""
from __future__ import annotations

from identity.contracts.actions import LOGIN_FAILED, SOURCE_WEB
from identity.models import AuditEvent, User

_OPEN_LABEL = "this box (no accounts)"
_SERVICE_LABEL = "a local command"
_ANONYMOUS_LABEL = "not signed in"


def _label_for(actor) -> str:
    """The actor's display name AT THE TIME OF THE EVENT.

    Resolved here, written once, and never resolved again. A user's
    label is their username; the three non-user kinds get a readable
    constant, because "open/box" on an audit page is a shape, not a
    sentence.
    """
    kind = getattr(actor, "kind", "")
    if kind == "user":
        key = actor.key
        if isinstance(key, str) and key.isdecimal():
            row = User.objects.filter(pk=int(key)).values_list("username", flat=True).first()
            if row:
                return row
        return f"user {actor.key}"
    if kind == "open":
        return _OPEN_LABEL
    if kind == "service":
        return _SERVICE_LABEL
    if kind == "anonymous":
        return _ANONYMOUS_LABEL
    return kind


def record(actor, action: str, *, target_type: str = "", target_key: str = "",
           target_label: str = "", source: str = SOURCE_WEB, **detail) -> AuditEvent:
    """Write one audit row for `actor` doing `action`.

    `**detail` becomes the row's JSON `detail`, so a caller writes
    `record(actor, POSTURE_CHANGED, to="personal")` rather than
    assembling a dict. `action` is validated against the closed
    catalogue by `AuditEvent.save()` -- a typo raises here, at the call
    site, rather than silently splitting a report in two.

    Never swallows. An audit write that failed quietly would leave the
    operator believing the trail is complete, which is worse than the
    failure it hid. Callers that genuinely must not fail on it (there
    are none in IA-1) would wrap this themselves.
    """
    return AuditEvent.objects.create(
        actor_kind=getattr(actor, "kind", ""),
        actor_key=getattr(actor, "key", ""),
        actor_label=_label_for(actor)[:255],
        action=action,
        target_type=target_type[:64],
        target_key=str(target_key)[:255],
        target_label=str(target_label)[:255],
        source=source,
        detail=detail,
    )


def recent(limit: int = 100) -> list[AuditEvent]:
    """The newest `limit` rows, newest first (`Meta.ordering`)."""
    return list(AuditEvent.objects.all()[:limit])


def for_target(target_type: str, target_key: str, limit: int = 100) -> list[AuditEvent]:
    """Everything recorded about one target, newest first."""
    return list(
        AuditEvent.objects.filter(
            target_type=target_type, target_key=str(target_key))[:limit]
    )


def failed_logins_since(username: str, since) -> int:
    """How many `login_failed` rows this username has since `since`.

    HERE, not in `identity/throttle.py`, because this module is the ONLY
    one permitted to touch `AuditEvent.objects` -- `identity/models.py`'s
    own docstring and the AST sweep in
    `foundation/ops/tests/test_column_boundaries.py` are the rule, and
    the append-only guarantee depends on it staying that way. The POLICY
    (how many is too many, for how long) lives in `throttle.py`; this is
    the read.

    CASE-INSENSITIVE on the username, because the login form is: Django
    authenticates case-sensitively by default but a person who types
    `Alice` five times and `alice` once has made six attempts, and a
    counter that disagreed would be trivially defeated by alternating
    case.
    """
    return AuditEvent.objects.filter(
        action=LOGIN_FAILED,
        target_label__iexact=username,
        at__gte=since,
    ).count()
