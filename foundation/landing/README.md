# foundation/landing/ — the front door

`GET /` — the page a visitor lands on when they type the box's address and nothing else.
Before UI-1 the root URL 404'd: you had to already know a path.

## What it renders

One entry card per surface this box can actually use, plus the Document library (which is
storage and needs no model bound at all). "Can actually use" is the **availability signal**
— `models.registry.context_processors.availability` — the *same* `surface_available` keys the
shared shell gates its nav entries on, which is what makes "the cards and the nav can never
disagree" true by construction rather than by review.

With nothing bound the page says so, in those words, and points at
[Install guides](../setup/README.md) (install and reach an engine) and Models (bind a model to
a role). It never offers a link to a surface that would only be able to apologise.

## What it does not do

- **No context of its own.** The view is a bare `TemplateView`; everything on the page comes
  from context processors the shell already runs. It issues no query.
- **No import from another column.** Import law rule 2 forbids `foundation/` reaching into
  `models.registry.models`, and this page never needs to.
- **No health check.** Availability means *bound*, not *reachable* — see
  `models/registry/README.md`, "The availability signal".

## Route class

`"landing": "A"` (`identity/routes.py`) — an ordinary page, not public like Setup. Setup is
public because it is what a person needs *before* they can sign in to a box whose engines are
down. The landing page is an inventory of this box's capability, and an accounts-on box
redirects an anonymous visitor to sign in first. On an open box the gate returns before it
looks at the route at all.
