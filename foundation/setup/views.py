"""
The universal setup page (/setup/).

Answers one question for an operator with a fresh box: *how do I get a model
into this thing?* It explains the register-then-bind flow once, then renders
ONE section per registered engine — built entirely from what each adapter
declares about itself (`setup_guide`, and the facts every adapter has) — and
finishes with a table of what each registered feature needs and whether it
has it yet.

Engine-agnostic by construction: this module and its template contain NO
engine name, port, path, or install command. A newly registered adapter gets
its own anchored section, its own live health line, and its own row in the
"served by" column with no change here. An adapter that declares no
`setup_guide` still gets a minimal card from `api_description`,
`well_known_ports`, `install_cmd_template`, and `library_url`.

Read-only and never-500: every engine call is wrapped, because this is the
page an operator lands on precisely WHEN things are not working.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.views.generic import TemplateView

from identity.access import is_admin
from identity.request import principal_for_request, settings_row_for
from models.contracts.bindings import resolve
from models.contracts.engines import ENGINES
from models.contracts.engines.base import SETUP_PLATFORM_LABELS, SETUP_PLATFORMS, SetupGuide
from models.contracts.roles import all_roles

logger = logging.getLogger(__name__)


def _platform_views(guide: SetupGuide | None) -> list[dict]:
    """The guide's platforms in display order (`SETUP_PLATFORMS`), each with
    its human label and whether it opens by default.

    Order comes from the platform tuple, not from the adapter's dict literal,
    so every engine's section reads the same way; the first platform is open
    so the page shows real steps without a click, and the others stay
    collapsed rather than burying the page in three OSes at once.
    """
    if guide is None:
        return []
    views = []
    for key in SETUP_PLATFORMS:
        steps = guide.platforms.get(key)
        if not steps:
            continue
        views.append(
            {
                "key": key,
                "label": SETUP_PLATFORM_LABELS[key],
                "steps": steps,
                "open": not views,
            }
        )
    return views


def _engine_views(*, is_admin: bool) -> list[dict]:
    """One view-model per registered engine.

    `status` is the honest three-way answer: "reachable", "unreachable", or
    "unknown" when there is no default endpoint to check at all. The health
    call is wrapped -- a broken adapter must not take down the page that
    explains how to fix it.

    IA-1: the reachability probe (an outbound call to a LAN endpoint) and
    the `verify_url` line it answers for are both computed ONLY when
    `is_admin` is true. This page is PUBLIC -- install guidance belongs to
    anyone landing on a fresh box -- but the endpoint address and whether
    it answers are inventory of the box, and firing the probe for an
    anonymous caller would also make this page an amplifier: a stranger
    could use it to make the box speak to arbitrary LAN addresses on their
    behalf. A non-admin gets the install steps with no endpoint line and
    no probe at all.
    """
    views = []
    for engine in ENGINES.values():
        endpoint = settings.INFERENCE_DEFAULT_ENDPOINTS.get(engine.name, "")
        guide = getattr(engine, "setup_guide", None)

        status = "unknown"
        verify_url = ""
        if is_admin:
            verify_path = guide.verify_url_path if guide else ""
            verify_url = f"{endpoint}{verify_path}" if endpoint and verify_path else ""
            if endpoint:
                try:
                    status = "reachable" if engine.is_healthy(endpoint) else "unreachable"
                except Exception:  # noqa: BLE001 -- a broken engine is unreachable, never a 500
                    logger.debug("Health check failed for %s at %r", engine.name, endpoint, exc_info=True)
                    status = "unreachable"

        views.append(
            {
                "name": engine.name,
                "anchor": f"engine-{engine.name}",
                "api_description": getattr(engine, "api_description", ""),
                "library_url": getattr(engine, "library_url", ""),
                "install_cmd_template": getattr(engine, "install_cmd_template", ""),
                "well_known_ports": getattr(engine, "well_known_ports", ()),
                "endpoint": endpoint,
                "guide": guide,
                "platforms": _platform_views(guide),
                "verify_url": verify_url,
                "status": status,
            }
        )
    return views


def _role_views(*, is_admin: bool) -> list[dict]:
    """One row per registered role for the "What each feature needs" table.

    `engines` lists the adapters that declare they can serve that capability
    (`serves_capabilities`) -- an honest "no registered engine serves this
    yet" when none do. `bound` answers "is it assigned right now?" through
    the real resolver, wrapped: an unassigned role is the normal state on a
    fresh box, not an error.

    IA-1: this whole table is bindings inventory -- which engine serves
    which role, and whether a role is assigned -- so it is built ONLY for
    an admin caller; a non-admin gets an empty list and the template skips
    the section entirely.
    """
    if not is_admin:
        return []
    rows = []
    for role in all_roles():
        servers = [
            engine.name
            for engine in ENGINES.values()
            if role.capability in getattr(engine, "serves_capabilities", ())
        ]
        try:
            resolve(role.key)
            bound = True
        except Exception:  # noqa: BLE001 -- unassigned (or a provider hiccup) reads as not bound
            bound = False
        rows.append(
            {
                "label": role.label,
                "capability": role.capability,
                "engines": servers,
                "bound": bound,
            }
        )
    return rows


class SetupView(TemplateView):
    """GET /setup/ -- how to install an engine, how a model reaches a role.

    PUBLIC (`identity/routes.py`'s `setup-index: PUBLIC`): the install
    guidance -- which engines exist, how to install one, how the
    register-then-bind flow works -- is what an operator needs BEFORE they
    can sign in to a box whose engines are not up yet, and it names no row,
    no document and no model choice. What IS gated, on `is_admin` alone,
    is the box's live inventory: each engine's configured endpoint, whether
    it answers, and the bindings table showing which role is assigned to
    what. See `_engine_views`/`_role_views`.
    """

    template_name = "setup/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # ZERO EXTRA QUERY on an open box: `IdentityGateMiddleware.
        # process_view` already fetched this row for every request
        # (`identity/middleware.py`) and stashed it as
        # `request.identity_settings_row` before this view ran --
        # `settings_row_for` reads THAT row rather than reading the
        # singleton again, which is what keeps this page at the one read
        # `identity/tests/test_zero_queries.py` pins for every mount.
        # `foundation/` may not import `identity.models` (import-law
        # rule 2) -- only `identity.request`/`identity.access` are
        # reached here, never the model itself.
        row = settings_row_for(self.request)
        principal = principal_for_request(self.request, settings_row=row)
        admin = is_admin(principal, settings_row=row)
        context["engines"] = _engine_views(is_admin=admin)
        context["roles"] = _role_views(is_admin=admin)
        context["setup_is_admin"] = admin
        return context
