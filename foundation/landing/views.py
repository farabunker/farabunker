"""
The landing page (`/`).

The front door. Until UI-1 the root URL 404'd -- every visitor had to know
a path before the box would speak to them. This page names what this
particular box can actually do right now, and says so honestly when the
answer is "nothing yet".

NO CONTEXT OF ITS OWN, DELIBERATELY. Everything it renders comes from
context processors the shared shell already runs on every page:
`surface_available` (`models.registry.context_processors.availability`)
for the cards, and `identity_*` for the account area in the shell. That
is not laziness -- it is what makes "the cards and the nav can never
disagree" true by construction rather than by review, and it is also why
this view issues no query and imports nothing from another column (the
import law's rule 2 forbids `foundation/` reaching into
`models.registry.models`, and this page never needs to).

Its route is class "A" (`identity/routes.py`): it behaves like every
other page on the box -- an accounts-on box redirects an anonymous
visitor to sign in rather than showing them an inventory of surfaces
before they have identified themselves. On an open box the gate returns
before it looks at the route at all, so it is simply the home page.
"""
from __future__ import annotations

from django.views.generic import TemplateView


class LandingView(TemplateView):
    """GET / -- entry cards for whatever this box can do."""

    template_name = "landing/index.html"
