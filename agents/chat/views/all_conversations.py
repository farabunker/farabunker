"""GET /chat/all/ -- every conversation this principal may read, in one
searchable, filterable table, with a server-rendered preview pane
(round 14, Part 2; owner feedback: "i also don't see a link to the
table list of all the chats, i thought I requested that").

WHY ITS OWN MODULE. `agents/chat/views/conversations.py` already owns
the conversation LIFECYCLE (start/rename/duplicate/archive/delete) and
`agents/chat/views/thread.py` owns reading ONE thread; this page reads
MANY at once, with its own query/filter/pagination/preview concerns
that belong beside neither -- the same "one file per subject" split
`turns.py`/`workstreams.py` already follow in this package.

IMPORTS `agents.chat.rendering.turn_card` DIRECTLY, not `agents.chat.
views.thread`/`turns.py` -- the package's own one-way import rule
(`views/__init__.py`'s docstring: `turns.py` -> `thread.py` -> nothing)
says nothing about a THIRD module reaching into either, but this
module reaches into NEITHER anyway: `rendering.py` is a shared LEAF
both of them already import (like `service.py`), so this stays a
sibling relationship, never a cycle.

CONSOLIDATION WAVE (audit A F3 / audit B S9) CORRECTS A PRIOR RULING
HERE: the per-turn attachment grouping `_preview_cards` below used to
build inline was described as "a DELIBERATE, SMALL duplicate of
`thread_context`'s own inline version... rather than a shared import"
-- reasoning that held for a CONSTANT (`views/__init__.py`'s own "a
constant reached by an import back up the chain is a cycle waiting for
its second caller") but does not extend to a FUNCTION with three
independent copies: the import-law direction this module's own
one-way rule describes is about `turns.py` reaching into `thread.py`
or vice versa, never about either reaching a SHARED LEAF neither of
them owns. `agents.attachments.attachments_for` is exactly that leaf
(the same role `rendering.py`/`service.py` already play), and all
three view modules now call it rather than three independently
maintained copies of the identical four-step grouping.

NO JAVASCRIPT IS REQUIRED. Search, every filter, and pagination are
all plain GET forms/links -- shareable URLs, the back button works,
and a JS-off reader gets a fully functional page either way. ROUND 16
adds one sanctioned, purely-additive `<script>` (a client-side text
filter over the Tags dropdown's own checkbox list, `chat/all.html`'s
own comments have the full reasoning) -- this is no longer a page with
literally zero `<script>` tags, but every filter/search/pagination
control remains a real GET link or GET form that needs no script to
work, which is the actual guarantee this page has always made.

CONTENT SEARCH INSIDE TURNS IS OUT OF SCOPE THIS ROUND (recorded
follow-up): `q=` matches `Conversation.title` only
(`title__icontains`), the same field the rail's own conversation rows
already show. Searching what was actually SAID would need either a
full-text index over `Turn.text` or a per-row `EXISTS` subquery this
page's own "bounded queries" rule does not budget for -- a real
feature, and its own round.
"""
from __future__ import annotations

import uuid

from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.http import urlencode
from django.views.generic import TemplateView

from agents.attachments import attachments_for
from agents.chat.rendering import turn_card
from agents.chat.sidebar import sidebar_context
from agents.models import ConversationTaint, Turn
from agents.visibility import chat_surface_conversations, visible_workstreams
from agents.workstreams import scope_for_conversation, tags_for_conversations
from identity.access import entitlement_names, held_entitlement_ids
from identity.request import principal_for_request, settings_row_for

# A page, not a paginator-free cap (contrast `sidebar.SIDEBAR_LIMIT`):
# this page IS the "see more" the rail's own capped list points at, so
# it has to keep going past 30 rather than stopping honestly-but-
# permanently there. 50, the brief's own number, matching `tools/rag/
# views.py::DOCUMENTS_PAGE_SIZE`'s own value for the identical "a
# single-operator box's own archive, not a public listing" reasoning.
PAGE_SIZE = 50

# The last N turns a preview pane shows (Part 2: "preview the most
# recent language/chat of the conversation we click"). A constant, not
# a magic `3` inline, so the template's own docstring and this
# module's tests can both name it once.
PREVIEW_TURN_COUNT = 3


class AllConversationsView(TemplateView):
    """The view itself is a thin wrapper: `_all_context` does the real
    work and is called a second time by nothing (unlike `thread_
    context`/`_index_context`, this page has no POST refusal to
    re-render from), but stays a free function anyway, matching this
    package's own "context builder is not a method" convention for
    every other multi-piece page.
    """

    template_name = "chat/all.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.update(_all_context(self.request))
        return context


def _filters(request, principal, settings_row) -> dict:
    """Resolve q/workstream/tag/archived into a filtered `rows`
    queryset plus the options/selections every filter control on the
    page needs to render itself -- FILTER RESOLUTION ONLY, no
    pagination and no URL-building (ROUND-14 REVIEW FIX, NIT 1: split
    out of `_all_context`, which used to do this, pagination, URL
    building and preview selection all in one ~130-line body -- "every
    piece is commented, but a split would let each be read, and
    TESTED, alone"). Returns a plain dict, this package's own "context
    builder returns a dict" convention throughout -- also carries `base`,
    the UNFILTERED queryset `_preview` needs (CONSOLIDATION WAVE, audit
    A F2), so `_all_context` can hand the same one to both without
    either resolving `visible_conversations` twice.
    """
    # CONSOLIDATION WAVE (audit B, S18): the context KEY is `showing_
    # archived`, not the bare `archived` this used to be -- a DIFFERENT
    # context key on `agents.chat.views.workstreams.workstream_list`
    # (`agents/chat/views/workstreams.py:151`) also used to be named
    # `archived` there for a LIST of workstream rows, so one name meant
    # a bool on this page and a list on that one. The `?archived=1` URL
    # PARAM name is unaffected -- only the internal/context vocabulary
    # renamed.
    showing_archived = request.GET.get("archived") == "1"
    q = request.GET.get("q", "").strip()

    # CONSOLIDATION WAVE (audit A, F2): `base` is the SAME unfiltered
    # queryset `_preview` below used to build a second time, fresh, just
    # to resolve `?selected=` -- `visible_conversations` pays its own
    # identity reads (`owned_rows_q`, `shared_keys`) at CALL time, so a
    # second call repeated all of them. Built ONCE here, handed back to
    # the caller (`_all_context`) to pass into `_preview`, and `.filter()`d
    # for THIS function's own `rows` -- a queryset clone costs nothing
    # the base call has not already paid for.
    base = chat_surface_conversations(principal, settings_row=settings_row) \
        .select_related("workstream", "agent")
    rows = base.filter(archived_at__isnull=not showing_archived)
    if q:
        rows = rows.filter(title__icontains=q)

    # WORKSTREAM FILTER. `visible_workstreams`, never a bare int cast
    # trusted straight into `.filter(workstream_id=...)` -- a hand-typed
    # id naming a stream this principal cannot even see would silently
    # narrow the list to "nothing" rather than 400ing (this is a GET
    # filter, not a gate: an unreachable value degrading to "no rows
    # match" is the honest, zero-JS-friendly answer a search form gives,
    # not a refusal).
    raw_workstream = request.GET.get("workstream", "")
    workstream_options = list(visible_workstreams(principal, settings_row=settings_row)
                              .order_by("name"))
    selected_workstream_id = int(raw_workstream) if raw_workstream.isdecimal() else None
    if selected_workstream_id is not None:
        if selected_workstream_id not in {w.pk for w in workstream_options}:
            selected_workstream_id = None
        else:
            rows = rows.filter(workstream_id=selected_workstream_id)

    # TAG FILTER, OFFERED FROM THE VIEWER'S OWN HOLDABLE SET ONLY (Part
    # 2's own words) -- `held_entitlement_ids` is what `tag_options`
    # below is built from, and a `tag=` value outside that set is
    # silently ignored (provenance/filter values are never a gate on
    # this platform -- `tools.rag.views.document_upload`'s own
    # placement-resolution comment states the identical rule for a
    # different field) rather than 400ing or, worse, actually filtering
    # by an id this viewer could not otherwise even learn the NAME of.
    #
    # ROUND 16 (owner feedback: "tags for this page should have a drop
    # down with checkboxes and search, not a shown list that is
    # selected") MAKES THIS MULTI-VALUE -- `getlist`, not `get`, and the
    # rule above generalises rather than relaxes: EVERY value in the
    # list is checked against `held` individually, and anything that
    # fails (non-numeric, unheld, or a plain duplicate -- the `set()`
    # below drops those for free) is dropped from the set silently,
    # never a 400, exactly the single-value branch's own behaviour
    # applied per-item instead of once.
    held = held_entitlement_ids(principal, settings_row=settings_row)
    tag_options = entitlement_names(held)
    selected_tag_ids = {
        int(raw) for raw in request.GET.getlist("tag")
        if raw.isdecimal() and int(raw) in held
    }
    if selected_tag_ids:
        # `entitlement_id__in=selected_tag_ids` against a table with ONE
        # ROW PER (conversation, entitlement) PAIR is already UNION/OR
        # across the selected set -- a conversation tainted by ANY one
        # of the checked tags has a matching row here, so this is the
        # same query shape as the old single-value `entitlement_id=`
        # equality, not a restructured one.
        rows = rows.filter(pk__in=ConversationTaint.objects.filter(
            entitlement_id__in=selected_tag_ids).values_list("conversation_id", flat=True))

    # `Meta.ordering = ["-updated_at"]` already sorts this way -- named
    # explicitly here anyway, matching the rail's own "most recent
    # first" convention, so a reader of THIS file does not have to go
    # find the model to know the order. HONEST LABEL, NOT A CLAIM THIS
    # PAGE DOES NOT MAKE GOOD ON: `updated_at` is the CONVERSATION
    # ROW'S own last write (creation, rename, archive) -- nothing on
    # this platform re-saves it when a later TURN is added (`agents.
    # chat.service.start_turn`'s own `_title_if_unset` is the one write
    # path, and it stops firing the moment a title exists) -- so
    # "Updated" is the honest column header, never "Last activity",
    # which would overclaim what this timestamp actually tracks
    # (`chat/all.html`'s own `title=` tooltip on that header, ROUND-14
    # REVIEW FIX MINOR 3, is what puts this truth on the RENDERED page
    # too, not only in this comment). Recorded as a follow-up, not
    # fixed here: a real per-turn last-activity column needs its own
    # write path this round does not open.
    rows = rows.order_by("-updated_at")

    return {
        "rows": rows,
        "base": base,
        "q": q,
        "showing_archived": showing_archived,
        "workstream_options": workstream_options,
        "selected_workstream_id": selected_workstream_id,
        "tag_options": tag_options,
        "selected_tag_ids": selected_tag_ids,
    }


def _preview(request, principal, settings_row, base):
    """Resolve `?selected=<uuid>` into `(conversation, preview_cards)`,
    or `(None, None)` when nothing valid was named -- PREVIEW-PANE
    SELECTION ONLY (ROUND-14 REVIEW FIX, NIT 1's own split, the second
    half; see `_filters` above for the first).

    THE PREVIEW PANE (Part 2: "Maybe on the new window we can preview
    the most recent language/chat of the conversation we click").
    `?selected=<uuid>` is looked up through `base` -- `_filters`' own
    UNFILTERED `visible_conversations` queryset (CONSOLIDATION WAVE,
    audit A F2: this used to call `visible_conversations` a second
    time, fresh, paying its identity reads twice on every `?selected=`
    render), never merely matched against the current page's own
    FILTERED/listed rows -- a shareable URL naming a row on page 3 must
    still preview correctly from a link that opens straight to page 1
    with no `page=` at all, and `base` carries no `archived_at` filter
    of its own, so a preview of an archived conversation reached from
    the archived list still resolves. A row this principal can no
    longer see (archived by someone else, deleted) must omit rather
    than KeyError.
    """
    raw_selected = request.GET.get("selected", "")
    if not raw_selected:
        return None, None
    # NEVER-500: `Conversation.id` is a UUID column, and Django's own
    # `UUIDField.get_prep_value` raises a bare `ValueError` for a
    # string that does not parse as one -- caught here, the same
    # "a malformed row-ish query value degrades to absent, never a
    # 500" rule `tools.rag.views` already applies to its own hand-
    # typed ids, before this value ever reaches the database.
    try:
        selected_id = uuid.UUID(raw_selected)
    except (ValueError, AttributeError, TypeError):
        return None, None
    selected = base.filter(pk=selected_id).first()
    if selected is None:
        return None, None
    # ROUND-13 REVIEW FIX, MINOR 2: this exact URL (search/filter/page/
    # `selected=` all still on it) is what a detach form in the preview
    # below returns to -- the SAME `request.get_full_path` idiom `chat/
    # _sidebar.html`'s own rename form already carries as `next`, threaded
    # here instead of read fresh in the template so `_preview_cards`
    # (below) can hand it straight to `turn_card`'s own new keyword.
    cards = _preview_cards(selected, principal, settings_row=settings_row,
                           next_url=request.get_full_path())
    return selected, cards


def _all_context(request) -> dict:
    principal = principal_for_request(request)
    settings_row = settings_row_for(request)

    resolved = _filters(request, principal, settings_row)
    rows = resolved["rows"]
    base = resolved["base"]
    q = resolved["q"]
    showing_archived = resolved["showing_archived"]
    workstream_options = resolved["workstream_options"]
    selected_workstream_id = resolved["selected_workstream_id"]
    tag_options = resolved["tag_options"]
    selected_tag_ids = resolved["selected_tag_ids"]

    # ROUND-14 REVIEW FIX, MINOR 1: `paginator.count`, never a SEPARATE
    # `rows.count()` first -- `Paginator.get_page()` already runs (and
    # caches) its own `COUNT(*)` internally (`validate_number` reads
    # `self.num_pages`, which reads `self.count`), so a standalone
    # `rows.count()` before it was a second, identical query for a
    # number the paginator was about to compute anyway. Read AFTER
    # `get_page()` so it reuses that cached value rather than running
    # its own.
    paginator = Paginator(rows, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    total = paginator.count
    listed = list(page_obj.object_list)

    tags_by_conv = tags_for_conversations(listed, principal, settings_row=settings_row)

    selected, preview_cards = _preview(request, principal, settings_row, base)

    # THE QUERY STRING EVERY LINK/FORM ON THIS PAGE PRESERVES -- q/
    # workstream/tag/archived, never `page` or `selected` (each control
    # that changes one of THOSE sets its own value). The same "preserve
    # params, add page" idiom `tools/rag/views.py::_page_url` already
    # uses for the library's own pagination.
    preserved = {}
    if q:
        preserved["q"] = q
    if selected_workstream_id is not None:
        preserved["workstream"] = selected_workstream_id
    if selected_tag_ids:
        # ROUND 16: `tag` is now a LIST value in `preserved` -- sorted so
        # the same selection always renders the same query string (a
        # `set`'s own iteration order is not guaranteed stable across
        # runs, and a shareable URL that reordered itself on every
        # render would fail `TestZeroJsAndShareableUrls`'s own "the same
        # url renders the identical content" claim once byte-for-byte
        # URL comparison is in play, not merely parsed-query equality).
        preserved["tag"] = sorted(selected_tag_ids)
    if showing_archived:
        preserved["archived"] = "1"

    base_url = reverse("chat-all")

    def _url(**overrides) -> str:
        """`base_url` plus `preserved` plus `overrides`, `None`-valued
        keys dropped -- the one URL-builder every filter/pagination/
        selection link and form on this page shares, so "preserve
        everything except what THIS control changes" is written once.

        `doseq=True` (ROUND 16, added for the new list-valued `tag`):
        confirmed safe for every OTHER preserved value here, which are
        all plain ints/strings -- Django's own `urlencode` falls back to
        ordinary single-param encoding for anything that is not actually
        iterable (a bare `try: iter(value) except TypeError` inside it),
        so this flips on list support for `tag` without changing how
        `q`/`workstream`/`archived`/`page`/`selected` -- every scalar
        value this closure has ever built a query string from -- render.
        """
        params = {**preserved, **overrides}
        params = {k: v for k, v in params.items() if v is not None}
        return f"{base_url}?{urlencode(params, doseq=True)}" if params else base_url

    page_obj.prev_url = (
        _url(page=page_obj.previous_page_number()) if page_obj.has_previous() else None
    )
    page_obj.next_url = (
        _url(page=page_obj.next_page_number()) if page_obj.has_next() else None
    )
    # THE PAGE-LEVEL "clear this filter" LINKS -- each drops exactly the
    # one key it names (never `page`, which a filter CHANGE should reset
    # anyway, the ordinary "changing a filter starts back at page 1"
    # rule every search UI keeps) by passing `None` for it, which `_url`
    # above filters out of the query string entirely.
    clear_search_url = _url(q=None) if q else None
    workstream_filter_urls = [
        {"workstream": w, "url": _url(workstream=w.pk),
         "active": w.pk == selected_workstream_id}
        for w in workstream_options
    ]
    all_workstreams_url = _url(workstream=None)
    # ROUND 16: the tag filter is now ONE dropdown with a checkbox per
    # offered tag (`chat/all.html`'s own `<details class="chat-menu">`),
    # not a row of per-tag toggle links -- each row needs only its own
    # `checked` state for the round-trip (owner: "a shown list that is
    # selected" is exactly the per-tag-link UI this replaces), never a
    # per-tag URL, since checking a box changes nothing until "Apply"
    # submits the whole form.
    tag_checkboxes = [
        {"id": tag_id, "name": name, "checked": tag_id in selected_tag_ids}
        for tag_id, name in tag_options
    ]
    all_tags_url = _url(tag=None)
    archived_toggle_url = _url(archived=None if showing_archived else "1")

    # `selected`/`preview_cards` were already resolved above, by
    # `_preview()` -- ROUND-14 REVIEW FIX, NIT 1's own split -- before
    # `_url` (which `select_url`, right below, needs) even existed.

    # THE CURRENT PAGE, NAMED EXPLICITLY (not folded into `preserved`):
    # selecting a row must not silently bounce back to page 1 -- a
    # search result opened three pages in stays on that page while its
    # preview pane opens beside it.
    current_page = page_obj.number if page_obj.number != 1 else None

    return {
        "rows": [
            {"conversation": c, "tags": tags_by_conv.get(c.pk, {"named": (), "unnamed_count": 0}),
             "selected": selected is not None and c.pk == selected.pk,
             "select_url": _url(page=current_page, selected=str(c.pk))}
            for c in listed
        ],
        "page_obj": page_obj,
        "total": total,
        "q": q,
        "clear_search_url": clear_search_url,
        "showing_archived": showing_archived,
        "archived_toggle_url": archived_toggle_url,
        "workstream_options": workstream_options,
        "workstream_filter_urls": workstream_filter_urls,
        "all_workstreams_url": all_workstreams_url,
        "selected_workstream_id": selected_workstream_id,
        "tag_options": tag_options,
        "tag_checkboxes": tag_checkboxes,
        "all_tags_url": all_tags_url,
        "selected_tag_ids": selected_tag_ids,
        "selected_tag_count": len(selected_tag_ids),
        # ROUND 17: pre-formatted for `chat/_search_checklist.html`'s
        # own `sc_summary_text` parameter, which takes a plain value,
        # never a template expression -- `{% templatetag openblock %}
        # include %} with %}` cannot itself spell "Tags" vs "Tags · N".
        "tags_summary_text": (
            f"Tags · {len(selected_tag_ids)}" if selected_tag_ids else "Tags"
        ),
        "selected": selected,
        "preview_cards": preview_cards,
        "preview_turn_count": PREVIEW_TURN_COUNT,
        **sidebar_context(principal, settings_row=settings_row),
    }


def _preview_cards(conversation, principal, *, settings_row=None, next_url=None) -> list:
    """The LAST `PREVIEW_TURN_COUNT` root-level (`depth=0`), NON-TOOL
    turns of `conversation`, as cards. Reuses `agents.chat.rendering.
    turn_card` (the SAME per-turn builder `chat/_turn_card.html`
    already renders for a full thread AND for the poller's own
    fragments), so a preview card can never drift from what opening the
    thread in full would show for that same turn.

    CONSOLIDATION WAVE (audit A F3 / audit B S9, plus the S10 ruling):
    this used to return `{"cards": [...], "legacy_attachments": [...]}`
    -- the SAME `turn_id IS NULL` legacy bucket `thread_context` built,
    off the SAME `attached_documents` call, since ROUND-13 REVIEW FIX,
    MINOR 3. The whole legacy-attachment renderer was removed as
    production-unreachable (the consolidation wave's own S10 ruling:
    no migration on `origin/main` ever let a `NULL turn_id` row exist
    outside this branch's own history), so there is no second bucket
    left to return -- this function now returns the card list directly,
    and its own attachment lookup goes through `agents.attachments.
    attachments_for` rather than grouping inline.

    `next_url`, OPTIONAL (ROUND-13 REVIEW FIX, MINOR 2): threaded
    straight through to `turn_card`'s own `attachment_detach_next`
    keyword for every card built here, so a detach form inside the
    preview pane returns to `next_url` instead of the thread page --
    see that keyword's own docstring for the full reasoning. `None`
    (no caller before this fix) keeps every OTHER `turn_card` call site
    behaving exactly as before this parameter existed.

    ROUND-14 REVIEW FIX, NIT 2: "PREVIEW_TURN_COUNT slices depth=0
    turns only, so a preview of an exchange whose last three root turns
    are USER/TOOL-free renders no assistant answer at all in some
    shapes ... 'the last 3 turns' in the brief reads as 'the last
    exchange' to a user." A top-level agent's own tool calls ARE
    depth=0 rows (`agents.runtime.loop`'s own `Turn.objects.create(...,
    depth=depth, ...)` for a `TOOL` turn passes through the CALLING
    depth unchanged -- only a DELEGATED agent's turns land at depth>0),
    so an exchange with several tool calls could crowd the slice with
    audit-trail rows and leave no room for the actual answer.
    `.exclude(role=Turn.Role.TOOL)` is the fix: the preview pane's own
    job (Part 2's brief, verbatim: "preview the most recent language/
    chat") is conversational text, never the tool-call bookkeeping a
    TOOL card renders -- excluding it from the SLICE (not merely from
    the RENDER) is what guarantees "the last 3" always means the last
    3 USER/ASSISTANT turns, the shape a reader's own "last exchange"
    expectation actually describes.

    `attachments_by_turn`, the round-13 chip data, threaded through the
    identical way `agents.chat.views.thread.thread_context` and
    `agents.chat.views.turns._attachments_by_turn` already build it --
    "round 13's chips render in previews" is this round's own explicit
    requirement, and reusing `turn_card`'s own `attachments=` keyword is
    what delivers it for free rather than needing a second rendering
    path.
    """
    turns = list(
        conversation.turns.filter(depth=0).exclude(role=Turn.Role.TOOL)
        .order_by("-index")[:PREVIEW_TURN_COUNT]
    )[::-1]
    stream_scope = scope_for_conversation(principal, conversation)
    by_turn = attachments_for(principal, conversation, stream=stream_scope,
                              settings_row=settings_row)
    return [turn_card(t, attachments=by_turn.get(t.pk),
                      attachment_detach_next=next_url) for t in turns]
