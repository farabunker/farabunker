"""The conversation list and its lifecycle."""
from __future__ import annotations

import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from agents.chat.pickers import chat_picker_options
from agents.chat.sidebar import sidebar_context
from identity.request import principal_for_request, settings_row_for
# THE ONE thread-URL builder, the HTTP-free turn starter, and the
# `next`-field validator. All three live in `service.py` rather than
# here because `turns.py` needs them too and must not import this
# module -- the package's import direction is one-way
# (`views/__init__.py`). `composer_attach_context`/`flash_attachment_
# outcomes` are round 18's own additions to that same shared leaf --
# `_index_context` needs the first (the start box's own attach door
# now gates through it), `conversation_start` the second (a start that
# carries files flashes the identical outcomes a later turn's own
# upload does).
from agents.chat.service import (
    composer_attach_context, composer_attachment_fields, conversation_url, fit_title,
    flash_attachment_outcomes, start_turn, validated_next_url,
    visible_conversation_or_404,
)
# NO `from agents.models import ...` HERE (ruling 4c). Every row this
# module reads comes through `agents/visibility.py`, and
# `foundation/ops/tests/test_column_boundaries.py` fails the build if a
# module under `agents/chat` reaches `Conversation`/`Agent`/`Flow`
# `.objects` directly. In open mode those functions return everything,
# so nothing today depends on this -- which is exactly why it needs a
# guard rather than a convention.
from agents.defaults import SETTINGS_SURFACE_SLUGS, missing_defaults
from agents.visibility import (
    chat_surface_agents, create_conversation, delete_conversation, duplicate_conversation,
    installed_agent_slugs, rename_conversation, set_conversation_archived,
    set_conversation_pinned, visible_workstreams,
)
from agents.workstreams import stream_access

logger = logging.getLogger(__name__)

_BLANK_TITLE = "A conversation needs a title. Type one, or leave the name it has."

# Lower-cased once, read at both comparison sites below (the offers
# list and the `nothing_installed` banner). CASE-INSENSITIVE (Task 6
# review, Finding 4): both sites used to compare an exact
# `SETTINGS_SURFACE_SLUGS` membership against a raw installed slug or
# catalogue slug, while slug identity is case-insensitive everywhere
# else this platform tests it (`Agent`'s own `uniq_agent_slug_ci`,
# `install_default`'s `slug__iexact`, `missing_defaults`'s own
# `.lower()`). Matches `missing_defaults`'s idiom exactly: lower-case
# the comparison, not the constant.
_SETTINGS_SURFACE_SLUGS_CI = frozenset(slug.lower() for slug in SETTINGS_SURFACE_SLUGS)

_NOTHING_INSTALLED = (
    "No agents are installed on this box yet. Add one of the defaults below, or "
    "run `manage.py install_defaults`."
)

# Distinct from `_NOTHING_INSTALLED`: rows exist, so there is nothing to
# OFFER (`offers` is computed from every installed slug, enabled or
# not -- review fix wave item 2), but every one of them is `enabled=
# False`, so the picker is just as empty as it would be with zero rows.
# Naming the CLI's `--reset` rather than a bare "enable one" is
# deliberate: `install_default(..., reset=True)` is the one path that
# re-enables a disabled row (`agents/defaults.py`'s own docstring), so
# this is the true, actionable next step -- not a placeholder.
_ALL_INSTALLED_DISABLED = (
    "Every installed agent is disabled. Enable one, or run `manage.py "
    "install_defaults --reset <slug>`."
)


class ChatIndexView(TemplateView):
    """GET /chat/ -- the conversation sidebar, the agents this principal
    can talk to, and the shipped defaults they have not installed yet.

    UI-3b RESHAPED THIS PAGE AND REMOVED A LIST. `/chat/` used to render
    the conversation list in its main pane; the list is now the SIDEBAR,
    rendered on this page and on every thread page alike, so clicking
    between conversations never goes back through a landing screen. What
    is left in the pane is the start composer -- the one thing this page
    does that a thread page does not -- plus the offers and the empty
    states. The rows are the same rows, read through the same
    `visible_conversations`; only where they render changed.

    `?archived=1` REDIRECTS TO `/chat/all/?archived=1`, round 14, Part
    2's own absorption (owner feedback: "i also don't see a link to the
    table list of all the chats"). Through round 13 this query string
    SWITCHED THE SIDEBAR TO THE ARCHIVE, on this same page -- the only
    way to see an archived conversation at all, and neither searchable
    nor filterable. `/chat/all/` is now that page (`AllConversationsView`,
    `agents/chat/views/all_conversations.py`), so this bare `GET` no
    longer serves the archive itself; it forwards there, preserving
    every OTHER query parameter the request carried. Every other query
    string (a bare `/chat/`, or one with `?connection=`) is UNTOUCHED --
    the redirect is one `if` at the top of `get()`, not a rewrite of
    this view.

    A GET INSTALLS NOTHING (ruling 2). A box with no rows renders an
    honest empty state naming what the platform offers, each with an
    "Add the default X" button -- it does not quietly create three
    agents because somebody opened a page. P2's `manage.py sync_agents`
    did create them, on every deploy that ran it, and that is what this
    ruling retires.

    TWO LISTS, AND THE DISTINCTION IS THE PRODUCT. `agents` are rows
    the operator owns and can start a conversation with; `offers` are
    shipped defaults not yet installed. Merging them would mean either
    hiding what the platform offers or pretending an offer is something
    you can talk to. They are disjoint by construction --
    `missing_defaults` is computed FROM the installed slugs.

    Every row is read through `agents/visibility.py` (ruling 4c). In
    open mode `visible_conversations` returns them all, which is the
    honest pre-auth behaviour: there are no users on this box (spec
    section 14 gap 4), so there is nobody for a conversation to be
    hidden from. WHEN IDENTITY & AUTH LANDS, THAT FUNCTION IS WHERE
    THE FILTER GOES -- not a migration, not a second listing view, not
    this class.

    Never 500s. A registry read that fails costs the operator the model
    picker, never the list of what they have already said.
    """

    template_name = "chat/index.html"

    def get(self, request, *args, **kwargs):
        if request.GET.get("archived") == "1":
            # `request.GET` ALREADY carries `archived=1` (that is this
            # branch's own condition) plus whatever else the caller
            # sent -- copied onto the new URL verbatim, nothing added
            # or removed.
            return redirect(f"{reverse('chat-all')}?{request.GET.urlencode()}")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.update(_index_context(self.request))
        return context


def _index_context(request, *, selected: str | None = None) -> dict:
    """Everything the index page needs, built ONCE.

    `conversation_start`'s refusal re-render (below) calls this too,
    so an error response can never show a different agent list or a
    different picker selection than a success would -- the same reason
    `thread.py::thread_context` exists.

    `selected` is an ARGUMENT for the same reason `thread_context`'s
    is: the refusal re-render happens on a POST, where the pick is in
    `request.POST`, and a builder that only ever looked at the query
    string would silently revert the operator's chosen model on the
    one render where they are least able to notice. `None` means "read
    the query string", the GET path's own behaviour.
    """
    if selected is None:
        selected = request.GET.get("connection", "")
    principal = principal_for_request(request)
    agents = list(chat_surface_agents(principal))
    # Every ROW's slug, enabled or not -- the offers computation below
    # needs this (review fix wave item 2), and `nothing_installed`'s
    # OWN key needs it too (this follow-up): keying that banner on
    # `agents` (enabled-only) would call a box with every default
    # installed-and-disabled "no agents installed", which is false and
    # points the operator at the wrong fix (`install_defaults`, which
    # is a no-op on rows that already exist, rather than `--reset`,
    # which re-enables them).
    installed_slugs = list(installed_agent_slugs(principal))
    # THE SETTINGS ASSISTANT DOES NOT COUNT AS "INSTALLED" FOR THIS
    # BANNER (owner ruling 2, review deviation from the brief's own
    # illustration -- see `agents/chat/README.md`): a box whose only
    # agent row is the settings assistant must say "nothing installed",
    # never "all installed agents are disabled" -- the assistant is
    # neither the fix `_NOTHING_INSTALLED` names nor the state
    # `_ALL_INSTALLED_DISABLED` describes, because it never sits on the
    # picker either enabled or disabled.
    chat_surface_installed_slugs = [slug for slug in installed_slugs
                                    if slug.lower() not in _SETTINGS_SURFACE_SLUGS_CI]
    if not chat_surface_installed_slugs:
        nothing_installed = _NOTHING_INSTALLED
    elif not agents:
        # Rows exist -- so `_NOTHING_INSTALLED` would be a lie -- but
        # none is enabled, so the picker is exactly as empty as it
        # would be with zero rows. A distinct, honest sentence naming
        # the actual fix, not a reuse of either neighbouring state.
        nothing_installed = _ALL_INSTALLED_DISABLED
    else:
        nothing_installed = ""
    # AUDIT 2, A2: ONE settings-row read for this render, threaded into
    # both consumers -- the house idiom every other context builder in
    # this package already follows (`_working_context`, `thread_context`,
    # `_all_context`), and the one this builder had drifted off when
    # round 18 added a second `settings_row_for(request)` call below.
    # With `IdentityGateMiddleware` in front this is a free attribute
    # read either way; on a render the gate did not touch, the fallback
    # is `IdentitySettings.get_solo()` -- two queries AND a second
    # INSTANCE, which defeats `identity.access._user_row`'s own
    # per-instance memo and costs a further `identity_user` read inside
    # `composer_attach_context`.
    settings_row = settings_row_for(request)
    return dict(
        # The sidebar, on the index exactly as on a thread page. `?archived
        # =1` is a plain truthiness read of a query key: anything else --
        # absent, empty, "0" -- is the active list, so a hand-typed URL can
        # only ever land on one of the two lists that exist.
        # `settings_row` -- the row the gate middleware already read for
        # this request, threaded through so the sidebar's per-row
        # `may_manage` question does not re-read the identity singleton
        # once per conversation. See `sidebar_context`'s own note.
        **sidebar_context(principal, archived=request.GET.get("archived") == "1",
                          settings_row=settings_row),
        agents=agents,
        # Shipped defaults this box has not adopted. Computed from
        # every INSTALLED slug (`installed_slugs`, enabled or not),
        # never from `agents` -- an offer and the picker answer
        # different questions, and a disabled default is still
        # installed: it must stay off the offers list even though
        # `agents` (which the picker reads) correctly drops it. Using
        # `agents` here would make a disabled default's "Add" button
        # come back, wired to an `install_default` that is a permanent
        # no-op (review fix wave item 2).
        #
        # THE SETTINGS ASSISTANT IS NOT OFFERED HERE (owner ruling 2),
        # UNCONDITIONALLY -- stripped from `missing_defaults`'s own
        # result rather than from `installed_slugs` above, because
        # `missing_defaults` answers "not yet installed" and pretending
        # the assistant is uninstalled (by dropping it from
        # `installed_slugs`) would make it MORE likely to appear here,
        # not less, on a box where it actually is installed. This is
        # the one subtraction that holds "installed or not".
        offers=[spec for spec in missing_defaults("agent", installed_slugs)
                if spec.slug.lower() not in _SETTINGS_SURFACE_SLUGS_CI],
        nothing_installed=nothing_installed,
        picker=chat_picker_options(principal, selected),
        selected_connection=selected,
        setup_url=reverse("inference-console"),
        # ROUND 18: `chat/_composer.html`'s own attach-door gate --
        # `workstream=None` because `/chat/` only ever starts a LOOSE
        # conversation (the workstream card's own start form is
        # `agents.chat.views.workstreams._working_context`, a separate
        # call with a real stream).
        **composer_attach_context(principal, settings_row=settings_row),
    )


def _startable_agent(request, slug: str):
    """The agent named by `slug` that this principal may start a
    conversation with, or `None`.

    Through `visible_agents`, not a direct query (ruling 4c), so the
    picker and this lookup can never disagree about which agents exist
    -- including about `enabled`, which that function applies.
    Case-insensitive, matching `Agent`'s own `uniq_agent_slug_ci`
    constraint and `manage.py agent_turn`'s lookup.
    """
    principal = principal_for_request(request)
    return chat_surface_agents(principal).filter(slug__iexact=(slug or "").strip()).first()


@require_POST
def conversation_start(request):
    """POST /chat/start/ -- open a new thread with one agent, and post
    its first turn when there is a message.

    RECORDS THE ACTING PRINCIPAL (ruling 4b), through
    `agents.visibility.create_conversation` -- which is also the only
    way this module may reach that manager at all (ruling 4c). In open
    mode the owner is `OPEN_PRINCIPAL`, the true statement about a box
    with no accounts rather than a placeholder. A row written with
    blank owner columns is a row a later filter cannot reason about,
    and backfilling one is a migration nobody has the information to
    write.

    The title is set from the first user message when there is one, by
    `service.start_turn`, and is NEVER generated by a model --
    that would be a second, invisible model call per conversation (spec
    section 7.2).

    A bad agent is a 400 and writes nothing. It is a caller error: the
    slug came from a `<select>` this page rendered from live rows, so
    an unknown one means the agent was disabled or removed between the
    render and the submit, and starting a thread with a different agent
    would be worse than saying no.

    A BLANK MESSAGE WITH NO FILES starts an empty thread -- the index's
    own form always carries a `text` field, but nothing downstream
    requires one, and a bare "start a conversation" is not an error. A
    NON-BLANK message, OR ONE CARRYING FILES (round 18), goes through
    `start_turn` exactly as the thread's own message form does; when it
    refuses, the just-created empty conversation is deleted -- a
    refused start must never leave an empty thread sitting in the list
    -- and the index re-renders with the refusal named, never a bare
    redirect to a thread that no longer exists.
    """
    agent = _startable_agent(request, request.POST.get("agent"))
    if agent is None:
        # ITEM 6 (Coherence Wave D): the shelled re-render shape this
        # SAME view already uses a few lines below for a refused
        # `start_turn` (`context["start_error"]`, `chat/index.html:84-85`'s
        # own banner slot), not a raw 400 -- a bare plain-text page was
        # never the "caller error" decision this docstring argues for,
        # only the refusal itself.
        context = _index_context(request)
        context["start_error"] = (
            "Pick an agent that exists and is enabled. Add one of the defaults on "
            "the chat page, or run `manage.py install_defaults`, if this box has "
            "no agents yet."
        )
        return render(request, "chat/index.html", context, status=400)
    connection = request.POST.get("connection", "")
    principal = principal_for_request(request)
    # THE STREAM, IF THIS THREAD IS BEING BORN IN ONE (spec §14). It must
    # be in `visible_workstreams` AND PASS `stream_access` (WS-2), else
    # 400 -- a caller error, exactly like the unknown agent above: the id
    # came from a page this surface rendered, so an unreachable one means
    # the stream was deleted or unshared between the render and the
    # submit, and starting a thread somewhere else would be worse than
    # saying no. `stream_access` IS THE SAME REFUSAL as the row-missing
    # case, not a second message: a dormant recipient's grants no longer
    # cover the stream's tags, which reads to them exactly like the
    # share having been revoked -- the stream is not available to start a
    # new conversation in, whichever of the two caused it. The owner and
    # `sees_all_content` are unaffected (`stream_access`'s own owner
    # branch, no tag check).
    raw_stream = request.POST.get("workstream", "")
    stream = None
    if raw_stream:
        stream = (visible_workstreams(principal).filter(pk=int(raw_stream)).first()
                  if raw_stream.isdecimal() else None)
        if stream is None or not stream_access(
                principal, stream, settings_row=settings_row_for(request)).ok:
            # ITEM 6 (Coherence Wave D): same shelled re-render as the
            # bad-agent refusal just above -- see that branch's own
            # comment. `selected=connection`: the picker's own choice
            # already came off `request.POST` above, so the re-render
            # must not silently revert it -- the same reason the
            # `start_turn` refusal a few lines below passes it too.
            context = _index_context(request, selected=connection)
            context["start_error"] = (
                "That workstream is not available. It may have been deleted, or the "
                "share that reached it may have been revoked.")
            return render(request, "chat/index.html", context, status=400)
    conversation = create_conversation(principal, agent, workstream=stream)
    text = request.POST.get("text", "")
    # ROUND 18 (owner feedback: "the ability to add an attachment isn't
    # on the intial new chat screen"): `chat/_composer.html` now renders
    # the SAME attach door on this form that `chat/conversation.html`'s
    # own turn-form does, and both surfaces read its three field names
    # through the ONE reader (`composer_attachment_fields`, audit 2 F6)
    # before threading them into the SAME `start_turn` -- the first
    # turn's own attachments are staged through the identical
    # transaction discipline a later turn's are, never a second,
    # bespoke path. `files` is unpacked into a local of its own because
    # the blank-message branch below has to ask about it.
    fields = composer_attachment_fields(request)
    files = fields["files"]
    # A BLANK MESSAGE WITH NO FILES EITHER still starts an empty thread
    # (unchanged, existing behaviour -- see this function's own docstring).
    # A blank message WITH files, though, must not go the "empty thread"
    # route: `start_turn` requires `text` (`BLANK_MESSAGE`), so silently taking
    # that branch would drop the files on the floor with no error at all
    # -- precisely the "I attached a file and nothing happened" complaint
    # this whole door exists to prevent. `text.strip() or files` routes a
    # files-only submission through `start_turn` instead, which then
    # refuses it with an honest, actionable message.
    if text.strip() or files:
        start = start_turn(conversation, text, connection=connection,
                           actor=principal, **fields)
        if not start.ok:
            # A refused start must never leave an empty thread behind
            # for the operator to find in the list.
            conversation.delete()
            context = _index_context(request, selected=connection)
            context["start_error"] = start.error
            return render(request, "chat/index.html", context, status=start.status)
        flash_attachment_outcomes(request, start.attachments)
        return redirect(
            conversation_url(conversation, connection=connection, pending=start.turn.pk)
        )
    return redirect(conversation_url(conversation, connection=connection))


@require_POST
def conversation_delete(request, conversation_id):
    """POST /chat/c/<uuid>/delete/ -- remove one thread.

    The TURNS go with it (`Turn.conversation` is CASCADE). The AUDIT
    DOES NOT: `Turn.invocation` is `SET_NULL`, so every `ToolInvocation`
    this conversation produced survives, principal and outcome intact.
    That asymmetry is deliberate and is the 2026-08-27 addendum's
    consequence 3 doing its job -- the audit row is not owned by the
    conversation table, precisely so deleting a conversation cannot
    erase the record of what was called.

    The AGENT is untouched: `Conversation.agent` is `PROTECT` in the
    other direction only.

    D4: a `messages.info(...)` notice ("Conversation deleted.") rides
    the redirect to `chat-index`, which renders it the same way
    `tools/rag/views.py` already does (`django.contrib.messages` is
    installed platform-wide -- `config/settings.py`'s `MIDDLEWARE` and
    `INSTALLED_APPS`) -- reused rather than a bespoke `?deleted=1` query
    flag, which is the fallback this task names only for a project that
    had not already adopted the framework.
    """
    principal = principal_for_request(request)
    conversation = visible_conversation_or_404(principal, conversation_id)
    if not delete_conversation(principal, conversation):
        raise Http404(f"Conversation {conversation_id} does not exist.")
    messages.info(request, "Conversation deleted.")
    return redirect(reverse("chat-index"))


# `validated_next_url` MOVED to `agents/chat/service.py` (ROUND-13
# REVIEW FIX, MINOR 2) -- `turns.py::attachment_detach` needed the
# identical guard, and `turns.py` may not import this module (the
# package's import direction is one-way, `views/__init__.py`'s own
# docstring) -- so it lives in the one module BOTH already import,
# the same reason `conversation_url` lives there rather than here.


@require_POST
def conversation_rename(request, conversation_id):
    """POST /chat/c/<uuid>/rename/ -- retitle one thread.

    THE SERVER RE-TRUNCATES, through `service.fit_title` -- the same
    `truncate_title` rules an auto-derived title goes through, spent
    against the COLUMN's width rather than the list's -- so a hand-typed
    title that is too long is cut on a word boundary with a trailing
    "…" rather than rejected or clipped mid-word by the database. The
    input's own `maxlength` is a courtesy; this is the rule.

    A BLANK TITLE IS REFUSED, not a silent clear. The form's input is
    `required`, so a blank body is a hand-made request or a stale form
    -- and "the thread now has no name" is not a thing anybody asks for
    by leaving a box empty. A flashed refusal and a redirect (Coherence
    Wave D, item 6 -- was a bare 400 until then; see the branch's own
    comment below), never the 404 this class's refusals otherwise use:
    the caller can see the row (they got past
    `visible_conversation_or_404`), so there is nothing left to conceal, and
    the honest answer is that the body was wrong.
    """
    principal = principal_for_request(request)
    conversation = visible_conversation_or_404(principal, conversation_id)
    title = fit_title(request.POST.get("title", ""))
    if not title:
        # ITEM 6 (Coherence Wave D): flash-and-redirect, not a raw 400 --
        # this docstring argues for the STATUS CODE (400, not 404), not
        # for a bare plain-text page. The rename form is a plain zero-JS
        # `<form>` (`chat/_sidebar_row.html:59-67`) carrying its own
        # `next`, the same redirect target the success path below uses.
        messages.error(request, _BLANK_TITLE)
        return redirect(validated_next_url(request) or conversation_url(conversation))
    if not rename_conversation(principal, conversation, title):
        raise Http404(f"Conversation {conversation_id} does not exist.")
    messages.info(request, "Conversation renamed.")
    return redirect(validated_next_url(request)
                    or conversation_url(conversation))


@require_POST
def conversation_duplicate(request, conversation_id):
    """POST /chat/c/<uuid>/duplicate/ -- a fresh thread carrying this
    one's finished history.

    LANDS ON THE COPY, never back where it was posted from -- the one
    action here that ignores `next`. A duplicate the operator cannot
    see is a duplicate they will make twice.

    `"Copy of <title>"` through the same `service.fit_title` a rename
    uses, so copying a thread whose title already fills the column
    produces a legal title rather than a database error -- and the cut
    lands in the TITLE, never in the prefix (that function's own note).
    An untitled thread copies to `"Copy of"` with nothing after it,
    which reads correctly beside the sidebar's own "Untitled
    conversation" fallback.

    WHAT DOES AND DOES NOT COME ACROSS is
    `agents.visibility.duplicate_conversation`'s decision, documented
    there: finished turns only, no audit links, no queue-job ids.
    """
    principal = principal_for_request(request)
    conversation = visible_conversation_or_404(principal, conversation_id)
    title = fit_title(conversation.title, prefix="Copy of ")
    copy = duplicate_conversation(principal, conversation, title=title)
    if copy is None:
        raise Http404(f"Conversation {conversation_id} does not exist.")
    messages.info(request, "Conversation duplicated.")
    return redirect(conversation_url(copy))


@require_POST
def conversation_archive(request, conversation_id):
    """POST /chat/c/<uuid>/archive/ -- take one thread out of the
    sidebar without destroying anything.

    ARCHIVING IS NOT DELETING, and the two controls sit next to each
    other in the same menu, so the difference has to be visible in what
    happens next: this one keeps every turn, stays readable at its own
    URL, and comes back with one click from `/chat/?archived=1`.

    Lands back on the page it was posted from (`next`), because
    archiving is a housekeeping gesture in the middle of doing something
    else -- and the thread page of a thread you just archived is a
    perfectly honest place to be: it is still yours and still readable.
    """
    return _set_archived(request, conversation_id, archived=True,
                         notice="Conversation archived.")


@require_POST
def conversation_unarchive(request, conversation_id):
    """POST /chat/c/<uuid>/unarchive/ -- put one thread back in the
    sidebar."""
    return _set_archived(request, conversation_id, archived=False,
                         notice="Conversation unarchived.")


def _set_archived(request, conversation_id, *, archived: bool, notice: str):
    """The body both archive views share. Two routes rather than one
    with a direction in its body, because each is a single-purpose POST
    a form can name -- and a route whose meaning depends on a field is a
    route a stale form can invert.
    """
    principal = principal_for_request(request)
    conversation = visible_conversation_or_404(principal, conversation_id)
    if not set_conversation_archived(principal, conversation, archived=archived):
        raise Http404(f"Conversation {conversation_id} does not exist.")
    messages.info(request, notice)
    return redirect(validated_next_url(request) or reverse("chat-index"))


@require_POST
def conversation_pin(request, conversation_id):
    """POST /chat/c/<uuid>/pin/ -- round 20 (owner, verbatim: "can we
    add the ability t[o] pin chats so it's always shown in its own
    se[c]tion... between Workstreams and Chats"). Mirrors `conversation_
    archive`'s own shape exactly -- row-addressed, POST-only, the SAME
    `next`-redirect landing behaviour, the SAME gate.
    """
    return _set_pinned(request, conversation_id, pinned=True,
                       notice="Conversation pinned.")


@require_POST
def conversation_unpin(request, conversation_id):
    """POST /chat/c/<uuid>/unpin/ -- put one thread back into the
    ordinary Chats list."""
    return _set_pinned(request, conversation_id, pinned=False,
                       notice="Conversation unpinned.")


def _set_pinned(request, conversation_id, *, pinned: bool, notice: str):
    """The body both pin views share -- `_set_archived`'s own shape,
    copied rather than merged with it: the two features are two
    different columns behind two different verbs, and a single body
    parameterised on WHICH COLUMN would read as one feature wearing two
    names rather than two features that happen to share a gate.
    """
    principal = principal_for_request(request)
    conversation = visible_conversation_or_404(principal, conversation_id)
    if not set_conversation_pinned(principal, conversation, pinned=pinned):
        raise Http404(f"Conversation {conversation_id} does not exist.")
    messages.info(request, notice)
    return redirect(validated_next_url(request) or reverse("chat-index"))
