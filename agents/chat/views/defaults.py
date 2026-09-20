"""Adopting a shipped default -- the page half of ruling 2.

ONE POST, and it is the only way a row appears on this box short of the
command that does the same thing. Not a GET: a page load must never
write, and `@require_POST` plus a real `<form>` is what makes that
true for a link somebody bookmarks, a prefetcher, and a crawler alike.

Idempotent because `install_default` is create-if-absent -- a
double-clicked button installs one row, and an already-installed
default is a no-op rather than an error, because the operator asked for
a state and that state is what they get.

A SETTINGS-SURFACE SLUG (`agents.defaults.SETTINGS_SURFACE_SLUGS`) IS
REFUSED HERE FOR ANYBODY BUT AN ADMINISTRATOR (Task 10 amendment 5,
orchestrator ruling on Task 6 advisory-5). This route is class A
(`identity/routes.py`) -- any signed-in caller, no row rule -- and the
Task 6 exclusion only kept a settings-surface agent off the `/chat/`
OFFERS list; it never stopped a crafted POST straight at this door,
which is reachable to every member regardless of whether they can see
the button. The settings assistant's own "Add the settings assistant"
offer (`chat/_assistant_panel.html`) posts to this SAME route (spec
§6.5 decision 12: three lines on an existing route beat a fourth one)
and is only ever RENDERED for an administrator -- so gating this door on
`is_admin` for exactly these slugs, rather than refusing the route
outright, is what keeps the assistant's own consent flow working while
closing it to everybody else. `is_admin` is True for everybody on an
open box, the same "household box" reasoning every other class-S/gated
surface in this settings area already leans on.
"""
from __future__ import annotations

from django.contrib import messages
from django.db import IntegrityError
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from agents.chat.service import validated_next_url
from agents.defaults import SETTINGS_SURFACE_SLUGS, install_default
from identity.access import is_admin
from identity.request import principal_for_request, settings_row_for

_KINDS = ("agent", "flow")

# CASE-INSENSITIVE, the same `Lower("slug")` comparison
# `agents.visibility.chat_surface_agents` makes and for the same reason
# (Task 6 review, Finding 4): slug identity is case-insensitive
# everywhere else this platform tests it.
_SETTINGS_SURFACE_SLUGS_CI = frozenset(slug.lower() for slug in SETTINGS_SURFACE_SLUGS)

_SETTINGS_SURFACE_REFUSAL = (
    "The settings assistant installs from its own panel, for an administrator only."
)


def _back(request) -> str:
    """Where an install returns to.

    THE PANEL'S OFFER POSTS HERE (settings assistant, spec §6.5, decision
    12): three lines on an existing route beats a fourth route, and
    `validated_next_url` -- which already guards this exact question for
    `views/conversations.py` and `views/turns.py` -- is what keeps a
    caller-supplied `next` from being an open redirect off this box.
    `chat-index` when there is no usable value, which is every caller
    that existed before this phase.
    """
    return validated_next_url(request) or reverse("chat-index")


@require_POST
def default_install(request):
    """POST /chat/defaults/install/ -- adopt one shipped default.
    Body: `kind` in {"agent", "flow"}, `slug`. Redirects back.

    See this module's own docstring for the settings-surface refusal:
    `is_admin` is only even CHECKED (let alone read from the database)
    when `slug` names a `SETTINGS_SURFACE_SLUGS` entry -- an ordinary
    default install pays no extra query for a check that will never
    refuse it.
    """
    kind = (request.POST.get("kind") or "").strip()
    slug = (request.POST.get("slug") or "").strip()
    if kind not in _KINDS:
        # ITEM 6 (Coherence Wave D): flash-and-redirect, not a raw 400 --
        # the house convention (`identity/views.py:317-321`), never a
        # bare plain-text page. Every caller is a plain zero-JS
        # `<form>`/hidden-`kind` pair (`chat/_offers.html:23`, `chat/
        # _assistant_panel.html:159`), and `_back(request)` is already
        # this view's own redirect target for its two success paths.
        messages.error(request, f"Unknown kind {kind!r}; must be one of {list(_KINDS)}.")
        return redirect(_back(request))
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    # SCOPED BY SLUG ALONE, NOT BY `kind == "agent"` (Task 10 review, a3
    # -- hardening, not a live gap: `SETTINGS_SURFACE_SLUGS` holds only
    # an agent slug today, and a `kind="flow"` smuggle of that same slug
    # is already refused (flash + redirect, Coherence Wave D, item 6 --
    # was a bare 400 until then) inside `install_default` for want of a
    # matching flow.
    # A `kind` TERM here would silently stop refusing the day a
    # settings-surface FLOW slug is ever added to that set -- the refusal
    # exists for the SLUG, and checking it before `kind` is even known to
    # be valid is what keeps that true regardless of which kind carries
    # it.
    if (slug.lower() in _SETTINGS_SURFACE_SLUGS_CI
            and not is_admin(principal, settings_row=settings_row)):
        return HttpResponseForbidden(_SETTINGS_SURFACE_REFUSAL)
    try:
        install_default(kind, slug, principal)
    except ValueError as exc:
        # An unknown slug is a caller error, not a server fault: the
        # button was rendered from the catalogue, so a slug that is not
        # in it means the page is older than the code. ITEM 6 (Coherence
        # Wave D): flash-and-redirect, not a raw 400 -- see this
        # function's own `kind` check just above for the same reasoning.
        messages.error(request, str(exc))
        return redirect(_back(request))
    except IntegrityError:
        # `install_default` is create-if-absent, but its own
        # `.filter().first() is None` check and its `INSERT` are not
        # one atomic operation -- two concurrent double-clicks (or two
        # browser tabs) can both pass the check before either commits,
        # and the second `INSERT` then hits the unique constraint. The
        # requested state (the row exists) is already true by the time
        # this is caught, so this redirects exactly like a normal
        # create-if-absent no-op would, never a 500 for a race whose
        # outcome the operator already got.
        return redirect(_back(request))
    return redirect(_back(request))
