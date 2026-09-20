"""The settings assistant panel's three views (spec §6.6, §8.1).

ALL THREE ARE CLASS S (`identity/routes.py::ROUTE_RULES`), so the
middleware has already refused a non-admin before any of them runs -- the
same reasoning `chat-settings` records for itself: "a page whose entire
body is an administrator-only form has nothing to show anybody else."
Each of them still resolves the acting principal, because the turn runs
AS THE ASKING ADMINISTRATOR and `start_turn`'s `actor` is required and
keyword-only precisely so nobody enqueues a turn attributed to nobody.

EACH VIEW ALSO CARRIES ITS OWN `is_admin` CHECK (Task 8 review, m1 --
binding Task 10 amendment 3), IN ADDITION TO the `ROUTE_RULES["S"]`
middleware gate above. `panel_context` -- which all three call, either
directly or through `_fragment` -- carries NO gate of its own; the only
existing caller that gates before calling it is the CONTEXT PROCESSOR
(`agents.chat.context_processors.settings_assistant`), and these views
are not it. So the admin check here is not decorative belt-and-braces
for its own sake: it is what keeps "a principal the panel will not
render for reads no row" true on THESE THREE ROUTES even if a future
change ever calls one of them from somewhere the middleware does not
run in front of (a management command, a test that renders the view
function directly, a URLconf that mounts it a second time under a
different name). It costs nothing beyond what the request already
pays: `identity.access.is_admin` is called with the SAME `settings_row`
instance `panel_context`'s own `visible_agents`/`visible_conversations`
calls thread onward, and `identity.access._user_row` memoises the
`auth_user` read on that instance -- so the FIRST `is_admin` call (here)
pays for the `auth_user` row, and every later call on the same request
reuses it for free.

THEY LIVE IN `agents/chat` because they read `agents/` rows, and they
read them through `agents/visibility.py`, never through a manager. They
are MOUNTED by `config/urls.py` at `/settings/assistant/`, beside
`/settings/` itself: the composition root is the one module that already
imports every column, so nothing crosses a boundary to put a settings-
shaped URL in front of an agents-column view.

EVERYTHING WORKS WITH NO SCRIPT AT ALL: the form is a plain POST, the
answer is a redirect carrying `?assistant=1`, the panel comes back open
with the queued turn showing "Thinking...", and a reload shows the
answer. That is the same contract `chat/conversation.html:6-9` makes.

NO `?pending=<turn_id>`, deliberately (spec §6.6 step 5, decision 9). On
the chat page that parameter is the hand-off telling a JS-enabled reload
which turn the script-free POST just queued; this panel re-renders its
WHOLE recent transcript on every render, so the queued turn is already in
front of the operator and the pending marker is derived from the rows.
Carrying a turn id nothing reads would be cargo.
"""
from __future__ import annotations

from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from agents.chat.context_processors import ASSISTANT_SLUG, panel_context, with_open_param
from agents.chat.service import start_turn, validated_next_url
from agents.visibility import (
    create_conversation, set_conversation_archived, visible_agents, visible_conversations,
)
from identity.access import is_admin, owner_fields
from identity.request import principal_for_request, settings_row_for

_FRAGMENT = "chat/_assistant_panel.html"

_NOT_INSTALLED = (
    "The settings assistant is not installed on this box yet. Open any settings page and "
    "use the panel's install button to add it."
)

_ADMIN_ONLY = "Administrators only."


def _is_xhr(request) -> bool:
    """True for the panel's own `fetch()` calls.

    COPIED, NOT IMPORTED, from `agents/chat/views/turns.py:44-50`, which
    itself copied it from `tools/vision/views.py` and says why in those
    words: it is a five-line reading of one header, and this package's
    import direction is one-way -- `views/assistant.py` importing
    `views/turns.py` would be a new edge for five lines.
    """
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _here(request) -> str:
    """THE SETTINGS PAGE THIS PANEL BELONGS TO -- ordinarily a page other
    than these three routes' own URLs, since every SHIPPED caller (the
    composer's hidden `next`, the poll fragment's `data-assistant-next`)
    is handed a settings page's URL, never one of its own.

    THAT IS A FACT ABOUT WHO CALLS THIS, NOT A GUARANTEE THIS FUNCTION
    ENFORCES (Task 10 review, a2 -- an earlier version of this docstring
    claimed the stronger, false thing). The GET leg's
    `url_has_allowed_host_and_scheme` check answers only "is this
    same-host", the identical question the POST leg's
    `validated_next_url` answers -- neither special-cases these three
    routes' own paths, so a hand-crafted `?next=/settings/assistant/
    panel/` is accepted like any other same-host path would be. No
    probe made of it does anything: all nine hostile payloads (absolute,
    scheme-relative, backslash, `javascript:`, `data:`, userinfo,
    leading whitespace) are refused on all three routes regardless.

    The composer POSTs it as `next`; the poll route receives it as
    `?next=`. The POST half goes through `validated_next_url` -- the ONE
    `next` reader this codebase has (`agents/chat/service.py:683`) --
    because a caller-supplied value is otherwise an open redirect off
    this box; the GET half applies the identical
    `url_has_allowed_host_and_scheme` guard rather than trusting a query
    string that guard has never seen.

    `settings-index` when there is no usable value: that route reads the
    posture row and redirects to whichever settings page this viewer may
    actually open, so it is a fallback that lands somewhere real in
    every posture.
    """
    posted = validated_next_url(request)
    if posted:
        return posted
    queried = request.GET.get("next", "")
    if queried and url_has_allowed_host_and_scheme(
            queried, allowed_hosts={request.get_host()},
            require_https=request.is_secure()):
        return queried
    return reverse("settings-index")


def _back(request) -> str:
    """Where a non-XHR POST redirects to, always carrying the panel's
    open state -- so the operator lands on the page they asked from with
    the panel still showing their question.

    `with_open_param`, NOT A BARE `?assistant=1` APPEND (Task 10 review,
    n1): `_here`'s own value can already carry the parameter -- the
    inline panel's own `next` is `request.get_full_path()`, so asking a
    second script-free question re-poses the FIRST redirect's own URL,
    which already had it. Appending unconditionally measured `[1, 2, 3]`
    copies of `assistant=1` in the address bar across three such asks;
    harmless (`request.GET.get` reads the first), but the same
    "query string runs away" shape this surface elsewhere guards
    against.
    """
    return with_open_param(_here(request))


def _fragment(request, *, error: str = ""):
    """The panel, rendered from the SAME builder the inline panel uses.

    `is_open=True` UNCONDITIONALLY, because a fragment is only ever
    rendered BECAUSE the panel is open -- the XHR ask and the poll are
    both things that happen inside an open panel, and neither request
    carries the `?assistant=1` the inline render reads. Without this the
    fragment comes back collapsed, with no transcript and no pending
    marker, and the poller stops on its first tick.
    """
    return render(request, _FRAGMENT,
                  panel_context(request, error=error, is_open=True,
                                next_url=_here(request)))


@require_POST
def assistant_ask(request):
    """POST /settings/assistant/ask/ -- one question to the settings
    assistant.

    Resolve or CREATE this principal's own conversation. On an
    accounts-on box that is one conversation per administrator, owned, and
    invisible to every other principal by the ordinary gate.

    ON AN OPEN BOX IT IS ONE CONVERSATION FOR THE WHOLE BOX,
    DELIBERATELY, AND THAT IS WORTH KNOWING: `visible_conversations`
    short-circuits on `sees_all_content` and returns `.all()`, and every
    viewer there is the same `OPEN_PRINCIPAL` -- so a household box has
    one assistant thread that everyone at the keyboard continues, reads
    the history of, and can "start over" for all of them. That is the
    same thing being true of `is_admin` that the settings sidebar already
    is, followed through to the transcript.

    `create_conversation(principal, agent)` stamps `owner_fields` and NO
    `workstream`. That NULL is load-bearing: it is what keeps an
    assistant conversation off every workstream card, and
    `agents/chat/tests/test_settings_surface.py` pins it from the other
    side.

    THE CONVERSATION LOOKUP IS OWNER-FILTERED, UNCONDITIONALLY (Task 8
    review, R1 -- binding Task 10 amendment 1): `**owner_fields(principal)`
    on the SAME filter `panel_context`'s own read-side lookup carries
    (Task 8 review, m2), with NO `sees_all_content` branch. Without it,
    `admin_sees_content=True` would let `visible_conversations(...)
    .filter(...).first()` resolve to WHATEVER administrator's thread was
    most recently updated -- `Conversation.Meta.ordering =
    ["-updated_at"]` -- and this view would then POST the asking
    administrator's turn into a conversation they do not own.
    `sees_all_content` authorises READING somebody else's thread; it does
    not authorise writing into it, and `may_post_to` is the separate
    predicate that would have to say so for that to be sound. This
    filter is what keeps "my own assistant conversation, always" true
    regardless of that setting.
    """
    # THE ROW FIRST, THEN THE PRINCIPAL OFF IT.
    # `principal_for_request` reads the singleton ITSELF through
    # `accounts_on()` when it is not handed one (`identity/request.py:91`),
    # and the gate middleware has already stashed the row -- so calling
    # them in this order, with the keyword, is what keeps this view to the
    # one-read-per-request rule the whole panel is budgeted against.
    # `foundation/setup/views.py:185-187` is the in-tree precedent.
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)

    # THIS VIEW'S OWN CLASS-S GATE (Task 8 review, m1; binding Task 10
    # amendment 3) -- see this module's own docstring for why it exists
    # despite the middleware already having refused a non-admin, and for
    # why it costs no second `auth_user` read.
    if not is_admin(principal, settings_row=settings_row):
        return HttpResponseForbidden(_ADMIN_ONLY)

    # NO `enabled=True` HERE: `visible_agents` already filters it, and
    # the term "goes with the manager call it came from -- no second
    # filter needed here" (`agents/runtime/delegate.py:99-102`, in those
    # words). `settings_row=settings_row` -- binding Task 10 amendment 2
    # / Task 8 review R3 -- threads the SAME row `panel_context` itself
    # threads, so `visible_agents`'s own `owned_rows_q`/`label_permitted_q`
    # legs do not re-read the `IdentitySettings` singleton a second time
    # in a view whose own comment above claims the one-read rule.
    agent = visible_agents(principal, settings_row=settings_row).filter(
        slug__iexact=ASSISTANT_SLUG).first()
    if agent is None:
        # ITEM 6 (Coherence Wave D): dual-mode, matching the `start.ok`
        # refusal a few lines below on this SAME view rather than a flat
        # flash-and-redirect -- this route already has a documented
        # script-optional contract ("EVERYTHING WORKS WITH NO SCRIPT AT
        # ALL", this module's own docstring), and its sibling refusal
        # already branches on `_is_xhr`: a fragment for the panel's own
        # `fetch()`, a redirect for the plain-POST caller. This raw 400
        # was the one branch on this view that never checked, which is
        # the actual defect -- not the status code the audit expected to
        # find "designed" here.
        if _is_xhr(request):
            return _fragment(request, error=_NOT_INSTALLED)
        return redirect(_back(request))

    conversation = visible_conversations(principal, settings_row=settings_row).filter(
        agent__slug__iexact=ASSISTANT_SLUG, archived_at__isnull=True,
        **owner_fields(principal)).first()
    if conversation is None:
        conversation = create_conversation(principal, agent)

    # NO `connection`, NO `files`, NO `placement`: this surface has no
    # picker and no attach door, so passing any of them would be the view
    # inventing a capability its own template does not offer.
    start = start_turn(conversation, request.POST.get("text", ""), actor=principal)

    if not start.ok:
        # A refusal from `start_turn` -- blank text, a turn already in
        # flight, the queue down -- lands in the panel's OWN error slot.
        # NEVER a bare fragment for a non-XHR caller: the rule
        # `agents/chat/views/turns.py:64-67` states, applied here.
        if _is_xhr(request):
            return _fragment(request, error=start.error)
        return redirect(_back(request))

    if _is_xhr(request):
        return _fragment(request)
    return redirect(_back(request))


@require_POST
def assistant_reset(request):
    """POST /settings/assistant/reset/ -- "Start over".

    NOTHING IS DELETED: the old thread is ARCHIVED, and the chat-surface
    exclusion keeps it out of the archive listing too. The next ask
    creates a fresh one.

    THE SAME OWNER-FILTERED LOOKUP `assistant_ask` USES, for the
    identical reason (Task 8 review R1, binding Task 10 amendment 1):
    without `**owner_fields(principal)`, `admin_sees_content=True` would
    let this view ARCHIVE whichever administrator's thread
    `visible_conversations(...).first()` happened to resolve to --
    `may_manage_conversation`, not `sees_all_content`, is the predicate
    that would have to authorise that, and this view asks neither; it
    only ever touches a conversation this principal itself owns.
    """
    # The row first, then the principal off it -- `assistant_ask`'s own
    # comment says why, and this view pays the same avoidable query
    # without it.
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)

    # THIS VIEW'S OWN CLASS-S GATE -- see `assistant_ask`'s docstring.
    if not is_admin(principal, settings_row=settings_row):
        return HttpResponseForbidden(_ADMIN_ONLY)

    conversation = visible_conversations(principal, settings_row=settings_row).filter(
        agent__slug__iexact=ASSISTANT_SLUG, archived_at__isnull=True,
        **owner_fields(principal)).first()
    if conversation is not None:
        set_conversation_archived(principal, conversation, archived=True)
    return redirect(_back(request))


@require_GET
def assistant_panel(request):
    """GET /settings/assistant/panel/ -- the same fragment the inline
    panel renders, from the same template and the same context builder.

    THE `chat/_turn_block.html` PRECEDENT, where "this page's inline
    render and `turn_status`'s poll body come out of the SAME loop over
    the SAME fragment".

    It takes `?next=` -- read and host-checked by `_here` -- so the
    fragment it returns carries the settings page the panel belongs to,
    rather than this route's own URL. IT WRITES NOTHING.
    """
    # THIS VIEW'S OWN CLASS-S GATE -- see `assistant_ask`'s docstring.
    # `settings_row_for`/`principal_for_request` here cost nothing extra:
    # `_fragment` -> `panel_context` resolves the SAME cached
    # `request.identity_settings_row` instance and the SAME `request.user`
    # attribute a second time, and `is_admin`'s own `auth_user` read is
    # memoised on that shared instance -- so this check pays the read
    # panel_context's own `visible_agents` call would have paid anyway.
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    if not is_admin(principal, settings_row=settings_row):
        return HttpResponseForbidden(_ADMIN_ONLY)
    return _fragment(request)
