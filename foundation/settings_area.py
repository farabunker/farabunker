"""The settings area: what it holds, who may see each entry, and the
`/settings/` landing route that sends a viewer to the first one they may.

UI-2 REPLACED THE APP BAR'S MANAGE ROW WITH ONE `Settings` LINK, and a
link needs somewhere to land. `/settings/` is not a page: it is a
redirect to the first section in sidebar order that this viewer is
allowed to open, so the entry never leads anywhere that would refuse
them. `Install guides` is ungated, so there is always at least one --
pinned by `foundation/tests/test_settings_area.py` rather than asserted
in a comment.

ONE TABLE, TWO READERS. `SETTINGS_GROUPS` below is the order, the
grouping and the gate of every settings-area entry; the sidebar in
`foundation/templates/_settings.html` renders the same order and the same
gates as template `{% if %}`s (a context processor would run on every
page in the box and cost a query the console's own query-count pin
forbids). The two are held together by a drift test that walks a real
rendered sidebar and asserts its first link is exactly where this module
redirects -- so the redirect can never point at an entry the sidebar
does not offer.

THE GATES ARE THE MANAGE ROW'S GATES, UNCHANGED. Nothing is newly
exposed and nothing newly hidden by the move:

  * EVERYONE      -- class P; Install guides, the page a person needs
                     before they can sign in to a box whose engines are
                     not up.
  * ADMIN         -- class S; a member who clicked would get a 403
                     (ORCHESTRATOR RULING R1). `is_admin` is True in the
                     open posture, so a household box sees these.
  * ACCOUNTS_ADMIN -- the same, AND accounts have to exist at all: an
                     open box administers no accounts, so it is offered
                     none of these.

ACCOUNTS_ADMIN IS DELIBERATELY NAV-ONLY. It hides these six entries from
the sidebar on an open box, but it has no counterpart at the route layer:
every one of those six pages is classified plain class `S` in
`identity/routes.py`, which resolves through `is_admin`, and `is_admin`
is True for everybody on an open box (same as ADMIN above). So on an open
box all six pages remain fully reachable and POSTable by URL -- this
gate controls what the sidebar OFFERS, not what the route ENFORCES. That
matters most for `identity-settings`: it is the only page from which an
open box can leave the open posture, and the sidebar hides the one page
that does that in exactly the posture where an operator would look for
it. The sanctioned door on an open box is not a route change here -- it
is the Install guides page's own "Accounts" card
(`foundation/setup/templates/setup/index.html:71-77`), rendered only in
the open posture, linking to `identity-settings` under the words "Turn
accounts on" rather than under the page's own name. Enforcing the
posture half of this gate at the route layer (403/404 on an open box)
remains open; this paragraph records the current, deliberate state
rather than proposing to close it.

Lives in `foundation/` because `foundation/` owns the shell and its nav
(`foundation/templates/_shell.html`), and this is the nav's own landing
route. A plain module rather than an app: it owns no model, no migration
and no template of its own, so there is nothing for `INSTALLED_APPS` to
register. `include()` reads `urlpatterns` off any module.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.shortcuts import redirect, resolve_url
from django.urls import path

from identity.access import is_admin as principal_is_admin
from identity.request import principal_for_request, settings_row_for

# THE GATE VOCABULARY LIVES IN `foundation/settings_help.py` (spec §3.1,
# author decision 1). ONE definition, so "admin" cannot come to mean two
# things in the two tables that both use it -- this module's `Entry.gate`
# and that module's `HelpCard.gate`, which a drift test compares for
# EQUALITY. An intra-column import, and re-exported here because every
# existing caller names this module.
from foundation.settings_help import (  # noqa: F401 -- re-exported on purpose
    ACCOUNTS_ADMIN, ADMIN, EVERYONE,
)


# THE SETTINGS ASSISTANT PANEL'S OPEN FLAG, AND THE THREE FUNCTIONS THAT
# WRITE IT. The panel's entire open/closed state is this ONE query
# parameter -- no script, no cookie, no `request.session` -- so "the
# panel stays open while I click through the settings" is true only if
# every piece of settings CHROME carries the parameter forward: the
# sidebar's links, the `/settings/` landing redirect, every settings
# form's `action`, and every settings POST view's redirect back.
#
# IT LIVES HERE, not in the panel's own column, because of the import
# law: the chrome that has to write it spans `identity/`, `models/`,
# `agents/` and `tools/`, and `foundation/` is the one column all of
# them may import (`agents/chat/context_processors.py` imports the name
# back from here for the panel itself, the same shape the gate
# vocabulary above already uses -- ONE definition, so "open" cannot come
# to mean two different strings in two columns).
ASSISTANT_OPEN_PARAM = "assistant"


def with_assistant_flag(url: str) -> str:
    """`url`, guaranteed to carry `?assistant=1` -- EXACTLY ONCE.

    PARSED, NOT SUBSTRING-MATCHED: checking `"assistant=1" in url` would
    also true-positive on an unrelated `notassistant=1`, so this reads
    the actual query dict for the real key. Idempotence is what lets
    every caller append without first asking whether somebody else
    already did -- three script-free asks in a row measured `[1, 2, 3]`
    copies of the parameter in the address bar before this guard existed
    (`agents/chat/views/assistant.py::_back`, Task 10 review).

    FRAGMENT-AWARE, because the chrome writes this onto URLs other code
    built: the assistant's own jump strip links a card's anchor
    (`/rag/settings/#retrieval-top-k`), and a bare append would bury the
    parameter INSIDE the fragment, where no query parser would ever see
    it. Split and rebuilt rather than concatenated, so the query lands
    in the query and the fragment survives untouched.

    IDEMPOTENCE KEYS ON THE VALUE, NOT ON THE KEY (review round 1, N2).
    `?assistant=0` -- which nothing in this tree writes, but a hand-typed
    or stale URL can carry -- used to come back untouched from a function
    whose whole promise is that its result OPENS the panel. Appending a
    second copy would be no better: `request.GET.get` reads the FIRST, so
    the stale value would still win. A value that is not `1` is therefore
    REPLACED, which is also what normalises a doubled parameter back to
    one. The replace leg rebuilds the whole query through `urlencode`,
    and the cheap append leg is kept for the overwhelmingly common case
    precisely so that re-encoding never touches a URL another module
    built (the console's `?endpoint=` is `quote`d, not `urlencode`d, and
    a round-trip through `parse_qs` would silently re-spell it).
    """
    split = urlsplit(url)
    values = parse_qs(split.query, keep_blank_values=True).get(ASSISTANT_OPEN_PARAM)
    if values == ["1"]:
        return url
    if values is not None:
        params = parse_qs(split.query, keep_blank_values=True)
        params[ASSISTANT_OPEN_PARAM] = ["1"]
        return urlunsplit((split.scheme, split.netloc, split.path,
                           urlencode(params, doseq=True), split.fragment))
    query = f"{split.query}&{ASSISTANT_OPEN_PARAM}=1" if split.query else f"{ASSISTANT_OPEN_PARAM}=1"
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))


def preserve_assistant_flag(request, url: str) -> str:
    """`url` with the panel's open flag appended IFF `request` carried
    it -- the rule every settings POST view redirects through.

    The contract, in one sentence: a settings form whose `action`
    carried `?assistant=1` (which is what the settings templates append
    while the panel is open) redirects back to a page that still has the
    panel open, and a form that did not is redirected EXACTLY where it
    always was. Pure string work over `request.GET` -- no query, no
    session, no cookie, nothing stored on the client.

    `== "1"` is the same read `agents.chat.context_processors.
    panel_context` makes to decide whether the panel is open at all: a
    flag that counted any truthy-looking value would be a second
    definition of open.
    """
    if request.GET.get(ASSISTANT_OPEN_PARAM) != "1":
        return url
    return with_assistant_flag(url)


def settings_redirect(request, to, *args, **kwargs):
    """`django.shortcuts.redirect(to, ...)` for a settings page, with the
    panel's open flag preserved.

    Sugar over `preserve_assistant_flag` above, and it exists because the
    alternative is ~60 call sites each spelling out `redirect(
    preserve_assistant_flag(request, reverse(...)))`: a settings POST
    view says `return settings_redirect(request, "rag-settings")` and is
    correct by construction. Takes whatever `redirect` takes (a route
    name with arguments, or a built URL) via the same `resolve_url` it
    uses itself, so a call site converts by adding `request` and nothing
    else.
    """
    return redirect(preserve_assistant_flag(request, resolve_url(to, *args, **kwargs)))


@dataclass(frozen=True)
class Entry:
    """One sidebar entry: what it is called, where it goes, who may see
    it, and -- OPTIONALLY -- which `FARABUNKER_FEATURES` token must be
    enabled for it to exist at all.

    `feature` is `None` for every entry that predates the Engine files
    page (2026-09-02): none of them is behind a feature flag, only ever
    behind standing. `Engine files` is `url_name="vision-engine-files"`,
    a route `config/urls.py` mounts ONLY while `"vision"` is enabled
    (`tools/vision/urls.py`) -- `reverse()`-ing it with the feature off
    would raise `NoReverseMatch`, so this gate must be checked BEFORE
    the entry is ever offered, the same way `_shell.html`'s own
    `surface_available.images` keeps the app bar from linking a route
    that does not exist.
    """

    label: str
    url_name: str
    gate: str
    feature: str | None = None


# THE ONLY TABLE -- group label, then that group's entries in order. A
# group whose every entry is hidden renders nothing at all, label
# included; `Setup` cannot empty out, because `Install guides` is
# EVERYONE.
SETTINGS_GROUPS: tuple[tuple[str, tuple[Entry, ...]], ...] = (
    ("Setup", (
        Entry("Models", "inference-console", ADMIN),
        Entry("Library", "rag-settings", ADMIN),
        # ROUND 21: how a conversation's prompt is built (today: whether
        # the model is told the date and time). ADMIN like Models and
        # Library -- it is box-wide operator policy, not a per-person
        # preference.
        Entry("Chat", "chat-settings", ADMIN),
        # F1 (Coherence Wave C): the four `JobSettings` controls used to
        # be embedded in the QUEUE page -- an activity surface this
        # module's own docstring is careful to say is not part of the
        # settings area. The page is; the queue itself still is not.
        # ADMIN like Models/Library/Chat, and NO `feature`: `JobSettings`
        # is core, obeyed by every queued job on every box whatever
        # `FARABUNKER_FEATURES` holds, so this entry must never be
        # flag-gated.
        #
        # SETUP, NOT BOX, AND THE GATE IS WHY (M2, Wave C review). Box is
        # the group that reads as "facts about this machine", which is
        # where a memory budget sounds like it belongs -- but Box's every
        # entry is ACCOUNTS_ADMIN, which this module's own docstring
        # records as NAV-ONLY and false on an open box: a household box
        # is offered no Box entry at all. Putting the queue's four
        # execution knobs there would hide core policy from exactly the
        # boxes most likely to hit it. Setup is ADMIN, which `is_admin`
        # answers True for everybody in the open posture, so the page is
        # offered wherever it can be used.
        Entry("Job execution", "jobs-settings", ADMIN),
        # Vision-owned, ADMIN like Models/Library, AND flag-guarded: with
        # "vision" off there is no `/vision/` route at all
        # (`config/urls.py`), so this entry must not render either --
        # `Entry.feature`'s own docstring.
        Entry("Engine files", "vision-engine-files", ADMIN, feature="vision"),
        Entry("Install guides", "setup-index", EVERYONE),
    )),
    ("Access", (
        Entry("Accounts", "identity-users", ACCOUNTS_ADMIN),
        Entry("Groups", "identity-groups", ACCOUNTS_ADMIN),
        Entry("Entitlements", "identity-entitlements", ACCOUNTS_ADMIN),
        Entry("Tool access", "chat-tool-entitlements", ACCOUNTS_ADMIN),
        Entry("Agent access", "chat-agent-entitlements", ACCOUNTS_ADMIN),
    )),
    ("Box", (
        Entry("Identity & security", "identity-settings", ACCOUNTS_ADMIN),
    )),
)


def _may_see(entry: Entry, *, admin: bool, posture: str, features: frozenset[str]) -> bool:
    if entry.feature is not None and entry.feature not in features:
        return False
    if entry.gate == EVERYONE:
        return True
    if entry.gate == ADMIN:
        return admin
    return admin and posture != "open"


def visible_entries(
    *, admin: bool, posture: str, features: frozenset[str] = frozenset()
) -> tuple[Entry, ...]:
    """Every entry this viewer may open, in sidebar order.

    `features` defaults to empty, NOT to `settings.FARABUNKER_FEATURES`:
    this module stays a plain function of its arguments (its existing
    callers -- `foundation/tests/test_shell.py`'s drift test chief among
    them -- already pass `admin`/`posture` explicitly rather than
    reading them off a request), and a caller that cares about a
    feature-gated entry passes its own `features` the same way.
    """
    return tuple(
        entry
        for _label, entries in SETTINGS_GROUPS
        for entry in entries
        if _may_see(entry, admin=admin, posture=posture, features=features)
    )


def first_entry(*, admin: bool, posture: str, features: frozenset[str] = frozenset()) -> Entry:
    """Where `/settings/` sends this viewer.

    Never raises: `Install guides` has no gate, so the sequence is never
    empty for anybody -- `test_settings_area.py` pins that for every
    posture/principal combination this box has.
    """
    return visible_entries(admin=admin, posture=posture, features=features)[0]


def settings_index(request):
    """GET /settings/ -- the app bar's `Settings` entry.

    Reads the ONE `IdentitySettings` row the gate middleware already
    stashed on the request (`identity.request.settings_row_for`) and
    resolves the caller off it, so landing here costs no query the page
    it redirects to would not have run anyway.

    NOT a copy of `identity.context_processors.identity`'s read, though
    it uses the same two helpers: that one wraps itself in `except
    DatabaseError` and answers "open, not an admin" for a box
    mid-`migrate`, because it renders the nav of EVERY page and a raise
    there would 500 the very page an operator is trying to fix. This is
    one route, and `IdentityGateMiddleware` has already touched the same
    row before it runs -- so on an unmigrated box the request has failed
    before it reaches here, and a swallow of our own would only be dead
    code claiming to be a safety net.
    """
    row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=row)
    entry = first_entry(
        admin=principal_is_admin(principal, settings_row=row),
        posture=row.posture,
        features=settings.FARABUNKER_FEATURES,
    )
    # THE BAR'S `Settings` ENTRY IS A SETTINGS LINK TOO. Arriving here
    # with the assistant panel open must not close it, so the flag this
    # request carried rides the redirect on to the page it lands on --
    # the same rule the sidebar's own links and every settings form
    # follow. Unconditional on standing: a viewer the panel does not
    # render for simply lands on a page whose chrome ignores the
    # parameter, which is what it already does everywhere else.
    return settings_redirect(request, entry.url_name)


urlpatterns = [
    path("", settings_index, name="settings-index"),
]
