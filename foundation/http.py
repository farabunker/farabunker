"""
Shared response-header helper for entitlement-gated byte routes (B-8, cache
half, round-3 hardening).

Unlike `foundation/files.py` and `foundation/format.py`, this module DOES
import Django (`django.utils.cache.patch_vary_headers`) -- it operates on an
`HttpResponse`, which has no meaning outside a configured Django project.
It stays in `foundation/` anyway, alongside those two pure leaves, for the
same reason ADR 0014 gives them a shared home: more than one column's view
layer (`tools.rag`, `tools.vision`) needs the identical response-marking
logic, and `foundation/` is the platform's base layer both may import
without either one reaching into the other's trust domain (see
`foundation/README.md`).
"""
from __future__ import annotations

from django.http import HttpResponse
from django.utils.cache import patch_vary_headers


def mark_private(response: HttpResponse) -> HttpResponse:
    """Mark `response` as private, uncacheable content (B-8): sets
    `Cache-Control: private, no-store, max-age=0` and adds `Cookie` to
    `Vary`.

    Every entitlement-gated byte route (a document's original file, its
    transcript, a vision job's generated output or input) answers with no
    `Cache-Control` and no `Vary: Cookie` today -- the framework adds
    neither by default for these responses. On a shared appliance browser
    the next person to use it can pull a labelled document out of the disk
    cache after the session that fetched it is gone; behind any caching
    proxy on the LAN, a response cached without `Vary: Cookie` can reach a
    principal who never held the entitlement that produced it. No proxy is
    deployed today -- this is hardening, not a live path.

    `Cache-Control` is set OUTRIGHT (there is nothing legitimate for an
    entitlement-gated content route to have set first), but `Vary` is
    patched via `django.utils.cache.patch_vary_headers` rather than
    assigned wholesale, so a `Vary` header a view already set for its own
    reasons keeps whatever it already named -- this helper only ever ADDS
    `Cookie` to the set, never replaces it. That also makes the call
    idempotent: calling it twice on the same response leaves `Cookie`
    listed exactly once.

    Returns `response` for chaining -- every call site sets this on the
    response object before `return`, the same placement H23's own
    `X-Content-Type-Options: nosniff` uses.
    """
    response["Cache-Control"] = "private, no-store, max-age=0"
    patch_vary_headers(response, ["Cookie"])
    return response
