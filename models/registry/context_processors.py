"""The availability signal, in every template's context.

Registered in `TEMPLATES[0]["OPTIONS"]["context_processors"]` beside
`tools.vision.context_processors.features` and
`identity.context_processors.identity`, for exactly the same reason both
of those are: the shared shell (`foundation/templates/_shell.html`) has
to decide whether to render a nav entry on EVERY page, including pages
whose own view knows nothing about model bindings, and a context
processor is the one mechanism that reaches all of them. The landing page
(`foundation/landing/`) reads the same two keys for its entry cards.

It lives in `models/registry` because that is the column that owns
bindings (`models/registry/models.py::RoleBinding`). Nothing outside this
column imports it -- Django resolves the dotted path from settings, which
is not a Python import by any column, exactly as it already does for the
two processors above.

TWO KEYS, AND THE SECOND IS THE POINT:

  * `bound_roles` -- the raw frozenset of bound role keys. The primitive;
    what the tests pin, and what a future surface with a role of its own
    reads without this module having to grow a key for it.
  * `surface_available` -- the DERIVED map the templates actually read.
    It exists so the composite rules live in ONE place: "Ask needs the
    answer role AND the embedding role", "Images needs the vision feature
    AND the generate role". The shell and the landing page both gate on
    those rules, and a rule retyped in two templates is a rule that will
    disagree with itself.

`any_model_surface` is a key of that same map rather than a third
context key: it is the same question ("is anything usable bound?"),
answered once here instead of as a four-term `{% if not ... %}` chain in
the landing template.

DB **OR** ENV, because `models.contracts.bindings.resolve()` is a db ->
env chain and both halves make a role live. A box with no `RoleBinding`
row but `LLM_MODEL`/`EMBED_MODEL` set in its environment answers Ask and
Search perfectly well, and the Models console already reports those
roles as bound -- a nav that hid them, and a landing page that said "No
models are set up yet", would be two surfaces of one box contradicting
each other, with the front door telling an operator with a working
install that they have nothing.

The env half is read straight off `settings` here rather than added to
`models.registry.availability`'s cached set, and that placement is the
point: it costs no query (so it needs no cache), and staying OUT of the
cached set means a `settings` override takes effect on the very next
render instead of whenever a 30-second TTL happens to lapse.

NO QUERY OF ITS OWN ON THE WARM PATH: `models.registry.availability.
bound_role_keys()` caches the set in process memory and invalidates on a
binding write -- see that module's docstring for the whole rule,
including why it deliberately bypasses the cache inside a transaction.
"""
from __future__ import annotations

from django.conf import settings

from models.contracts.roles import (
    CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, VISION_GENERATE_ROLE,
)
from models.registry.availability import bound_role_keys


def availability(request) -> dict:
    """`bound_roles` and `surface_available` for every template."""
    bound = bound_role_keys()
    # The env half of the db -> env chain (`models.contracts.bindings.
    # env_provider` resolves exactly these two roles, and only these two).
    # No query, and deliberately not folded into the cached set above.
    if settings.LLM_MODEL:
        bound = bound | {RAG_ANSWER_ROLE}
    if settings.EMBED_MODEL:
        bound = bound | {RAG_EMBED_ROLE}
    chat = CHAT_CONVERSE_ROLE in bound
    # Ask retrieves BEFORE it answers, so an answer model with no
    # embedding model behind it is an Ask page that can only ever say
    # "nothing matched" -- both roles, or the entry is a dead end.
    ask = RAG_ANSWER_ROLE in bound and RAG_EMBED_ROLE in bound
    search = RAG_EMBED_ROLE in bound
    # The feature flag first: with "vision" off there is no /vision/
    # route at all (config/urls.py), so a template must never reach
    # `{% url 'vision-create' %}` -- keeping the flag INSIDE this rule is
    # what lets the shell write a single `{% if %}` instead of two.
    images = (
        "vision" in settings.FARABUNKER_FEATURES and VISION_GENERATE_ROLE in bound
    )
    return {
        "bound_roles": bound,
        "surface_available": {
            "chat": chat,
            "ask": ask,
            "search": search,
            "images": images,
            "any_model_surface": chat or ask or search or images,
        },
    }
