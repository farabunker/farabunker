"""Explicit per-view checks, for the handful of callers that want one.

THE MIDDLEWARE IS THE MECHANISM; THESE ARE THE BELT. A view reached
through the URL conf is already gated by `IdentityGateMiddleware`, and
nothing in IA-1 needs these. They exist for the callers that do not
arrive through the middleware -- a management command's future HTTP
counterpart, the MCP edge -- and so a reviewer reading a view can see
the rule at the view rather than only in a table.
"""
from __future__ import annotations

from functools import wraps

from identity.access import is_admin
from identity.middleware import refuse_anonymous, refuse_not_admin
from identity.request import principal_for_request


def require_principal(view):
    """Refuse an anonymous caller: 302 for a browser, 401 for a poll."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if principal_for_request(request).kind == "anonymous":
            return refuse_anonymous(request)
        return view(request, *args, **kwargs)
    return wrapper


def require_admin(view):
    """Refuse anybody who is not an administrator of this box. 403, not
    404: the existence of an admin surface is not a secret.

    `refuse_not_admin` -- the SAME function `IdentityGateMiddleware` uses
    -- so an XHR reaching this decorator's check gets the identical JSON
    401/403 the middleware would have answered, rather than a second,
    HTML-only refusal that happened to share one sentence with it.
    """
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        principal = principal_for_request(request)
        if principal.kind == "anonymous":
            return refuse_anonymous(request)
        if not is_admin(principal):
            return refuse_not_admin(request)
        return view(request, *args, **kwargs)
    return wrapper
