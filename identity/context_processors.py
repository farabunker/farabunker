"""Three context keys the shared shell reads.

Registered in `TEMPLATES[0]["OPTIONS"]["context_processors"]` beside
`tools.vision.context_processors.features`, for exactly the same reason
that one is: the shell (`foundation/templates/_shell.html`) has to
decide whether to render a nav link on EVERY page, including pages whose
own view knows nothing about identity, and a context processor is the
one mechanism that reaches all of them.

`identity_accounts_on` was added beside the two the design names
(`identity_posture`, `identity_is_admin`) so the shell's sign-out form
(RULING R-T8) can decide whether to render without running a second
query of its own -- a template that queried the posture directly would
be exactly the "an open box never runs a permission query" rule broken
in the one place nothing else checks.

ZERO EXTRA `IdentitySettings.get_solo()` READS on a real request:
`IdentityGateMiddleware.process_view` already fetches the row and
stashes it as `request.identity_settings_row` before any view -- and
therefore any template -- runs, and this reads THAT row rather than
calling `posture()`/`is_admin()`/`accounts_on()` and re-fetching the
singleton off their own public, no-argument forms.
`test_middleware.py::TestTheSingleRowRead` pins the gate at exactly one
`identity_identitysettings` read per request; a context processor that
read its own would double it on every rendered page. The fallback read
below only fires for a render path the gate never touched (a test
rendering a template directly), so it stays correct there too.

In the open posture this still answers ("open", True, False) off that
one read: `accounts_on` returns False before anything else runs, and
`is_admin` returns True from its own open branch.
"""
from __future__ import annotations

from django.db import DatabaseError

from identity.access import accounts_on, is_admin
from identity.request import principal_for_request, settings_row_for


def identity(request) -> dict:
    """`identity_posture`, `identity_is_admin` and `identity_accounts_on`,
    so no page -- and no template -- has to ask twice.

    Swallows `DatabaseError` and answers "open, not an admin, accounts
    off" for a box mid-migration: a template that raised while rendering
    the nav would turn every page on an unmigrated box into a 500, which
    is precisely the state an operator is trying to fix.
    """
    try:
        row = settings_row_for(request)
        principal = principal_for_request(request, settings_row=row)
        return {
            "identity_posture": row.posture,
            "identity_is_admin": is_admin(principal, settings_row=row),
            "identity_accounts_on": accounts_on(settings_row=row),
        }
    except DatabaseError:
        return {
            "identity_posture": "open",
            "identity_is_admin": False,
            "identity_accounts_on": False,
        }
