"""The gate: may this principal be here at all.

INSTALLED AFTER `django.contrib.auth.middleware.AuthenticationMiddleware`
because it needs `request.user`, and therefore AFTER
`django.middleware.csrf.CsrfViewMiddleware` too. That ordering is NOT
changed: moving the gate ahead of CSRF would mean an unauthenticated
cross-site POST was evaluated for AUTHORISATION before it was evaluated
for FORGERY, which is the wrong order to fail in. The consequence, stated
rather than discovered: an anonymous POST with no CSRF cookie gets 403
from CSRF, and one with a valid CSRF cookie but no session reaches this
middleware and gets 302/401. Both are correct refusals and neither is a
500.

`process_view`, not `__call__`, so `request.resolver_match` is already
populated and the URL name is known without re-resolving it.

`LOGIN_URL` is a literal PATH (`/identity/login/`), matching where
`/identity/` is actually mounted (pinned by `identity/tests/
test_middleware.py::TestAnonymous::
test_the_redirect_target_really_is_the_login_url`) rather than a
resolver name -- redirecting through `django.contrib.auth.views.
redirect_to_login` with no explicit `login_url` argument (Django's own
pattern) resolves `settings.LOGIN_URL` without ever calling `reverse()`,
so this path stays correct even on a boot where URL resolution has not
run yet.

ONE `IdentitySettings.get_solo()` READ PER REQUEST, not three to five:
`process_view` fetches the row itself and threads it through
`identity.access`'s `settings_row=` keyword (`accounts_on`, `is_admin`)
and into `principal_for_request`'s optional `settings_row` argument,
instead of letting `accounts_on()`, `principal_for_request()` and
`is_admin()` each re-read the singleton off their own no-argument
forms. Open posture stays exactly one read either way -- this is about
not multiplying it for every other posture too.

`refuse_anonymous`/`refuse_not_admin` are PUBLIC, not private to this
module: `identity.gate.require_admin` refuses the same two ways for the
same reason, and imports these rather than restating either refusal --
one door, one JSON-vs-HTML decision, for both the middleware's coarse
gate and a view's own explicit check.

Deliberately SYNCHRONOUS. Django adapts a sync middleware under ASGI by
wrapping it in a thread; nothing here does its own I/O worth an `async
def`, so there is no reason to carry that complexity into a gate every
request passes through.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, JsonResponse

from identity.access import accounts_on, is_admin
from identity.models import IdentitySettings
from identity.request import principal_for_request
from identity.routes import ADMIN, AUTHENTICATED, ROUTE_RULES, tier_for

logger = logging.getLogger(__name__)


def _is_xhr(request) -> bool:
    """The existing polling clients on this box already send this header
    (`tools/vision/templates/vision/create.html`, the chat turn form's
    submit) so a poll or a submit gets a JSON 401 it can render rather
    than a login page it cannot. `tools/rag/templates/rag/ask.html`'s
    fetch calls send it too, for the same reason."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def refuse_anonymous(request):
    if _is_xhr(request):
        return JsonResponse(
            {"error": "sign-in required", "login_url": settings.LOGIN_URL},
            status=401,
        )
    return redirect_to_login(request.get_full_path())


def refuse_not_admin(request):
    """403 on an admin surface, JSON for an XHR poll exactly like
    `refuse_anonymous`'s 401 -- the ONE refusal both the middleware's
    coarse gate and `identity.gate.require_admin`'s explicit check use,
    so a poll never gets a different answer depending on which door
    refused it."""
    if _is_xhr(request):
        return JsonResponse({"error": "administrators only"}, status=403)
    return HttpResponseForbidden("This page is for administrators of this box.")


class IdentityGateMiddleware:
    """Coarse-tier enforcement, plus the rolling session window."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Return a response to refuse the request, or None to let it
        through.

        ONE ROW READ, right here, threaded through everything below it.
        IN OPEN POSTURE THIS RETURNS IMMEDIATELY, before it looks at the
        route, the user, or anything else. That is the first half of
        "an open box never runs a permission query"; this one read is
        the query that answers WHICH POSTURE, and the box must ask it
        before it can skip anything else.
        """
        row = IdentitySettings.get_solo()
        # Stashed on the request so `identity.context_processors.identity`
        # -- which every rendered page's template runs, via the shared
        # shell -- reads this SAME row instead of fetching its own. Without
        # this, `identity_identitysettings` would be read twice per
        # request on every page (once here, once by the template), which
        # is exactly what `test_middleware.py::TestTheSingleRowRead` pins
        # against.
        request.identity_settings_row = row
        if not accounts_on(settings_row=row):
            return None

        match = request.resolver_match
        url_name = match.url_name if match else None
        app_names = list(match.app_names) if match else []
        tier = tier_for(url_name or "", app_names)
        if url_name and url_name not in ROUTE_RULES and "admin" not in app_names:
            logger.warning(
                "identity: route %r is not classified in identity/routes.py; "
                "treating it as superuser-only", url_name,
            )

        principal = principal_for_request(request, settings_row=row)
        if tier in (AUTHENTICATED, ADMIN) and principal.kind == "anonymous":
            return refuse_anonymous(request)
        if tier == ADMIN and not is_admin(principal, settings_row=row):
            # 403 on an admin surface is fine and is used: the EXISTENCE
            # of a model console is not a secret. Row-addressed URLs
            # answer 404 instead, and that rule lives in the views.
            return refuse_not_admin(request)

        self._roll_the_session(request, row)
        return None

    def _roll_the_session(self, request, row):
        """A ROLLING idle window: every authenticated request pushes the
        expiry out again. `SESSION_SAVE_EVERY_REQUEST = True` is what
        makes it actually roll. Zero minutes means "expire when the
        browser closes", which is why one column covers both behaviours
        and no `SESSION_EXPIRE_AT_BROWSER_CLOSE` setting exists.

        `row`: the same `IdentitySettings` `process_view` already
        fetched -- `session_idle_minutes` comes off it directly rather
        than a second `get_solo()` call.
        """
        session = getattr(request, "session", None)
        if session is None or not getattr(request.user, "is_authenticated", False):
            return
        minutes = row.session_idle_minutes
        session.set_expiry(minutes * 60 if minutes else 0)
