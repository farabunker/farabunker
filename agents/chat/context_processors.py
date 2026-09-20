"""The settings assistant panel's context (spec §6.2, §6.3).

A CONTEXT PROCESSOR, AND THE OBJECTION IS ANSWERED RATHER THAN IGNORED.
`foundation/settings_area.py:12-20` rejected a context processor for the
settings SIDEBAR, for a stated reason: one "would run on every page in
the box and cost a query the console's own query-count pin forbids". So
the FIRST clause below is a `resolver_match.url_name` membership test
against a pure `frozenset`, and on every page in the box that is not a
settings page this processor costs one attribute read, one set lookup and
ZERO queries.

THE SECOND CLAUSE IS A GATE, NOT AN OPTIMISATION, and it is this
repository's own named defect class. `_settings.html`'s `{% if
identity_is_admin %}` decides what RENDERS; it cannot decide what was
BUILT, and a gate that lives one layer above the work it guards is
render-vs-gate inverted. The proving case is real: `setup-index`
("Install guides") is a settings-area entry gated EVERYONE with route
class P -- PUBLIC -- and `foundation/setup/templates/setup/index.html`
extends `_settings.html`. So on an accounts-on box an ANONYMOUS visitor
renders a settings page, passes the first clause (Install guides has a
card; the drift test requires one), and, with a hand-typed
`?assistant=1`, would otherwise have an agent row, a conversation lookup
and six turns resolved for them before a `{% if %}` threw it all away. It
is not a leak today -- `visible_conversations(ANONYMOUS)` returns that
principal's own nothing -- it is a leak SHAPE, on the box's one public
settings page, and every field a later phase adds to this context would
inherit it.

NO PANEL CONTEXT KEY IS PRODUCED, AND NO CONVERSATION OR TURN IS READ,
FOR A PRINCIPAL THE PANEL WILL NOT RENDER FOR.

IT LIVES IN `agents/chat` because it reads `Conversation`/`Turn` rows,
and it reads them through `agents/visibility.py`, never through a manager
(`agents/visibility.py:14`). Registered in `config/settings.py`'s
`TEMPLATES` -- the composition root is the one place that already names
every column. The alternatives are worse and one is impossible: per-view
context would need an edit in every column that owns a settings page, and
`identity/` -- which owns four of them -- may not import `agents/`
AT ALL; a custom template tag would work, but this repository has no
`templatetags` package anywhere, so it would be a brand-new mechanism
where an existing one fits.

IT READS THROUGH THE UN-NARROWED GATE, deliberately:
`visible_conversations(principal).filter(agent__slug=...)`, not
`chat_surface_conversations`. This IS the surface the chat-surface
exclusion excludes FROM.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from django.urls import NoReverseMatch, reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe

from agents.chat.rendering import render_answer
from agents.chat.service import (
    MAX_POLL_DURATION_MS, MAX_TRANSPORT_RETRIES, POLL_INTERVAL_MS,
)
from agents.models import Turn
from agents.visibility import visible_agents, visible_conversations
# THE OPEN PARAMETER'S NAME AND ITS APPEND RULE LIVE IN `foundation/`,
# and are imported back here under the names this module has always used
# for them. They moved when the settings CHROME learned to carry the
# panel's state (the sidebar's links, every settings form's `action`,
# every settings POST view's redirect): that chrome spans `identity/`,
# `models/`, `agents/` and `tools/`, and `foundation/` is the one column
# every one of them may import -- `identity/` may not import `agents/`
# AT ALL (this module's own docstring says so, above). ONE definition of
# the parameter, in the column that owns the settings area, exactly as
# the gate vocabulary is one definition in `foundation/settings_help.py`.
from foundation.settings_area import ASSISTANT_OPEN_PARAM as OPEN_PARAM
from foundation.settings_area import with_assistant_flag as with_open_param
from foundation.settings_help import CARDS, card_for, card_routes
from identity.access import is_admin, owner_fields
from identity.request import principal_for_request, settings_row_for

# The slug the catalogue declares (`agents/defaults.py`). Named here
# rather than imported from `SETTINGS_SURFACE_SLUGS`, which is a SET
# expressing a different question ("which agents are off `/chat/`"): this
# module needs the ONE agent it renders, and a `next(iter(...))` over a
# set would be that set silently deciding which agent the panel is for.
ASSISTANT_SLUG = "settings-helper"

# How many recent turns the open panel renders. SIX, not a page: this is
# a guide's last exchange or two, not a thread reader -- `/chat/` is where
# a whole history is read, and this surface has no scrollback affordance
# of its own to make more useful.
ASSISTANT_PANEL_TURNS = 6

# The jump strip's own cap (placement-round follow-up, owner-found
# defect, 2026-09-12). Anchor links from the most recent tool call are
# already small by construction (bounded by one page's own field
# count); the NAMED-PAGE leg `_named_page_links` adds is bounded only
# by how many of the registry's titles/route-names the final answer
# happens to name, which a broad or hostile-but-still-whitelisted
# answer could in principle make all of them. SIX, the same
# number as `ASSISTANT_PANEL_TURNS` above for the identical reason: a
# guide's jump strip is a short wayfinding aid, not an index of every
# settings page on the box.
MAX_JUMP_LINKS = 6

# THE PANEL'S OPEN-STATE PARAMETER (spec §6.3, decision 9) is
# `OPEN_PARAM`, imported above from `foundation.settings_area`, and its
# append rule is `with_open_param` beside it. ONE query parameter is what
# makes "it persists across screens" true with no script and no client
# storage: the ask/reset redirects carry it, every link the assistant
# emits carries it, AND -- since the persistence round -- so does every
# link and every form in the settings chrome the panel sits on. A
# session of asking, clicking through the sidebar and saving settings
# keeps the panel open the whole way; a plain navigation to a settings
# page starts collapsed.


def without_open_param(url: str) -> str:
    """The inverse of the imported `with_open_param` -- `url` with
    `?assistant=1` removed, if present, and every other query parameter
    untouched.

    STAYS IN THIS COLUMN while its inverse moved to `foundation/`: the
    settings chrome only ever ADDS the flag, so closing the panel is the
    panel's own business and has exactly one caller, the Close link
    built a few lines below.

    PLACEMENT-ROUND FOLLOW-UP (owner review, 2026-09-11): the closed
    corner bubble is now a real link to `with_open_param(here)` (the
    review finding this closes -- see `chat/_assistant_panel.html`'s own
    docstring), and the open panel needs the reverse link to collapse
    back to. PARSED, NOT SUBSTRING-MATCHED, for the identical reason
    `with_open_param` is: `"?assistant=1" not in url.replace(...)` style
    surgery would also mangle an unrelated `notassistant=1`.
    """
    split = urlsplit(url)
    params = parse_qs(split.query, keep_blank_values=True)
    params.pop(OPEN_PARAM, None)
    query = urlencode(params, doseq=True)
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))


def settings_assistant(request) -> dict:
    """The panel's context, or `{}` -- registered in
    `config/settings.py::TEMPLATES`.

    TWO CLAUSES, IN THIS ORDER, AND BOTH RUN BEFORE ANY ROW IS READ. See
    this module's docstring for why the second one is a gate rather than
    a tidiness.
    """
    match = getattr(request, "resolver_match", None)
    if match is None or match.url_name not in card_routes():
        return {}
    row = settings_row_for(request)
    # `settings_row=row` ON BOTH CALLS, and it is not a flourish:
    # `principal_for_request` reads the singleton itself through
    # `accounts_on()` when it is not given one (`identity/request.py:91`),
    # and `is_admin` reads it again. Threading the row the gate
    # middleware already stashed is what makes this whole clause cost
    # ZERO queries -- and `_user_row`'s memoisation on that same instance
    # (`identity/access.py:80-93`) is what keeps the administrator branch
    # from re-reading `auth_user` a second time in one request.
    principal = principal_for_request(request, settings_row=row)
    if not is_admin(principal, settings_row=row):
        return {}
    return panel_context(request, principal=principal, settings_row=row)


def panel_context(request, *, principal=None, settings_row=None, error: str = "",
                  is_open=None, next_url: str = "") -> dict:
    """`{"assistant": {...}}` -- the ONE key, built once.

    THE SAME BUILDER THE PANEL ROUTE CALLS, so the inline render and the
    poll body come out of the SAME function over the SAME rows -- the
    `chat/_turn_block.html` precedent, where "this page's inline render
    and `turn_status`'s poll body come out of the SAME loop over the SAME
    fragment".

    ONE KEY, not a dozen: "an anonymous visitor gets no panel context at
    all" is then a single assertion a later phase cannot erode by adding
    a twelfth name.

    `settings_row` is THREADED, never re-fetched: the row the gate
    middleware already stashed, handed to every access call this makes,
    so the panel reads the `IdentitySettings` singleton once per request
    -- collapsed AND open. (Task 8 review, R2: the open branch's own
    `visible_conversations` call used to re-read the singleton a second
    time, through its internal `owned_rows_q` -> `is_admin`; that leg now
    threads the same row, which is what makes this claim true on the
    path the panel exists to serve, not merely on the cheaper one.)

    `is_open` AND `next_url` ARE EXPLICIT PARAMETERS, and that is the
    whole difference between a panel that works and one that looks like
    it does. Rendered INLINE on a settings page, "is the panel open" and
    "which page does it belong to" really are the current request's own
    query string and path. Rendered from `settings-assistant-ask` -- a
    POST with NO query string -- or from `settings-assistant-panel` --
    whose own URL is not a settings page at all -- they are not:
    `request.GET` is empty there, so the fragment would come back
    COLLAPSED, with no transcript and no pending marker, and the poller
    would stop on the first tick. That is the one path the script exists
    to serve. Reading `get_full_path()` there is the same defect on the
    other axis: it would hand the composer its own action URL as a
    `next`, so a transport-failure fallback would GET a `@require_POST`
    view (405) and each poll would re-encode the previous poll's URL
    into the next one until the query string ran away.
    """
    if settings_row is None:
        settings_row = settings_row_for(request)
    if principal is None:
        principal = principal_for_request(request, settings_row=settings_row)

    # THE PANEL'S STATE COMES FROM THE PAGE IT BELONGS TO, NOT FROM THE
    # URL OF THE REQUEST RENDERING IT. The two defaults below are right
    # for exactly one caller -- the context processor, on a settings page
    # -- and both assistant views override them. See the docstring.
    if is_open is None:
        is_open = request.GET.get(OPEN_PARAM) == "1"
    here = next_url or request.get_full_path()

    # NO `enabled=True`: `visible_agents` already applies it, and this
    # tree states the convention outright -- the `enabled` term "goes
    # with the manager call it came from -- no second filter needed here"
    # (`agents/runtime/delegate.py:99-102`). `settings_row=settings_row`
    # for the same reason every other call in this function threads it:
    # without it, `visible_agents` -> `sees_all_content` re-fetches the
    # `IdentitySettings` singleton this function already has.
    agent = visible_agents(principal, settings_row=settings_row).filter(
        slug__iexact=ASSISTANT_SLUG).first()

    cards: list[dict] = []
    links: list[dict] = []
    pending = False

    # COLLAPSED COSTS ONE QUERY -- the agent row -- AND READS NO
    # CONVERSATION AND NO TURN. A `<details>` renders its children into
    # the DOM even when closed, so not rendering them is the only honest
    # way to not pay for them. Not-installed costs the same as collapsed:
    # there is no conversation to look up, and looking anyway would be a
    # query spent proving nothing exists.
    if is_open and agent is not None:
        # `**owner_fields(principal)` -- THE VIEWING PRINCIPAL'S OWN
        # THREAD, ALWAYS, REGARDLESS OF `sees_all_content` (Task 8
        # review, m2). `visible_conversations` alone answers "may this
        # principal READ this row", and with `admin_sees_content=True`
        # that is every administrator's thread, ordered newest-first
        # (`Conversation.Meta.ordering`) -- so an unfiltered `.first()`
        # would bind administrator two's panel to administrator one's
        # transcript whenever it was the more recently updated of the
        # two. That is a real leak SHAPE even though the read itself is
        # authorised (`sees_all_content` says so): the panel does not
        # offer a picker, it auto-selects, and Task 10's ask flow would
        # then POST a new turn into a conversation the composer never
        # named -- a WRITE `sees_all_content` does not authorise
        # (`may_post_to` is the separate predicate). The owner filter
        # is the exact dict `create_conversation` stamps a new thread
        # with, so this is "my own assistant conversation", never
        # "any conversation I am merely allowed to read". EXACT means
        # exact (Task 8 review, R4): an `owner_kind="service"` assistant
        # thread -- which `owned_rows_q` would admit for an administrator
        # on the READ side -- is deliberately invisible here too. Nothing
        # creates one today (`create_conversation(principal, agent)`
        # always stamps the calling principal), so this is a narrowing
        # with no current effect, not a gap.
        conversation = visible_conversations(
            principal, settings_row=settings_row).filter(
                agent__slug__iexact=ASSISTANT_SLUG, archived_at__isnull=True,
                **owner_fields(principal)).first()
        if conversation is not None:
            # ONE QUERY FOR N TURNS, INCLUDING THE TOOL TURNS -- which is
            # where the Jump-to links live, so the strip below is built
            # from rows already fetched and never costs a read of its own.
            recent = list(
                conversation.turns.order_by("-index")[:ASSISTANT_PANEL_TURNS])[::-1]
            # `fetched_routes=_fetched_routes_before(recent, i)` PER TURN
            # (Q7 tightening) -- each assistant turn's own inline links
            # only ever get the exact-case override for the routes ITS
            # OWN exchange fetched, never a sibling turn's.
            cards = [_card(turn, fetched_routes=_fetched_routes_before(recent, i))
                     for i, turn in enumerate(recent)]
            pending = any(card["pending"] for card in cards)
            links = _links(recent)

    return {"assistant": {
        "installed": agent is not None,
        "open": is_open,
        # THE SUFFIX THE SETTINGS CHROME APPENDS WHILE THE PANEL IS OPEN
        # -- `"?assistant=1"`, or `""` when it is shut. Every settings
        # sidebar link and every settings form `action` writes
        # `{{ assistant.open_query }}` and nothing else, so the parameter
        # is DECLARED ONCE, IN PYTHON (the rule the four URL keys below
        # already follow) rather than typed into forty-odd templates
        # where one typo would silently close the panel on one page.
        # Absent context -- off the settings area, or for a viewer the
        # panel does not render for -- makes it the empty string by
        # Django's own missing-variable rule, which is exactly the
        # collapsed render: byte-identical to before this existed.
        #
        # KEYED ON `is_open` ALONE, NOT ON `installed`, and that is load
        # bearing rather than an oversight (review round 1, N4 --
        # examined and deliberately not narrowed). `chat/
        # _assistant_panel.html` gates its WHOLE render on
        # `assistant.open`, and its not-installed branch is a real panel:
        # the install OFFER, a form posting `install_next`. An
        # administrator on a box with no assistant row who opens the
        # panel is therefore looking at something actionable -- and
        # narrowing this key to `is_open and agent is not None` would
        # strip the flag from every sidebar link on exactly that box, so
        # their next click would shut the offer they were being asked to
        # act on. The residual it would have closed is one dead query
        # parameter on a hand-typed URL; the cost is the install path's
        # own persistence.
        "open_query": f"?{OPEN_PARAM}=1" if is_open else "",
        "agent": agent,
        "cards": cards,
        "links": links,
        "pending": pending,
        "error": error,
        "slug": ASSISTANT_SLUG,
        "next": here,
        # THE INSTALL OFFER'S OWN `next` (Task 10 review, n2). The offer
        # renders only inside the OPEN branch now (placement-round
        # follow-up fix, below), so `here` already carries `?assistant=1`
        # by construction and `with_open_param` is idempotent on it. Left
        # in place anyway, deliberately: it is what keeps this key
        # correct even for a future caller of this same builder that
        # does not share that invariant, and it is what it always was
        # for the ask/reset redirects' own `_back` (n1) -- a guard that
        # only ever WOULD have added the param, never one this fix had to
        # touch to keep true. (Superseded reasoning, kept here rather
        # than deleted so the history reads: this key USED TO be
        # load-bearing for a native `<summary>` toggle that could expand
        # the panel client-side with no navigation and so no
        # `?assistant=1` on the URL -- see `chat/_assistant_panel.html`'s
        # own docstring for why that path no longer exists at all.)
        "install_next": with_open_param(here),
        # THE OPEN/CLOSE LINKS (placement-round follow-up, owner review
        # 2026-09-11). The reviewer's own finding: opening the panel via
        # the native `<details>` toggle revealed the COLLAPSED render's
        # zero-read variant -- an empty transcript for an administrator
        # with real history, because the context builder never reads a
        # conversation while shut (see this function's own "COLLAPSED
        # COSTS ONE QUERY" comment above). The fix removes the native
        # local toggle as a way to OPEN the panel: the closed bubble is
        # now a plain link to `open_url`, which forces a real request
        # through THIS SAME builder with `is_open=True` -- the already-
        # tested, already query-pinned leg that actually reads the
        # conversation. `close_url` is the reverse, for the open panel's
        # own explicit close control. Both are cheap string operations on
        # `here`, costing no query beyond what this function already
        # pays.
        "open_url": with_open_param(here),
        "close_url": without_open_param(here),
        # THE FOUR URL KEYS. Reversed in PYTHON, never `{% url %}` in the
        # fragment: this same context is what the panel ROUTE renders
        # from, and a template that reversed its own action would be a
        # second place the panel's URLs are decided.
        "ask_url": reverse("settings-assistant-ask"),
        "reset_url": reverse("settings-assistant-reset"),
        "panel_url": reverse("settings-assistant-panel"),
        # THE INSTALL OFFER POSTS TO THE ONE INSTALL ROUTE (spec §6.5,
        # decision 12), extended in Task 9 with a validated `next`. Three
        # lines on an existing route beat a fourth route.
        "install_url": reverse("chat-default-install"),
        # DECLARED ONCE, IN PYTHON (`agents/chat/service.py:93-95`), AND
        # HANDED TO THE TEMPLATE -- never a second copy typed into the
        # script, which is the rule `chat/conversation.html:396-407`
        # states for the poller this one is not a copy of.
        "poll_interval_ms": POLL_INTERVAL_MS,
        "max_transport_retries": MAX_TRANSPORT_RETRIES,
        "max_poll_duration_ms": MAX_POLL_DURATION_MS,
    }}


def _page_alias_regex(alias: str, *, case_sensitive: bool = False) -> re.Pattern[str]:
    """The one word-bounded, EXACT (never fuzzy) match rule this whole
    feature is built on -- shared by `_names_page` (the jump strip's own
    boolean check, `249523e`) and `_linkify_named_pages` (this round's
    inline version, below, which additionally needs the MATCH itself,
    not only whether one exists), so the two can never drift into two
    different definitions of "names this page." See `_names_page`'s own
    docstring for the accepted, deliberately-unfixed residual this one
    rule carries forward, unchanged, into both callers.

    `case_sensitive` DEFAULTS TO CASE-INSENSITIVE, unchanged from before
    this parameter existed -- every route name and every multi-word
    title still matches either way it is capitalised. Only
    `_requires_exact_case`'s own two callers ever pass `True`, below.
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", flags)


def _requires_exact_case(card, alias: str, *, fetched_routes: frozenset[str]) -> bool:
    """Q7 TIGHTENING (owner ruling, final-code resilience battery,
    2026-09-13): the drilled scenario asked "libary admin onyl??", got an
    answer entirely about LIBRARY ACCESS POSTURE (`identity-settings`,
    never `rag-settings`), and the inline linkifier still wrapped the
    word "library" in a link to `rag-settings` -- because `rag-settings`'
    own `HelpCard.title` happens to BE the single, ordinary English word
    "Library". A short, GENERIC, ONE-WORD title ("Models", "Library",
    "Chat", "Accounts", "Groups", "Entitlements") collides with prose
    about something else entirely the way a multi-word title ("Identity
    & security", "Tool access") almost never does -- nobody writes
    "identity & security" or "tool access" as a stray phrase about an
    unrelated subject.

    THE OWNER'S RULING: a one-word title links on an EXACT-CASE match --
    the identical capitalisation the registry itself uses -- or when
    THAT PAGE'S OWN CARD WAS FETCHED THIS TURN, in which case a
    lowercase, in-passing mention is no longer a coincidental word
    collision; it is the page the model is visibly, groundedly talking
    about. Multi-word titles, and every route name (never ordinary
    English), are UNCHANGED -- this predicate returns `False` for both,
    which is what keeps `_page_alias_regex`'s existing case-insensitive
    default in force for them.

    ONE PREDICATE, ONE SHARED SEAM: both callers below --
    `_linkify_named_pages` (inline) and `_named_page_links` (the jump
    strip) -- call this, so the rule can never drift into two different
    answers to "does this title need exact case here" the way two
    independent regexes could.
    """
    if alias != card.title or len(card.title.split()) != 1:
        return False
    return card.route_name not in fetched_routes


def _fetched_routes_before(turns: list, position: int) -> frozenset[str]:
    """The registry routes actually `settings.card`-fetched THIS TURN,
    for the assistant turn at `turns[position]` -- the contiguous run of
    TOOL turns immediately BEFORE it, never an older exchange's.

    WHY "CONTIGUOUS, IMMEDIATELY BEFORE" IS THE RIGHT WINDOW:
    `agents/runtime/loop.py::_finish` moves the assistant turn's own row
    to the END of the conversation once its answer is written -- "The
    placeholder was created before this turn's tool turns existed, so it
    currently sorts BEFORE them. One UPDATE moves it." -- so by the time
    a turn is `state=done`, every tool call that produced ITS OWN answer
    sits directly before it in index order, and the first non-TOOL turn
    walking backward is unambiguously the START of a different exchange.

    VALIDATED THE SAME WAY `_links` ALREADY VALIDATES THE ANCHOR LEG:
    each TOOL turn's `data["links"]` entries are `route`/`anchor`/`label`
    triples (`agents/settings_tools.py::run_card`); a route only counts
    if it is a `str` AND a member of `card_routes()` -- the identical
    guard `_links` applies against a hand-crafted or malformed row --
    which is enough to know a page's card was fetched without also
    requiring that card to have had any fields to link an anchor for.
    """
    valid = card_routes()
    routes: set[str] = set()
    i = position - 1
    while i >= 0 and turns[i].role == Turn.Role.TOOL:
        data = turns[i].data
        if isinstance(data, dict):
            for entry in data.get("links") or []:
                if isinstance(entry, dict):
                    route = entry.get("route")
                    if isinstance(route, str) and route in valid:
                        routes.add(route)
        i -= 1
    return frozenset(routes)


def _linkify_named_pages(html: str, *, fetched_routes: frozenset[str] = frozenset()) -> str:
    """Wrap the FIRST occurrence of each registry page's own exact
    title/route-name spelling found in `html` in a whitelist-built link
    to that page (owner feedback, live, screenshot-verified, 2026-09-13
    -- the second finding on the same scenario `249523e` closed for the
    jump strip). The owner's own words: the answer "speaks in internal
    route names" ("chat-tool-entitlements", "identity-settings
    (identity-settings)") and its only links sit in a separate strip
    below -- the page names already IN the sentence should be the
    clickable part. This wraps those same words where they already sit,
    rather than only summarising them underneath.

    `fetched_routes` IS `_fetched_routes_before`'s OWN RESULT for the
    turn this HTML was rendered from -- the routes this exchange's own
    `settings.card` calls actually fetched. Threaded straight into
    `_requires_exact_case` (Q7 tightening, see that function's own
    docstring): a one-word title only matches case-insensitively when
    its route is in this set, never merely because the answer said the
    word somewhere.

    `html` IS ALREADY `render_answer`'s OWN OUTPUT -- escaped first,
    minimal markdown second (`agents/chat/rendering.py::_inline`'s own
    contract) -- and that ordering is exactly why running THIS function
    AFTER it is safe: every `<`/`>`/`&` in the model's own words is
    already an entity by the time this function ever sees the string,
    so nothing this function's regex can match was ever live markup, and
    nothing it inserts is built from anything the model wrote -- only
    from `reverse(HelpCard.route_name)`, the identical table-sourced
    source every other link on this surface already uses. Matched
    against `escape(card.title)`/`escape(card.route_name)`, never the
    raw spelling: the text being searched is itself already escaped, so
    searching for the raw `"Identity & security"` against text that
    spells it `"Identity &amp; security"` would simply never match.

    THE MATCHED TEXT IS THE LINK'S OWN VISIBLE WORDS, UNCHANGED. This
    function never rewrites `"chat-tool-entitlements"` to `"Tool
    access"` -- it wraps exactly what the model wrote in an anchor to
    the right page. The prompt half of this same fix (`agents/
    defaults.py`) is what stops the model writing the ugly form in the
    first place; this half's only job is to make whatever it does write
    clickable.

    FIRST OCCURRENCE PER PAGE, NEVER PER ALIAS: for each card, only the
    EARLIER of its title's and its route name's first occurrence is
    even considered a candidate, so an answer that names the same page
    three times, by either spelling, gets one link, not three.

    NON-OVERLAPPING BY CONSTRUCTION, LONGEST-MATCH-WINS ON A TIE: every
    card's one candidate span is computed against the SAME original
    string, sorted by start position (ties broken toward the LONGER
    span), and a candidate is dropped if it starts before a
    previously-claimed span ends. This is what keeps a short title that
    happens to be a hyphen-delimited token INSIDE a longer, unrelated
    route name -- `"Chat"` inside `"chat-tool-entitlements"` -- from
    ever winning over the longer, more specific match that also starts
    there: the two never both survive, and when they collide the longer
    one does. (`_names_page`'s own accepted residual is a DIFFERENT
    shape -- a compound merely CONTAINING a full route name, with no
    competing shorter match at the same position -- and is untouched by
    this.) Two aliases can therefore never produce two overlapping or
    nested `<a>` tags.

    RETURNS A NEW `SafeString`, NEVER THE BARE RESULT OF STRING
    CONCATENATION: every piece spliced in here is either a verbatim
    slice of the already-safe input or an `<a href=...>`/`</a>` this
    function built itself from a reversed, escaped, table-sourced URL --
    genuinely safe HTML -- but Django's autoescape has no way to know
    that from a plain `str`, and would re-escape the very anchor tags
    this function just wrote. `mark_safe` says so once, here, the same
    place `render_answer` itself says so for its own output.
    """
    if not html:
        return html

    candidates: list[tuple[int, int, str]] = []
    for card in CARDS:
        best = None
        for alias in (card.title, card.route_name):
            exact = _requires_exact_case(card, alias, fetched_routes=fetched_routes)
            match = _page_alias_regex(escape(alias), case_sensitive=exact).search(html)
            if match is not None and (best is None or match.start() < best.start()):
                best = match
        if best is not None:
            candidates.append((best.start(), best.end(), card.route_name))

    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))

    selected: list[tuple[int, int, str]] = []
    claimed_until = -1
    for start, end, route_name in candidates:
        if start < claimed_until:
            continue
        selected.append((start, end, route_name))
        claimed_until = end

    if not selected:
        return html

    selected.sort(key=lambda c: c[0])

    pieces: list[str] = []
    cursor = 0
    for start, end, route_name in selected:
        try:
            url = reverse(route_name)
        except NoReverseMatch:
            # A card whose page is behind a `FARABUNKER_FEATURES` token
            # this box has off -- the identical guard `_links`/
            # `_named_page_links` apply on their own two legs. The span
            # is left exactly as it was: unlinkified, not dropped.
            continue
        pieces.append(html[cursor:start])
        pieces.append(f'<a href="{escape(url)}?{OPEN_PARAM}=1">')
        pieces.append(html[start:end])
        pieces.append("</a>")
        cursor = end
    pieces.append(html[cursor:])
    return mark_safe("".join(pieces))


def _card(turn, *, fetched_routes: frozenset[str] = frozenset()) -> dict:
    """One turn, in the four states this surface has (spec §6.4).

    `fetched_routes` -- THIS TURN'S OWN `_fetched_routes_before` RESULT,
    handed straight to `_linkify_named_pages` (Q7 tightening, see
    `_requires_exact_case`'s docstring). A turn nobody ever calls this
    for (the assistant slot in a UI test, say) simply gets the empty
    set, the same as before this parameter existed -- exact-case titles
    still link, generic lowercase ones do not.

    ITS OWN COMPACT VIEW MODEL, NOT `agents.chat.rendering.turn_card`'s:
    that one carries tool cards with arguments, thumbnails and citations,
    artifact image and file strips, attachment rows and five turn states,
    and a settings panel needs none of it. Reusing the card MARKUP would
    drag roughly twenty `.turn*`/`.tool*` selectors out of
    `chat/base.html` and into the global shell so they could render on a
    page that then hides most of them.

    WHAT IT DOES REUSE IS THE THING THAT MATTERS: `render_answer`, so a
    model's `**bold**`, lists and code spans render identically here and
    in `/chat/`, ESCAPED FIRST -- and, as there, `render_answer` ITSELF
    emits no `<a>` from a URL or otherwise.

    `_linkify_named_pages` RUNS ON TOP, HERE, NOT INSIDE
    `render_answer` (owner feedback, live, 2026-09-13): the ANCHOR is
    this surface's own addition -- a settings page's title or route name
    means something on THIS panel that it does not mean in an ordinary
    `/chat/` conversation, and `render_answer` is shared by both. So the
    one place `text_html` gains an `<a>` at all is here, scoped to the
    settings assistant's own card, never inside the shared renderer
    every other surface also calls.
    """
    return {
        "role": turn.role,
        "state": turn.state,
        "text": turn.text,
        "text_html": (_linkify_named_pages(render_answer(turn.text),
                                          fetched_routes=fetched_routes)
                      if turn.role == Turn.Role.ASSISTANT else None),
        "error": turn.error,
        "pending": turn.state in (Turn.State.QUEUED, Turn.State.RUNNING),
    }


def _names_page(text: str, alias: str, *, case_sensitive: bool = False) -> bool:
    """Does `text` contain `alias` as a whole, word-bounded phrase? EXACT,
    NOT FUZZY (placement-round follow-up, owner-found defect, 2026-09-
    12): a literal occurrence of the registry's own spelling --
    `HelpCard.title` or `HelpCard.route_name`, never a typo-tolerant or
    synonym match that could point at the wrong page. Word-bounded so
    `"Chat"` does not fire inside some unrelated longer word, and
    `re.escape`d so `"Identity & security"`'s own `&` is read literally
    rather than as a regex operator. THE PATTERN ITSELF now lives in
    `_page_alias_regex`, shared with `_linkify_named_pages` below, so
    this function's own behaviour is unchanged -- only where the regex
    is built moved.

    CASE-INSENSITIVE BY DEFAULT, UNCHANGED -- `case_sensitive` is Q7's
    tightening (`_requires_exact_case`), which only ever passes `True`
    for a short, generic one-word title; every other caller of this
    function keeps the original, unqualified behaviour.

    KNOWN, ACCEPTED RESIDUAL (owner ruling, 2026-09-12): `\\w` does not
    include `-`, so a hyphenated compound that merely CONTAINS a route
    name as a substring -- `"pre-identity-settings-page"` around
    `"identity-settings"` -- still reads as word-bounded and matches.
    Left as-is, deliberately: the worst case this can produce is an
    EXTRA link to a page the text did genuinely mention the name of,
    never a link to the WRONG page, and narrowing this regex further
    (requiring a non-hyphen boundary too) buys no safety a working
    whitelist matcher does not already have, at the cost of a harder
    rule to read.
    """
    return _page_alias_regex(alias, case_sensitive=case_sensitive).search(text) is not None


def _named_page_links(text: str, *, exclude_routes: frozenset[str]) -> list[dict]:
    """One page-level link (`HelpCard.route_name` -> `HelpCard.title`) for
    every registry card the FINAL ANSWER TEXT names, skipping any route
    already covered by an anchor link (`exclude_routes`) -- the owner-
    found defect this closes (2026-09-12): a turn whose answer named
    "Identity & security" showed a jump strip of `rag-settings` anchors
    only, because the model's own `settings.card` calls never reached the
    second page. `_links`, below, is the model-independent fix: the
    platform adds the missing page link itself, off the answer's own
    text, regardless of which cards the model happened to fetch.

    Q7 TIGHTENING, AND WHY THIS LEG NEEDS NO SEPARATE `fetched_routes`
    PARAMETER (`_requires_exact_case`'s docstring has the full ruling):
    the "OR fetched this turn" half of that rule is ALREADY TRUE here by
    construction -- any route the model fetched this turn that earned an
    anchor link is in `exclude_routes`, and this loop's very first check
    (`continue` below) skips it before the title/route-name match ever
    runs. So every card that reaches the match this turn is, by
    definition, one whose card was NOT fetched this turn, and a one-word
    title among them requires the registry's own exact capitalisation,
    unconditionally.

    STILL THE SAME WHITELIST, NOT A NEW DOOR (spec §4.3, restated):
    matched ONLY against `foundation.settings_help.CARDS` -- the
    platform's own table, pure and model-blind -- never against anything
    the model wrote. A name with no card match, real-sounding or not,
    produces nothing. `reverse(route)` is guarded against `NoReverseMatch`
    for the identical reason `_links` below guards it on the anchor leg:
    `vision-engine-files` is a real card with no mounted route while the
    `vision` feature is off.

    PAGE-LEVEL, NEVER AN ANCHOR: this leg has no field to point at, only
    a page the answer named -- `HelpField.anchor` information the model
    never supplied and this function has no business inventing. A later
    turn that actually calls `settings.card` for the same page earns the
    sharper anchor link instead, via `exclude_routes`.

    CARDS' OWN TABLE ORDER, not the order names first appear in the
    text: a stable, predictable strip beats one that reorders itself
    based on prose the model is free to restructure turn to turn.
    """
    found: list[dict] = []
    for card in CARDS:
        if card.route_name in exclude_routes:
            continue
        # `fetched_routes=frozenset()`: unconditionally exact-case for a
        # one-word title here, per the docstring above -- any route
        # actually fetched this turn already took the `continue` branch.
        title_exact = _requires_exact_case(card, card.title, fetched_routes=frozenset())
        if not (_names_page(text, card.title, case_sensitive=title_exact)
                or _names_page(text, card.route_name)):
            continue
        try:
            url = reverse(card.route_name)
        except NoReverseMatch:
            continue
        found.append({"url": f"{url}?{OPEN_PARAM}=1", "label": card.title})
    return found


def _final_answer_text(turns) -> str:
    """The most recent ASSISTANT turn's raw `text` in this window, or
    `""`. Raw, not `card["text_html"]`: matching against rendered HTML
    would have to see `&amp;` where `HelpCard.title` spells `&`, and
    `render_answer` is markdown-to-HTML, not plain-text normalisation --
    a second parse this function has no reason to pay for when the row
    it wants is sitting right there. A pending or failed turn's `text` is
    empty by this codebase's own convention, so neither produces a false
    match."""
    for turn in reversed(turns):
        if turn.role == Turn.Role.ASSISTANT:
            return turn.text or ""
    return ""


def _links(turns) -> list[dict]:
    """The "Jump to" strip: anchor links from the MOST RECENT TOOL
    TURN's own `data["links"]`, THEN page-level links for any registry
    page the final answer NAMES but never fetched this turn (placement-
    round follow-up, owner-found defect, 2026-09-12 -- see
    `_named_page_links`'s own docstring for the defect and the fix).
    Both legs are whitelisted by construction (spec §4.3).

    THE MODEL NEVER PRODUCES A LINK. `render_answer` escapes first and
    emits no `<a>` from a URL or otherwise, which is a deliberate posture
    -- an `<a href>` a model can be talked into writing is a phishing
    primitive -- and this does not weaken it. So every link here is the
    PLATFORM's, built from the platform's own table: every anchor entry
    whose `route` is not in `card_routes()`, or whose `anchor` is not one
    of THAT CARD's own `HelpField.anchor` values, is dropped before
    anything is reversed; every named-page entry is matched only against
    `foundation.settings_help.CARDS`' own `title`/`route_name` spellings,
    never against the model's own prose otherwise.

    Nothing that is not already in the code-side table can become a link
    on a settings page. That is the property that matters most here,
    because a link is the one thing on this surface an operator will
    click.

    `isinstance(route, str)` / `isinstance(anchor, str)` GUARD EACH
    MEMBERSHIP TEST (Task 10 review, m1). `routes` and the per-card
    anchor set are both plain `set`/`frozenset` objects, and `x not in a_
    set` raises `TypeError` when `x` is unhashable -- a stored `route` or
    `anchor` that is a `list` or `dict` would otherwise 500 every one of
    the settings pages for the administrator who owns that row,
    until it is deleted. Nothing that ships today writes such a value
    (`settings.card` builds the triples in Python; no route creates or
    edits an agent's `tool_keys`), so this is a robustness guard against
    a future or hand-crafted row, not a live hole -- but this module's
    own malformed-data test already claims exactly this property, and a
    guard the test does not exercise is a claim, not a fact.

    ONLY THE MOST RECENT TOOL TURN IS EVER CONSULTED FOR ANCHOR LINKS
    (Task 10 review, n3): the loop `break`s on the first TOOL turn with
    dict `data`, keeping whatever it built -- including nothing --
    rather than falling back to an older tool turn. It used to `return`
    on that same condition; it now `break`s because the named-page leg
    still has to run afterward, but the no-fallback behaviour this
    guards is unchanged: a NEWER all-invalid payload never lets an OLDER
    exchange's anchor links survive under a newer answer -- stale, not a
    whitelist breach, but still a strip that belonged to a different
    question than the one above it.

    `reverse(route)` IS GUARDED AGAINST A ROUTE THIS BOX DOES NOT MOUNT
    (final review, C1; the same guard now covers the named-page leg too,
    in `_named_page_links`). `settings_help.py`'s `CARDS` is a PURE LEAF
    -- `foundation/settings_area.py:69-77`'s own docstring names the
    reason it cannot consult `settings.FARABUNKER_FEATURES` -- so
    `card_routes()` always contains `vision-engine-files` even on a box
    that has `vision` off, `config/urls.py:45-46` mounts `/vision/` only
    while the flag is on, and `settings.card` legitimately offers Engine
    files as a page name regardless (it is a description, not a link).
    Without this guard, a tool turn naming that page raises
    `NoReverseMatch` OUT OF THIS CONTEXT PROCESSOR -- a 500 on every one
    of the settings pages, and on the assistant's own ask/panel
    routes, for as long as that turn sits inside the six-turn window.
    The residual this guard accepts is named in ADR 0018's gaps: with
    `vision` off, the assistant still *describes* Engine files (the card
    exists); it simply never LINKS to it, because there is nowhere to
    link.

    CAPPED AT `MAX_JUMP_LINKS`, COMBINED (not per leg): anchor links are
    already small by construction (bounded by one page's own field
    count); the named-page leg is bounded only by how many of the
    registry's titles/route-names the final answer happens to name,
    which a broad or hostile-but-still-whitelisted answer could in
    principle make all of them. A cap keeps this a short wayfinding aid,
    never an index of the box.
    """
    routes = card_routes()
    fetched: list[dict] = []
    fetched_routes: set[str] = set()
    for turn in reversed(turns):
        if turn.role != Turn.Role.TOOL or not isinstance(turn.data, dict):
            continue
        raw = turn.data.get("links") or []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            route, anchor = entry.get("route"), entry.get("anchor")
            if not isinstance(route, str) or route not in routes:
                continue
            card = card_for(route)
            if card is None or not isinstance(anchor, str) or \
                    anchor not in {f.anchor for f in card.fields}:
                continue
            try:
                url = reverse(route)
            except NoReverseMatch:
                # A card whose page is behind a `FARABUNKER_FEATURES`
                # token this box has off (`vision-engine-files`,
                # `config/urls.py:45-46`). The card still describes it;
                # the platform will not link somewhere that does not
                # exist.
                continue
            label = next(f.name for f in card.fields if f.anchor == anchor)
            fetched.append({
                "url": f"{url}?{OPEN_PARAM}=1#{anchor}",
                "label": label,
            })
            fetched_routes.add(route)
        break

    named = _named_page_links(
        _final_answer_text(turns), exclude_routes=frozenset(fetched_routes))
    return (fetched + named)[:MAX_JUMP_LINKS]
