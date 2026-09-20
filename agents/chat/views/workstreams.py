"""The workstream surface — a list, a setup screen, TWO stream pages,
one edit route with an `action` field, and the wall editor.

THREE PAGES ON THIS PRINCIPAL'S OWN STREAMS, EACH WITH ONE JOB (owner
feedback drove two splits in sequence). `chat-workstreams`
(`workstream_list`) only lists any more -- GET, class A, no create form
left on it at all. `chat-workstream-new` (`workstream_new`,
`_setup_context`) is the SETUP SCREEN (owner feedback: "should we have
it on its own screen rather than chilling at the top... workstream
setup that has key items and who's full extent?"): name, description,
instructions, scope, and the upload default, one form, one submit,
before the stream exists at all. `chat-workstream`
(`workstream_page`, `_working_context`) is the WORKING surface for a
stream that already exists -- New chat, Conversations, Documents -- and
stays what a share recipient sees, `stream_access`'s own 403 exception
included. `chat-workstream-settings` (`workstream_settings`,
`_settings_context`) hosts what changes rarely once a stream is
running -- Manage, Instructions, Scope, Upload default, Tags, Shared
with -- and it is OWNER-ONLY under the plain house 404 rule: no
`stream_access` call, no 403 exception, because every section it hosts
already needed ownership.

EVERY ROW COMES THROUGH `agents/visibility.py` (ruling 4c, extended by
this phase): `foundation/ops/tests/test_column_boundaries.py` now
forbids any module under `agents/chat` from touching `Workstream.
objects` as well as the four it already covered.

ALMOST ZERO JAVASCRIPT (narrowed by round 18, the same way `chat/
workstream.html`'s own docstring narrows the identical claim). Every
control below is a link, a `<details>` element, or a plain POST form
with a CSRF token -- the idiom `chat/_sidebar.html` already documents
-- except the WORKING page's own "New chat" card, which is `chat/
_composer.html` now and carries that fragment's own small, progressive
scripts (Enter-to-send, attachment staging, drag-and-drop).
"""
from __future__ import annotations

import uuid
from functools import wraps

from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import (
    require_GET, require_POST, require_http_methods, require_safe,
)

from agents.chat.service import QUEUE_UNAVAILABLE, composer_attach_context
from agents.chat.sidebar import sidebar_context
from agents.entitlements import tool_access_for
from agents.models import Share
from agents.visibility import (
    ShareRefused, chat_surface_agents, create_workstream, delete_workstream,
    latest_completed_turn_index, may_manage_workstream, rename_workstream,
    revoke_workstream_share, set_workstream_archived, set_workstream_description,
    set_workstream_include_universal, set_workstream_instructions, set_workstream_scope,
    set_workstream_upload_default, share_list_for, share_workstream, visible_conversations,
    visible_workstreams, workstream_taint_ids,
)
from agents.workstreams import panels_for, staleness_for, stream_access
from identity.access import (
    accounts_on, entitlement_names, held_entitlement_ids, share_subjects,
)
from identity.contracts.principals import payload_fields
from identity.request import principal_for_request, settings_row_for
from models.contracts.bindings import resolve
from models.contracts.queue import QueueQuotaExceeded, QueueUnavailable, enqueue
from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_EMBED_ROLE
from models.queue.visibility import live_job_for

# F10 review round 2 (Important, render-vs-gate): the tool key the
# rag-owned panel's Upload section gates on, alongside `is_owner` --
# `tool_access_for(principal).allows(...)` is `documents.html`'s OWN
# `may_upload` predicate (`tools/rag/views.py::_may_upload`), so an
# owner who lacks the `rag.ingest` label sees the SAME "no upload here"
# absence a non-owner already gets, instead of a form that renders and
# then 403s bare on submit -- the exact render-vs-gate bug class the
# `pin_into_scope.may_upload` comment in `tools/rag/views.py` already
# documents.
#
# A STRING, NEVER AN IMPORT: `agents/` may not import `tools/` at all
# (import-law rule 3, `foundation/ops/tests/test_import_law.py::
# test_no_agents_module_imports_a_tools_package`), so this crosses the
# seam as data, the same way `workstream_consolidate`'s own "THE JOB
# KIND IS A STRING" docstring already crosses it for a queue kind.
# Pinned equal to `tools.rag.tools.RAG_INGEST_TOOL_KEY` by
# `tools/rag/tests/test_workstream_upload_panel.py` (the REVERSE
# direction -- `tools/rag` may import `agents/` freely -- mirroring
# `tools/rag/tests/test_upload_placement.py::
# test_the_views_placement_literals_equal_the_models_own_values`'s own
# same-shaped pin).
_RAG_INGEST_TOOL_KEY = "rag.ingest"

# The eight actions `chat-workstream-edit` accepts (ROUND 17 adds
# "include_universal" to the original seven). ONE ROUTE, ONE
# PREDICATE, ONE 404 RULE (author decision 11) -- the shape
# `identity-entitlement-edit`'s `_ENTITLEMENT_ACTIONS` tuple already has
# and `may_manage_conversation`'s one-predicate-for-five ruling applied a
# third time. Eight routes would be eight chances for one to drift, and
# the drift would show as a control that 404s.
_WORKSTREAM_ACTIONS = ("rename", "description", "instructions", "upload_default",
                       "include_universal", "archive", "unarchive", "delete")


def _resolve(request, pk):
    """The stream, or `Http404`. Resolved through `visible_workstreams`,
    so a stranger and a nonexistent row are indistinguishable."""
    principal = principal_for_request(request)
    row = visible_workstreams(principal).filter(pk=pk).first()
    if row is None:
        raise Http404("No such workstream.")
    return principal, row


def stream_owner_required(view):
    """CONSOLIDATION WAVE (audit B, S16): `if not may_manage_workstream(
    principal, stream): raise Http404(...)`, typed verbatim at FIVE call
    sites in this module (`workstream_settings`, `workstream_edit`,
    `workstream_scope`, `workstream_share`, `workstream_consolidate`) --
    the third occurrence of a guard becomes a decorator (R6). Resolves
    `pk` through `_resolve` (above), 404s anyone not `may_manage_
    workstream` for the resolved row, and hands the wrapped view
    `(request, principal, stream)` in place of `(request, pk)`.

    `workstream_scope`'s OWN gate used to run CONDITIONALLY, only after
    `set_workstream_scope` had already been called and refused --
    purely to tell a not-owner 404 apart from an owner-with-bad-
    entitlements 400. That function ALREADY checks `may_manage_
    workstream` internally FIRST (`agents/visibility.py`, `return None`
    before it ever reads the submitted entitlement ids), so a not-owner
    caller never wrote anything either way -- gating EAGERLY here,
    before the view body runs at all, changes no observable behaviour,
    only WHEN the identical fact is checked.

    THE ONE LEGITIMATE EXCEPTION this decorator does NOT cover:
    `workstream_page` (`chat-workstream`) deliberately answers a 403
    PAGE (`workstream_dormant.html`) for a live-but-dormant share
    recipient, via `stream_access(...).ok`, never a plain `may_manage_
    workstream` 404 -- a different route class this decorator was never
    meant to also handle, now visible as the one view in this module
    that does NOT use it, rather than invisible among five identical
    404s.
    """
    @wraps(view)
    def wrapper(request, pk, *args, **kwargs):
        principal, stream = _resolve(request, pk)
        if not may_manage_workstream(principal, stream):
            raise Http404("No such workstream.")
        return view(request, principal, stream, *args, **kwargs)
    return wrapper


def _stream_rows(principal, *, archived: bool):
    """The active or archived half of this principal's stream list,
    each row carrying its own `conversation_count`.

    THE ONE BUILDER `workstream_list` calls for both halves, and the one
    its own query-count pin (`agents/chat/tests/test_workstream_page.py::
    TestTheListBuilderCostsTheSameAtOneRowAndAtTwentyFive`) calls
    directly -- the same builder/view split `agents/chat/sidebar.py::
    sidebar_context` already established, so a regression shows up at
    the queryset level and not only through the extra session/auth reads
    a full HTTP request carries.

    `.annotate(Count("conversations"))`, NOT a per-row `.count()` in the
    template -- the list renders each stream's conversation count (spec
    §15.1), and a per-row query would be the exact N+1 Global
    Constraint 6 forbids on a list surface. One JOIN + GROUP BY keeps
    this flat at 1 row and at 25, the same shape `tools/rag/workstreams
    .py::panel`'s own "ONE QUERY FOR THE WHOLE PIN SET" comment argues
    for.
    """
    return (visible_workstreams(principal)
            .filter(archived_at__isnull=not archived)
            .annotate(conversation_count=Count("conversations")))


@require_GET
def workstream_list(request):
    """GET `/chat/w/` lists. Nothing else -- POST-create is RETIRED from
    this route (owner feedback round 7: "should we have it on its own
    screen rather than chilling at the top"), moved wholesale to
    `chat-workstream-new` below. `@require_GET` makes the retirement
    real rather than aspirational: a POST here now answers a clean 405,
    never a silent no-op render and never the create it used to do.

    CLASS A: the list is this principal's own streams and the ones shared
    to them, so there is no row to be addressed by and nothing to 404
    on.
    """
    principal = principal_for_request(request)
    context = {
        "workstreams": list(_stream_rows(principal, archived=False)),
        # CONSOLIDATION WAVE (audit B, S18): `archived_streams`, not the
        # bare `archived` this used to be -- `agents.chat.views.all_
        # conversations`'s own context key of the same bare name is a
        # BOOL (`showing_archived` there now) for a DIFFERENT page; one
        # name meant two types depending which page you were reading.
        "archived_streams": list(_stream_rows(principal, archived=True)),
    }
    context.update(sidebar_context(principal, settings_row=settings_row_for(request)))
    return render(request, "chat/workstreams.html", context)


def _setup_context(request, principal, *, error="", name="", description="",
                    instructions="", selected_ids=None, placement=""):
    """`chat/workstream_new.html`'s whole context -- the ONE builder for
    both the GET and every POST refusal, the identical "a re-render can
    never be missing a section the GET has" guarantee `_working_context`/
    `_settings_context` already give their own pages.

    EVERY FIELD DEFAULTS TO BLANK, matching a fresh GET; a POST refusal
    (a duplicate name, an invalid scope submission) passes back exactly
    what the caller typed and checked, so a rejected submission never
    loses what was already filled in -- `name`/`description`/
    `instructions`/`selected_ids`/`placement` are the raw, UNVALIDATED
    strings/ids `workstream_new` read from `request.POST`, not anything
    read back off a row (there is no row yet on a refusal).

    SCOPE GATED ON `accounts_on()` ALONE (ruling A), not on any
    "is_owner" question the way `_settings_context`'s own `scope_visible`
    is -- every visitor to this page is, by construction, about to own
    the stream they are describing, so there is no second principal's
    ownership left to ask about.
    """
    settings_row = settings_row_for(request)
    accounts = accounts_on(settings_row=settings_row)
    context = {
        "page_error": error,
        "name": name,
        "description": description,
        "instructions": instructions,
        "placement": placement,
        "scope_visible": accounts,
        "scope_options": (entitlement_names(held_entitlement_ids(principal))
                          if accounts else ()),
        "scope_selected": selected_ids or set(),
    }
    context.update(sidebar_context(principal, settings_row=settings_row))
    return context


# R7 (audit 2, S22): the method vocabulary is DECLARED, not implied by
# a branch in the body. GET renders, POST mutates, and anything else is
# a 405 from Django before this function runs -- so the `request.method`
# test below chooses between methods this view really serves rather than
# silently treating a PUT or a DELETE as a read.
#
# HEAD IS LISTED EXPLICITLY (audit-2 confirm, observation 7). Django
# serves HEAD by running the GET path and dropping the body, which is
# what this view did before it declared anything; `require_http_methods`
# admits only what it is given, so omitting HEAD would have turned a
# previously-working request into a 405 for anything that HEADs a chat
# URL. Declaring the vocabulary was the point -- narrowing it was not.
@require_http_methods(["GET", "HEAD", "POST"])
def workstream_new(request):
    """GET `/chat/w/new/` renders the workstream SETUP SCREEN; POST
    creates.

    CLASS A, the same class `chat-workstreams` itself carries: creating a
    stream needs an authenticated principal and nothing else -- there is
    no row yet, so there is nothing to be addressed by and nothing to
    404 on.

    ONE FORM, ONE SUBMIT, ONE TRANSACTION. The writers below are the
    EXACT SAME ones the settings page's own three POST routes call --
    `create_workstream`, then (only when provided) `set_workstream_
    instructions`, `set_workstream_scope`, `set_workstream_upload_
    default` -- called in sequence against the row this same request
    just created, inside one `transaction.atomic()` block so a mid-
    sequence refusal leaves NOTHING behind: not the row, not its audit
    trail entries, not a half-set scope. `may_manage_workstream`'s guard
    inside every one of those writers passes trivially here -- the
    principal that just created the row is, by construction, its owner
    -- so their own refusal branches (an outsider posting, a workstream
    that stopped existing mid-request) can never fire; only two REAL
    refusals remain reachable from here, and each is handled explicitly:
    a duplicate/blank name (`create_workstream` returns `None`, the
    identical refusal `chat-workstreams`' own retired create form always
    had), and a scope submission naming an entitlement the principal does
    not hold (`set_workstream_scope` returns `None`) -- checked the same
    way `workstream_scope`'s own dedicated view already checks it,
    because a tampered request is the only way a chip-picker rendered
    from `held_entitlement_ids(principal)` could ever submit an id
    outside that set. Instructions and the upload default are NOT
    return-checked, matching `workstream_edit`'s own established
    precedent for those same two writers (`action="instructions"`/
    `"upload_default"` there ignore the boolean too) -- their only
    refusal branch is the ownership guard already proven to pass.
    """
    principal = principal_for_request(request)
    if request.method != "POST":
        return render(request, "chat/workstream_new.html", _setup_context(request, principal))

    name = request.POST.get("name", "")
    description = request.POST.get("description", "")
    instructions = request.POST.get("instructions", "")
    placement = request.POST.get("placement", "")
    raw_ids = [i for i in request.POST.getlist("entitlements") if str(i).isdecimal()]
    selected_ids = {int(i) for i in raw_ids}

    # THE WHOLE SEQUENCE RUNS INSIDE ONE `atomic()` BLOCK, but every
    # `render()` call happens strictly AFTER it exits, never inside it:
    # `_setup_context` reads several tables of its own (`accounts_on`,
    # `entitlement_names`, `sidebar_context`'s own queries), and Django
    # refuses to run ANY query on a connection already marked for
    # rollback (`TransactionManagementError`) -- rendering from inside a
    # block whose `set_rollback(True)` just fired would crash on its own
    # refusal banner. `error`/`row` are set INSIDE the block and read
    # back OUTSIDE it instead.
    error = ""
    row = None
    with transaction.atomic():
        row = create_workstream(principal, name, description=description)
        if row is None:
            error = ("You already have a workstream called that, or the name was "
                     "blank. Pick another name.")
        else:
            if instructions.strip():
                set_workstream_instructions(principal, row, instructions)
            if selected_ids and set_workstream_scope(principal, row, selected_ids) is None:
                error = "A workstream's scope may only name entitlements you hold."
                # `set_rollback`, not a plain fall-through: without it
                # `atomic()` would COMMIT the row `create_workstream`
                # already wrote (and any instructions just set on it) the
                # moment this block exits normally -- it only rolls back
                # on an exception or this explicit flag, never merely
                # because a later line noticed a problem.
                transaction.set_rollback(True)
            elif placement:
                set_workstream_upload_default(principal, row, placement)

    if error:
        return render(request, "chat/workstream_new.html",
                      _setup_context(request, principal, error=error, name=name,
                                     description=description, instructions=instructions,
                                     selected_ids=selected_ids, placement=placement),
                      status=400)
    return redirect("chat-workstream", pk=row.pk)


def _working_context(request, principal, stream, *, is_owner: bool):
    """The WORKING page's whole context (`chat/workstream.html`) -- New
    chat, Conversations, Documents. ONE BUILDER, called by the GET only:
    unlike before the settings split, no POST handler re-renders this
    page any more (the settings page took over every form whose failure
    used to land here -- Rename/Description/Instructions/Upload default/
    Archive/Delete/Scope/Share). `page_error` is therefore GONE from this
    builder and from `chat/workstream.html` itself, not merely unused:
    `_settings_context`, below, is now the only builder that ever
    receives an `error`.

    OWNER FEEDBACK, VERBATIM (the split this function is half of): "the
    workstream chat needs to keep the settings that are not often
    updated on a separate settings page, we dont need to see all the
    items when we go to a workstream." This builder is what is left once
    everything "not often updated" moves out: `is_owner` still gates the
    New-chat hint and the Conversations section's own staleness hints/
    Consolidate button (both still on the WORKING page), and the
    Documents panel's own Upload section (`placement_state`/
    `may_upload_tool`), but computes NOTHING `_settings_context` alone
    needs -- no `accounts_on`, no Scope, no Tags, no Shares -- so this
    page's own query cost drops rather than merely staying flat.

    `is_owner`, REQUIRED and keyword-only (E7, unchanged by the split):
    every call site has already settled this question by the time it
    calls here -- `workstream_page` through `stream_access`'s own
    `StreamAccess.is_owner`.
    """
    settings_row = settings_row_for(request)
    conversations = list(visible_conversations(principal, settings_row=settings_row)
                        .select_related("workstream")
                        .filter(workstream_id=stream.pk)
                        .order_by("-updated_at"))
    # THE STALENESS HINT IS OWNER-ONLY, AND `staleness_for` IS SIMPLY NOT
    # CALLED for a non-owner (ruling F): a staleness hint is a prompt to
    # press Consolidate, and rendering one to a recipient who would get a
    # 404 on that button would be the render-vs-gate pair broken on the
    # most visible surface there is. One call for the whole page, not one
    # per row -- `staleness_for`'s own docstring.
    if is_owner:
        hints = staleness_for(conversations)
        for c in conversations:
            c.staleness_hint = hints.get(c.pk, "")
    # ROUND 18: `chat/_composer.html`'s own attach door on the New chat
    # card needs this for EVERY viewer, not only the owner (a live share
    # recipient may also start a conversation here -- this page's own
    # template renders the card regardless of `is_owner`, only a hint
    # paragraph changes) -- so the "compute lazily, owner-only" saving
    # `may_upload_tool` (below) used to bank on no longer holds; resolved
    # ONCE, here, and threaded into BOTH keys that need it, rather than
    # `tool_access_for` paying its own query twice for the same viewer on
    # the same render.
    rag_tool_access = tool_access_for(principal, settings_row=settings_row)
    context = {
        "workstream": stream,
        "is_owner": is_owner,
        "conversations": conversations,
        "agents": list(chat_surface_agents(principal)),
        "panels": panels_for(principal, stream.pk),
        "placement_state": ("default" if stream.default_upload_placement
                            else "choose") if is_owner else "none",
        "may_upload_tool": (rag_tool_access.allows(_RAG_INGEST_TOOL_KEY)
                            if is_owner else False),
    }
    # `may_upload=is_owner` (ROUND-18-CONFIRM R7): `is_owner`, ABOVE, is
    # `stream_access`'s own owner branch -- `may_manage_workstream`
    # under yet another name, the SAME predicate `WorkstreamScope.
    # may_upload` is -- already resolved for this exact principal/
    # stream/render before this function was ever called. Passing it
    # through skips `composer_attach_context`'s own default fresh
    # `workstream_scope(...)` resolution (a `visible_workstreams` filter
    # plus a wall read) entirely, rather than paying for the identical
    # answer twice.
    context.update(composer_attach_context(principal, workstream=stream,
                                           settings_row=settings_row,
                                           tool_access=rag_tool_access,
                                           may_upload=is_owner))
    context.update(sidebar_context(principal, settings_row=settings_row))
    return context


def _settings_context(request, principal, stream, *, is_owner: bool, error=""):
    """The SETTINGS page's whole context (`chat/workstream_settings.
    html`) -- Manage (Rename/Description/Archive/Delete), Instructions,
    Scope, Upload default, Tags, Shared with. ONE BUILDER, called by the
    GET and by the three POST handlers (`workstream_edit`/`_scope`/
    `_share`) that re-render with a message rather than redirecting --
    so a re-rendered page can never be missing a section the GET has, the
    same guarantee `_working_context`'s own pre-split ancestor gave.

    OWNER-ONLY BY CONSTRUCTION, not merely by convention: `workstream_
    settings` (the GET view) 404s a non-owner before this ever runs, and
    every POST handler that calls this already ran the identical `may_
    manage_workstream` guard first -- so `is_owner` is always `True` in
    practice here. The keyword stays required anyway, matching `agents:
    is_owner, REQUIRED and keyword-only (E7)`'s own reasoning: a future
    call site that forgot it should fail loudly, not silently re-derive
    the answer.

    SECTIONS 3, 8 AND 9 ARE ABSENT ON AN OPEN BOX, and not disabled -- an
    operator running a household box never sees the words "entitlement",
    "taint" or "share" on this page. That is "machinery invisible until a
    row says otherwise", rendered.
    """
    settings_row = settings_row_for(request)
    accounts = accounts_on(settings_row=settings_row)
    scope_visible = accounts and is_owner
    context = {
        "workstream": stream,
        "is_owner": is_owner,
        # SECTION 3, the wall editor. Rendered from
        # `entitlement_names(held_entitlement_ids(principal))`, NOT
        # `labelling_entitlements`, so the set offered and the set
        # `set_workstream_scope` accepts are the SAME SET (M10, spec
        # §6.1). The condition is THE POSTURE, not "holds no
        # entitlements" (ruling A): the wall is inert on an open box, so
        # a hidden editor strands nothing.
        "scope_visible": scope_visible,
        "scope_options": (entitlement_names(held_entitlement_ids(principal))
                          if scope_visible else ()),
        # GATED THE SAME WAY `scope_visible` IS, deliberately, and not
        # merely mirroring it for symmetry: an unguarded
        # `stream.scope_entitlements.values_list(...)` here would run a
        # `WorkstreamScopeEntitlement` query on every settings-page
        # render, including on an open box where the wall is inert and
        # no permission query is supposed to run at all (identity.access.
        # accounts_on's own docstring). `scope_selected` is read by
        # nothing outside the `{% if scope_visible %}` block, so this
        # guard changes no rendered output -- only whether the query
        # runs.
        "scope_selected": (set(stream.scope_entitlements.values_list(
                               "entitlement_id", flat=True))
                           if scope_visible else set()),
        "page_error": error,
        # SECTIONS 8 AND 9 -- ABSENT (not merely hidden), on an open box
        # (spec §15.3): `accounts_on` gates both. Section 8 (Tags) is
        # SAFE FOR ANY VIEWER WHO REACHES THIS PAGE, owner or recipient:
        # a recipient only gets here when gate two answered `ok=True`,
        # which by construction means they hold every one of the stream's
        # tags -- so naming them discloses nothing they do not already
        # hold. Section 9 (Sharing) is OWNER-ONLY, gated the same way
        # Instructions and the upload default are: only the owner may
        # add or remove a share (`may_manage_workstream`), so a recipient
        # never sees a control the POST would 404. (Both are now moot in
        # PRACTICE, since this whole page is owner-only -- kept as real
        # gates rather than assumptions, the same reasoning `is_owner`'s
        # own docstring above gives for staying required.)
        # CONSOLIDATION WAVE (audit B, S18), two renames:
        # `accounts_enabled`, not `accounts_on` -- the bare context key
        # `accounts_on` shadowed the NAME of the imported function that
        # computes it (`identity.access.accounts_on`), so `{%
        # templatetag openblock %} if accounts_on {% templatetag
        # closeblock %}` in the template read like a call. `stream_
        # shares`, not `shares` -- `agents.chat.views.thread`'s own
        # `shares_for(...)` result used to sit under the SAME bare key
        # for a DIFFERENT row shape (a conversation's own shares); one
        # name meant two shapes depending which page you were reading.
        "accounts_enabled": accounts,
        "workstream_tags": (entitlement_names(workstream_taint_ids(stream))
                            if accounts else ()),
        "stream_shares": share_list_for(stream, principal) if accounts and is_owner else (),
        "share_subjects": share_subjects(principal) if accounts and is_owner else {},
    }
    context.update(sidebar_context(principal, settings_row=settings_row))
    return context


# R7 (audit 2, S22): read-only, and now declared so. A POST to this URL
# is a 405 before any row is resolved.
#
# `require_safe`, NOT `require_GET` (audit-2 confirm, observation 7):
# `require_GET` is `require_http_methods(["GET"])` and rejects HEAD,
# which this view served before it declared anything -- Django answers
# HEAD by running the GET path and dropping the body. `require_safe` is
# the GET+HEAD pair, so the declaration excludes the unsafe verbs it was
# added for without newly refusing a request that used to work.
@require_safe
def workstream_page(request, pk):
    """GET `/chat/w/<pk>/`.

    CLASS O: resolved through `visible_workstreams` -> 404. WS-2 inserts
    the read-time share gate here, between the resolution and the render,
    and it is the ONLY route on this platform that then answers 403
    (spec §12.3, fenced there).
    """
    principal, stream = _resolve(request, pk)
    access = stream_access(principal, stream, settings_row=settings_row_for(request))
    if not access.ok:
        # THE ONE ROUTE ON THIS PLATFORM THAT ANSWERS 403 ON A
        # ROW-ADDRESSED URL, and spec §12.3 fences it in three ways:
        # only a holder of a real, live `Share` row reaches this branch
        # (a stranger was already excluded by `visible_workstreams` and
        # got the house rule's 404); every missing entitlement is named
        # and nothing else is; and the list caps at `NAME_CAP`.
        #
        # It renders NO STREAM CONTENT -- not the conversations, not the
        # documents, not the other recipients. Only the name, which the
        # recipient already has in their sidebar.
        context = {"workstream": stream, "named": access.missing_names,
                   "unnamed_count": access.unnamed_count}
        # NO SIDEBAR CONTEXT HERE, deliberately, and that is a course
        # correction on the ambient one: `chat/_sidebar.html`'s
        # "Conversations" nav is built from `visible_conversations`,
        # which THIS SAME TASK'S two new clauses now admit every
        # conversation IN this stream to a live share holder regardless
        # of the stream's own dormancy -- gate two gates the STREAM PAGE
        # only (spec §14's route table), not each conversation read. A
        # merged `sidebar_context` would put this stream's own
        # conversation titles on the one page whose entire job is to
        # reveal nothing about them
        # (`test_the_403_page_reveals_nothing_about_the_streams_contents`).
        # The template's own "All chats" link is the navigation escape
        # hatch instead; `test_a_dormant_share_still_LISTS_in_the_
        # recipients_sidebar` asserts the stream's OWN listing on
        # `/chat/`, a different page with a different (and correct)
        # sidebar of its own.
        return render(request, "chat/workstream_dormant.html", context, status=403)
    return render(request, "chat/workstream.html",
                  _working_context(request, principal, stream, is_owner=access.is_owner))


# R7 (audit 2, S22): read-only, and now declared so. A POST to this URL
# is a 405 before any row is resolved.
#
# `require_safe`, NOT `require_GET` (audit-2 confirm, observation 7):
# `require_GET` is `require_http_methods(["GET"])` and rejects HEAD,
# which this view served before it declared anything -- Django answers
# HEAD by running the GET path and dropping the body. `require_safe` is
# the GET+HEAD pair, so the declaration excludes the unsafe verbs it was
# added for without newly refusing a request that used to work.
@require_safe
@stream_owner_required
def workstream_settings(request, principal, stream):
    """GET `/chat/w/<pk>/settings/` -- Manage, Instructions, Scope,
    Upload default, Tags, Shared with: everything the owner feedback
    named "not often updated," split off `chat/workstream.html` into its
    own page ("we dont need to see all the items when we go to a
    workstream").

    CLASS O, THE HOUSE 404 RULE -- deliberately NOT `chat-workstream`'s
    own `stream_access`/403 exception. Every section this page hosts is
    already owner-only or owner-relevant, so a recipient (a live share
    holder included, not only a dormant one) gets exactly the same 404 a
    stranger gets, the identical shape `workstream_edit`/`_scope`/
    `_share` already gate with (`@stream_owner_required`, above --
    CONSOLIDATION WAVE, audit B S16). The working page (`chat-
    workstream`) stays what a recipient sees; this one they never reach
    at all.
    """
    return render(request, "chat/workstream_settings.html",
                  _settings_context(request, principal, stream, is_owner=True))


@require_POST
@stream_owner_required
def workstream_edit(request, principal, stream):
    """POST `/chat/w/<pk>/edit/` -- one route, eight actions.

    Owner or `sees_all_content` only; a recipient gets 404, because these
    are row-addressed mutations of a row the recipient can see
    (`@stream_owner_required`, above -- CONSOLIDATION WAVE, audit B S16).

    SETTINGS-PAGE SPLIT: every one of these actions' own form now lives
    on `chat/workstream_settings.html`, not `chat/workstream.html` -- so
    a re-render on refusal, and a redirect on success, both retarget to
    `chat-workstream-settings`. The one exception is "delete"'s own
    SUCCESS path, unchanged: the row is gone, so there is no settings
    page left to land on, and `chat-workstreams` (the list) is still the
    only sane destination.
    """
    action = request.POST.get("action", "")
    if action not in _WORKSTREAM_ACTIONS:
        # ITEM 6 (Coherence Wave D): the shelled RE-RENDER shape this
        # view's OWN seven action branches already use for a refusal
        # (`error=...`, `status=400`, the settings page re-rendered
        # inside the shell), not a raw 400 -- matching THIS view's own
        # established convention beats forcing the flash-and-redirect
        # shape onto the one branch that would then be the odd one out
        # among its own siblings. Still never a bare plain-text page.
        return render(request, "chat/workstream_settings.html",
                      _settings_context(
                          request, principal, stream,
                          error=f"Unknown action. Expected one of: "
                                f"{', '.join(_WORKSTREAM_ACTIONS)}.",
                          is_owner=True),
                      status=400)
    if action == "delete":
        message = delete_workstream(principal, stream)
        if message:
            # E7: `is_owner=True` -- this handler's own guard just above
            # (`may_manage_workstream`) already settled the question for
            # this principal and this stream on this request.
            return render(request, "chat/workstream_settings.html",
                          _settings_context(request, principal, stream, error=message,
                                            is_owner=True),
                          status=400)
        return redirect("chat-workstreams")
    # F5 (walk-fix batch): every one of these five actions used to
    # complete silently -- the page just redirected back to itself with
    # nothing to show for the click. `chat/_messages.html` is already
    # included on the settings page for free (it renders whatever
    # `messages` carries, or nothing at all), so a flash on each
    # successful branch costs one line per action and no template
    # change.
    if action == "rename":
        if not rename_workstream(principal, stream, request.POST.get("name", "")):
            return render(request, "chat/workstream_settings.html",
                          _settings_context(request, principal, stream,
                                            error="That name is blank or already taken.",
                                            is_owner=True),
                          status=400)
        messages.success(request, "Renamed.")
    elif action == "description":
        set_workstream_description(principal, stream, request.POST.get("description", ""))
        messages.success(request, "Description saved.")
    elif action == "instructions":
        set_workstream_instructions(principal, stream,
                                    request.POST.get("instructions", ""))
        messages.success(request, "Instructions saved.")
    elif action == "upload_default":
        set_workstream_upload_default(principal, stream,
                                      request.POST.get("placement", ""))
        messages.success(request, "Upload default saved.")
    elif action == "include_universal":
        # CHECKBOX SEMANTICS: an unchecked `<input type="checkbox">`
        # sends NOTHING, never `"off"` or `"false"` -- the SAME "absence
        # means False" convention `workstream_scope`'s own entitlement
        # checkboxes already rely on, here applied to one box rather
        # than a set of them.
        set_workstream_include_universal(
            principal, stream, include_universal="include_universal" in request.POST)
        messages.success(request, "Document library setting saved.")
    else:
        set_workstream_archived(principal, stream, archived=(action == "archive"))
    return redirect("chat-workstream-settings", pk=stream.pk)


@require_POST
@stream_owner_required
def workstream_scope(request, principal, stream):
    """POST `/chat/w/<pk>/scope/` -- sets the wall to exactly the
    submitted entitlement ids.

    Each must be in `held_entitlement_ids(principal)` (spec §6.1), and
    `set_workstream_scope` is the SECOND gate: it answers `None` when
    any submitted id is outside `held_entitlement_ids`, and this view
    renders THAT as a message rather than a silent partial write. THE
    OWNERSHIP GATE is `@stream_owner_required` now (above --
    CONSOLIDATION WAVE, audit B S16): `set_workstream_scope` already
    checks `may_manage_workstream` internally, first, and returns
    `None` for a non-owner exactly as it does for an out-of-grant id,
    so a non-owner caller never wrote anything either way -- gating
    EAGERLY, before this function's own body runs at all, changes no
    observable behaviour. SETTINGS-PAGE SPLIT: the Scope form lives on
    `chat/workstream_settings.html` now, so both the refusal re-render
    and the success redirect retarget there.
    """
    raw = [i for i in request.POST.getlist("entitlements") if str(i).isdecimal()]
    result = set_workstream_scope(principal, stream, [int(i) for i in raw])
    if result is None:
        return render(request, "chat/workstream_settings.html",
                      _settings_context(request, principal, stream,
                                        error="A workstream's scope may only name "
                                              "entitlements you hold.",
                                        is_owner=True),
                      status=400)
    messages.success(request, "Scope saved.")
    return redirect("chat-workstream-settings", pk=stream.pk)


@require_POST
@stream_owner_required
def workstream_share(request, principal, stream):
    """POST `/chat/w/<pk>/share/`. Owner or `sees_all_content`; a
    recipient may not re-share and gets 404 (`@stream_owner_required`,
    above -- CONSOLIDATION WAVE, audit B S16). SETTINGS-PAGE SPLIT: the
    Shared-with list and its form live on `chat/workstream_settings.
    html` now, so every re-render and every success redirect below
    retarget there.
    """
    if request.POST.get("action") == "revoke":
        revoke_workstream_share(principal, stream, request.POST.get("share", ""))
        return redirect("chat-workstream-settings", pk=stream.pk)
    subject_user, subject_group = _share_subject(request)
    if subject_user is None and subject_group is None:
        return render(request, "chat/workstream_settings.html",
                      _settings_context(request, principal, stream,
                                        error="Pick an account or a group to share with.",
                                        is_owner=True),
                      status=400)
    result = share_workstream(principal, stream, user=subject_user,
                              group=subject_group, level=Share.Level.USE)
    if isinstance(result, ShareRefused):
        # GATE ONE'S REFUSAL RENDERS AS A MESSAGE, NOT A 404 (spec §14):
        # the sharer may share this stream, and what failed is the
        # recipient's grants -- which is actionable, and which the
        # message names under ruling B's default mode.
        return render(request, "chat/workstream_settings.html",
                      _settings_context(request, principal, stream, error=str(result),
                                        is_owner=True),
                      status=400)
    return redirect("chat-workstream-settings", pk=stream.pk)


@require_POST
@stream_owner_required
def workstream_consolidate(request, principal, stream):
    """POST `/chat/w/<pk>/consolidate/` -- distil one of this stream's
    conversations into its note.

    OWNER-ONLY, INCLUDING FOR A RECIPIENT'S OWN THREAD (ruling F, spec
    §23.F, `@stream_owner_required` above -- CONSOLIDATION WAVE, audit B
    S16). Consolidation writes a stream-contained `Document`, labelled
    from the stream's taint set and ingested on the owner's box -- a
    STREAM MUTATION, and it belongs beside re-sharing, wall edits and pin
    changes. Ruling C is what makes the OWNER able to consolidate a
    recipient's thread; it does not make the recipient able to
    consolidate anything.

    SIX REFUSALS (fix round 1, C-7 review added the sixth), and each has
    its own status because they are six different facts:
      - the caller may not mutate this stream -> 404, not 403, for the
        same reason every other row-addressed mutation is;
      - the conversation is not in this stream, or the caller may not
        read it -> 404 (the row-addressed rule; `visible_conversations`
        already carries ruling C's owner clause, so the owner reaches a
        recipient's thread here);
      - a consolidation for that conversation is already queued or
        running -> 409, the shape `agents.chat.service.start_turn`'s own
        in-flight refusal already uses;
      - the conversation has no completed turns to distil -> a flashed
        refusal and a redirect back to the workstream page, naming the
        reason (was a bare 400 until Coherence Wave D, item 6 -- see
        below, this refusal's own comment);
      - no chat model or no embedding model is bound -> 503, THE
        ENQUEUE-TIME PREFLIGHT `tools.rag.jobs.plan_consolidate`'s own
        module docstring requires: `models.queue.backend.enqueue` calls
        the planner SYNCHRONOUSLY and lets an unbound role's `ValueError`
        propagate straight out uncaught, correct only because every
        enqueue-time caller pre-checks first -- the same preflight
        `agents.runtime.jobs.plan_turn`'s own docstring requires of
        `agent.turn`'s caller;
      - this principal is already at their own queue quota -> 429, an
        honest `QueueQuotaExceeded` refusal (C-7, fix round 1) naming the
        quota -- never a 500, and never confused with the unmigrated-
        window 503 just above: the queue itself is fine, this caller has
        simply asked for too much of it.

    THE JOB KIND IS A STRING (`"rag.consolidate"`), never an import:
    `agents/` may not import `tools/` at all, and
    `models.contracts.queue.enqueue` validates the kind against the
    registry before dispatching -- the same dotted-string crossing every
    other cross-column reach in this phase uses.

    `Turn.objects` NEVER APPEARS HERE: `foundation/ops/tests/
    test_column_boundaries.py`'s gate does not cover `Turn`, but its
    spirit does, so the "has this conversation got a completed turn"
    question goes through `agents.visibility.latest_completed_turn_index`
    instead of a bare queryset in this module.
    """
    raw = request.POST.get("conversation", "")
    conversation = None
    if raw:
        try:
            uuid.UUID(raw)
        except (ValueError, AttributeError, TypeError):
            raw = None
        if raw:
            conversation = (visible_conversations(principal)
                            .filter(pk=raw, workstream_id=stream.pk).first())
    if conversation is None:
        raise Http404("No such conversation in this workstream.")

    if live_job_for("rag.consolidate", conversation=str(conversation.pk)) is not None:
        return HttpResponse(
            "This conversation is already being consolidated. Wait for that job to "
            "finish, or cancel it on the queue page.", status=409,
            content_type="text/plain; charset=utf-8")

    latest_index = latest_completed_turn_index(conversation)
    if latest_index is None:
        # ITEM 6 (Coherence Wave D): flash-and-redirect, not a raw 400.
        # This function's own docstring documents six refusals with
        # their own status codes for six different facts -- that
        # argument is about the CODE, not about the response having no
        # shell; the "Consolidate" button (`chat/workstream.html:138`)
        # is a plain zero-JS `<form>` on a full page, and "everything
        # works with no script at all" is exactly the promise a bare
        # plain-text 400 breaks for this refusal.
        messages.error(request, "This conversation has no completed turns to distil yet.")
        return redirect("chat-workstream", stream.pk)

    # THE ENQUEUE-TIME PREFLIGHT `plan_consolidate`'s own module docstring
    # requires: `models.queue.backend.enqueue` calls the planner
    # SYNCHRONOUSLY and lets a `ValueError` from an unbound role propagate
    # straight out uncaught (`tools/rag/jobs.py`'s module docstring names
    # this correct only because every enqueue-time caller pre-checks both
    # roles first) -- the same preflight `agents.runtime.jobs.plan_turn`'s
    # own docstring requires of `agent.turn`'s caller. Without it, a box
    # with no chat/embed model bound would 500 on this button instead of
    # answering the same honest, never-500 message `AskView`'s own
    # pre-check gives.
    #
    # THIS CHECKS BINDING EXISTENCE, NOT ENDPOINT HEALTH: `resolve()`
    # answers whether a role has a model bound to it at all, not whether
    # that model currently answers. A bound-but-unreachable model still
    # enqueues here and only fails later, on the worker -- visible on the
    # Queue page, not at this 503.
    try:
        resolve(CHAT_CONVERSE_ROLE)
        resolve(RAG_EMBED_ROLE)
    except ValueError:
        return HttpResponse(
            "Consolidation needs both a chat model and an embedding model set up first — "
            "see the model console.", status=503,
            content_type="text/plain; charset=utf-8")

    try:
        enqueue("rag.consolidate", {
            "conversation": str(conversation.pk),
            "workstream": stream.pk,
            "stream_name": stream.name,
            "title": conversation.title,
            # THE INDEX THIS RUN CONSOLIDATES THROUGH, resolved HERE
            # rather than in the handler: the staleness hint compares
            # against what the job actually read, and a handler that
            # re-read it would record an index later than the transcript
            # it distilled.
            "through_index": latest_index,
            **payload_fields(principal),
        })
    except QueueUnavailable:
        # THE UNMIGRATED-WINDOW CASE, the same shape `agents.chat.
        # service.start_turn`'s own catch answers: never a 500.
        return HttpResponse(QUEUE_UNAVAILABLE, status=503,
                            content_type="text/plain; charset=utf-8")
    except QueueQuotaExceeded as exc:
        # C-7 review, fix round 1: this route had no handler for this
        # exception at all -- it fell into no `except` here (there is no
        # broad `except Exception` on this view), so it would have
        # propagated to a 500. `agents.chat.views.workstreams` was held
        # by the settings-assistant peer at H39's own plan time; that
        # branch merged to main (PR #92) and the hold has lapsed, so
        # this file is touched here for the first time this round. 429,
        # the same honest-refusal shape `agents.chat.service.start_turn`'s
        # own `QueueQuotaExceeded` catch and `tools.rag.views.AskView`'s
        # answer already use -- the queue itself is fine, this caller has
        # simply asked for too much of it.
        return HttpResponse(str(exc), status=429,
                            content_type="text/plain; charset=utf-8")
    return redirect("chat-workstream", pk=stream.pk)


def _share_subject(request):
    """`(user, group)` from the POST body -- exactly one, or `(None, None)`.

    THE SAME SHAPE `chat-conversation-share` ALREADY PARSES, and the same
    two guards: `isdecimal()` before `int()`, because the value arrives
    from a POST body and a non-numeric one would otherwise reach the
    manager and raise on a never-500 surface (`agents.visibility.
    revoke_share`'s own docstring records why `isdigit()` is not enough
    -- it admits characters like "²" that `int()` itself rejects); and
    the subject is resolved through `identity.access.share_subjects`,
    which already excludes the caller, so a hand-posted pk cannot name an
    account this page would never have offered.

    Neither field, or both, answers `(None, None)`: the form makes both
    cases unrepresentable, and `share_workstream`'s own
    `bool(user) == bool(group)` guard refuses them again.
    """
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group

    offered = share_subjects(principal_for_request(request))
    raw_user = request.POST.get("user", "")
    raw_group = request.POST.get("group", "")
    if bool(raw_user) == bool(raw_group):
        return None, None
    if raw_user:
        if not raw_user.isdecimal():
            return None, None
        allowed = {pk for pk, _name in offered.get("users", ())}
        return (get_user_model().objects.filter(pk=int(raw_user)).first()
                if int(raw_user) in allowed else None), None
    if not raw_group.isdecimal():
        return None, None
    allowed = {pk for pk, _name in offered.get("groups", ())}
    return None, (Group.objects.filter(pk=int(raw_group)).first()
                  if int(raw_group) in allowed else None)
