"""Who is acting on this request.

ONE function, and it is the only place in the codebase that turns an
HTTP request into a principal. Every view reaches it; none of them
builds a principal itself. MOVED here from `agents/chat/principal.py`,
which is deleted -- the question "who is in front of the browser" is not
an agents-column question, and it stopped being one the moment there
were accounts.

This module and `identity/contracts/principals.py` are the only two
files that may CONSTRUCT a `Principal`, pinned by the AST guard in
`foundation/ops/tests/test_import_law.py`.
"""
from __future__ import annotations

from identity.access import accounts_on
from identity.contracts.principals import ANONYMOUS, OPEN_PRINCIPAL, Principal
from identity.models import IdentitySettings


def settings_row_for(request):
    """The `IdentitySettings` row for `request` -- `IdentityGateMiddleware`'s
    own stashed read if it already ran, or a fresh fetch otherwise.

    THE THIRD SPELLING OF "read the singleton once per request", named
    once here instead of at each of its callers
    (`identity.context_processors.identity`, `foundation.setup.views.
    SetupView`): both used to write `getattr(request,
    "identity_settings_row", None)` and fall back to `get_solo()`
    themselves, which is the same fallback this function makes, just not
    duplicated.

    The no-row branch only fires for a render path the gate never
    touched -- a test rendering a view or a template directly, with no
    middleware chain in front of it.
    """
    row = getattr(request, "identity_settings_row", None)
    return row if row is not None else IdentitySettings.get_solo()


def user_for_request(request):
    """The authenticated `identity.User` instance behind `request`, or
    `None` -- for a caller that needs the real row (a foreign key like
    `ToolEntitlement.labelled_by`), not the `Principal` value object
    `principal_for_request` answers with.

    THIS MODULE IS NOT SCANNED BY `foundation/ops/tests/test_column_
    boundaries.py::test_no_view_outside_identity_reads_request_user` --
    that sweep polices `tools/`, `models/` and `agents/` reading
    `request.user` directly, so those columns go through
    `identity.request`/`identity.access` instead of reading it
    themselves. Reading it HERE, once, is that seam; a second direct
    read anywhere under the three scanned columns is exactly what the
    sweep exists to catch.

    `None`, not `AnonymousUser`, for an unauthenticated request -- an
    anonymous caller never reaches a view that would call this (the
    gate refuses it first), but a direct test or a render with no
    middleware chain in front of it should get a value a `ForeignKey`
    can actually take, not Django's sentinel object.
    """
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


def principal_for_request(request, *, settings_row=None):
    """The principal acting on `request`.

    OPEN posture: one principal for the whole machine, and no query
    against any identity table -- `accounts_on()` reads the settings
    singleton and returns before anything else is touched.

    PERSONAL/ENTERPRISE: the signed-in user, or `ANONYMOUS` for a
    request with no session -- NEVER the open principal, which would be
    a posture leak wearing an unauthenticated request's clothes.

    `ANONYMOUS` is not a `Principal` and "anonymous" is not a principal
    kind (see `identity/contracts/principals.py`). Handling it is the
    GATE's job, not the view's: `IdentityGateMiddleware` refuses an
    anonymous request before a view ever sees it, so this value reaches
    an access function only through a direct call in a test.

    `settings_row`: an already-fetched `IdentitySettings` row, for a
    caller that reads the singleton once and needs more than one answer
    off it -- `IdentityGateMiddleware`, which also needs `is_admin` and
    `session_idle_minutes` from the same row. Optional and keyword-only:
    every other caller in the codebase leaves it out, and this function
    reads the singleton itself through `accounts_on()`, exactly as
    before.
    """
    if not accounts_on(settings_row=settings_row):
        return OPEN_PRINCIPAL
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return ANONYMOUS
    return Principal("user", str(user.pk))
