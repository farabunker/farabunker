"""
RAG module HTTP surface (WAVE 3; T6: async Ask).

This is the module's `ui.mount: /rag` self-describing UI surface
(docs/ARCHITECTURE.md §5): a tiny HTML page (AskPageView) plus two JSON
endpoints -- `AskView` (enqueues a `rag.ask` job against the execution
queue, models/contracts/queue.py, and returns 202) and `AskJobStatusView`
(the page's poll target, surfacing that job's queued/running/finished
status; C-55: both are plain functions now, not DRF `APIView` classes --
see each one's own docstring for why the names stayed). ADR 0004 noted
query responses want streaming/async eventually; T6 (the execution-queue
build) is that move -- there is now exactly ONE way a question gets
answered anywhere in the platform (through the queue), the prior
synchronous "call answer_question and return its result inline" path is
gone.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Count, Q
from django.http import (
    FileResponse, Http404, HttpResponse, HttpResponseForbidden,
    JsonResponse, StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from models.registry.bindings import (
    MODEL_FORBIDDEN_MESSAGE, model_access_for, picker_options, resolve_connection_named,
)
from foundation.files import create_owner_only_dir
from foundation.format import bytes_to_gb, gb_to_bytes, human_bytes
from foundation.http import mark_private
from foundation.settings_area import settings_redirect
from foundation.settings_bounds import (
    BIGINT_FIELD_MAX, POSITIVE_INT_FIELD_MAX, exceeds_field_ceiling,
)
from identity import audit
from identity.access import is_admin, labelling_entitlements, owned_entitlement_ids, sees_all_content
from identity.contracts import actions
from identity.contracts.principals import payload_fields
from identity.request import (
    principal_for_request,
    # ALIASED, because this module owns a `settings_row_for` of its
    # own for `RagSettings` (below) and the two answer different
    # questions. Identity's reads the row `IdentityGateMiddleware`
    # has already fetched and stashed, so threading it into
    # `labelling_entitlements` costs no query at all.
    settings_row_for as identity_settings_row_for,
    user_for_request,
)
from models.contracts.bindings import ResolvedModel, resolve
from models.contracts.queue import (
    QueueQuotaExceeded, QueueUnavailable, enqueue, get_job, resolve_client_priority,
)
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE
from models.queue.visibility import may_read_job_content, may_see_job_id, visible_jobs as visible_queue_jobs
from tools.rag import index as rag_index
from tools.rag import ingest, retrieval, services, store
from tools.rag.access import (
    document_visibility, listable_documents, may_administer_document, may_label_document,
    readable_document,
    visible_ask_records,
)
from tools.rag.categories import normalize_category_name
from tools.rag.labels import document_label_ids, set_document_labels
from tools.rag.messages import (
    PRECHECK_ROLES,
    SEARCH_FAILED_MESSAGE,
    SEARCH_NO_RESULTS_MESSAGE,
    UNBOUND,
    UNREACHABLE,
    model_unavailable_message,
    search_score_floor_message,
    unreachable_endpoints,
)
from tools.rag.retrieval import _search_result_for
from tools.rag.models import UNCATEGORIZED, Category, Document, RagSettings
from tools.rag.sidecar import display_segments, read_sidecar

logger = logging.getLogger(__name__)

# Extensions that should render inline as plain text rather than being
# downloaded or misidentified by content-type sniffing.
_TEXT_LIKE_EXTENSIONS = {".md", ".txt", ".csv"}

# Documents shown per page in the library view (DocumentsView).
DOCUMENTS_PAGE_SIZE = 25

# The two placement values, as plain strings. NOT imported from
# `agents.models.Workstream.UploadPlacement`: `tools/rag` may reach
# `agents` only through the three named seams, and a choices enum on a
# model is not one of them. Two literals beside the one view that reads
# them, pinned equal to the model's own values by
# `tools/rag/tests/test_upload_placement.py`.
#
# THE WORKSTREAM DOCUMENTS PANEL'S OWN SET -- the library/workstream
# doors' own two values (round 12, owner ruling: "The workstream
# Documents panel and library doors: UNCHANGED -- they are the
# 'supporting document' path"). RETIRED, round 13: the chat door's own
# WIDER set (a third, "conversation" value, "This chat only") used to
# live here too (`_CHAT_PLACEMENTS_IN_STREAM`/`_LOOSE`) -- gone along
# with the rest of this view's chat-door handling (see `document_
# upload`'s own docstring); its replacement lives in `agents.contracts.
# attachments.CHAT_PLACEMENTS_IN_STREAM`/`_LOOSE`, a pure leaf both
# `agents.chat.service.start_turn` and `tools.rag.services.stage_turn_
# attachments` read, neither of which is this view.
_PLACEMENTS = ("universal", "contained")


def settings_row_for(request):
    """The `RagSettings` row for `request` -- fetched on the first ask and
    stashed on the request, answered from that stash every ask after.

    THE FOURTH SPELLING OF "read the singleton once per request" (S6,
    settings-backend audit), and deliberately the SAME NAME as the third:
    `identity.request.settings_row_for`. That function is the pattern this
    one copies rather than a pattern this one invents, and a reader who
    knows one knows the other.

    ONE DIFFERENCE, which is about where the row comes from and not about
    what a caller gets. `IdentityGateMiddleware` has already read the
    identity row and stashed it on the request by the time any view asks,
    so identity's helper reads the stash and falls back to a fetch.
    Nothing stashes `RagSettings` -- there is no rag middleware and there
    should not be one for this -- so this helper does the first fetch
    itself and stashes the result. Both answer the same question: "the
    row for THIS request", named once instead of re-derived at each call
    site.

    REQUEST-SCOPED, NEVER PROCESS-SCOPED, and that is the whole safety
    argument. Every write on the settings page reads the row, mutates it
    and saves it inside ONE request, so a per-request memo can only ever
    hand back the object that request is already working with. A module-
    level cache would hand the NEXT request the values from before the
    operator's own save -- the failure this shape cannot have.

    The seven `views.py` call sites this replaces are the ones a request
    reaches THROUGH THIS MODULE. `ingest.py`, `media.py`, `index.py` and
    `services.py`'s prune read keep their own `get_solo()`: they run on
    the watcher and inside job handlers, where there is no request to
    scope anything to. ONE untouched site is NOT of that kind, and is
    named rather than generalised over (closing-wave re-verify, C1):
    `services.stage_turn_attachments` (`services.py:639`) reads the row
    on a REQUEST path -- `agents.chat.service.start_turn` calls it from
    three chat POST handlers. It stays un-threaded for a different
    reason: reaching it would mean putting a settings row through
    `start_turn`'s cross-column signature, which is a design change
    rather than the query-count change this helper is.
    Pinned by `tools/rag/tests/test_views_retrieval_settings_and_gating.py::
    TestTheSingleSettingsReadPerRequest`.
    """
    row = getattr(request, "rag_settings_row", None)
    if row is None:
        row = RagSettings.get_solo()
        request.rag_settings_row = row
    return row


def _entitlement_filter(request, choices):
    """The library's `?entitlement=` filter, as plain data for the view
    and the banner above the list.

    `active`  -- the parameter was supplied at all.
    `id`      -- the integer to filter on, or `None` for a value that is
                 not an entitlement id. `None` means "match nothing",
                 never "match everything": a filter that silently showed
                 the whole library back would tell an operator who typed
                 a wrong id that the entitlement covers four hundred
                 documents.
    `name`    -- what to call it in the banner, or `""`.
    `params`  -- what every other link on the page must carry to keep
                 this filter on.

    THE NAME COMES FROM `labelling_entitlements`, which is the predicate
    this page ALREADY renders entitlement names from (the "With
    selected..." card), and nothing wider. An id this viewer may not
    label with is still filtered on -- narrowing is always safe -- but it
    is not NAMED, so the banner cannot become a way to read the
    entitlement catalogue off a page that never offered it. The link
    that brings anybody here is on their own entitlement's page, and
    reaching that page already means owning or administering it.
    """
    raw = request.GET.get("entitlement", "").strip()
    if not raw:
        return {"active": False, "id": None, "name": "", "raw": "", "params": {}}
    pk = int(raw) if raw.isdecimal() else None
    # `choices` IS PASSED IN, not re-read (fix round 2, P2-M3). This page
    # already asks `labelling_entitlements` for its own "With
    # selected..." card, and asking a second time for the same principal
    # in the same request cost three extra queries -- including a twelfth
    # `identitysettings` read where the unfiltered page has eleven. The
    # per-request-reuse norm `identity.access.sees_all_content`'s
    # docstring states, applied here.
    names = dict(choices)
    return {
        "active": True,
        "id": pk,
        "name": names.get(pk, ""),
        "raw": raw,
        "params": {"entitlement": raw},
    }


def _sidebar_entry(
    name, count, *, base_url, query, category_param, category_value=None,
    category_id=None, extra=None,
):
    """Build one sidebar shelf entry for the document library.

    `category_value=None` is the special "All" shelf: it links to the bare
    library URL and is active only when neither a search nor a category
    filter is applied. Otherwise the entry links to `?category=<value>` and
    is active when that value is the current filter (and no search is active
    -- searching spans all categories, so no shelf is highlighted then).

    `category_id` is the `Category.pk` for real categories (None for the
    synthetic "All" / "Uncategorized" shelves); `manageable` tells the
    template whether to render the rename/delete controls for this shelf.
    """
    # `extra` IS EVERY OTHER FILTER ALREADY ON THE LIBRARY, carried
    # through so that clicking a shelf narrows what is on screen rather
    # than silently dropping the entitlement filter the operator arrived
    # with (phase 2). It is a dict, not a string, so the one `urlencode`
    # below stays the only place a query is spelled.
    params = dict(extra or {})
    if category_value is None:
        url = f"{base_url}?{urlencode(params)}" if params else base_url
        active = not query and not category_param
    else:
        url = f"{base_url}?{urlencode({**params, 'category': category_value})}"
        active = not query and category_param == category_value
    return {
        "name": name,
        "count": count,
        "url": url,
        "active": active,
        "id": category_id,
        "manageable": category_id is not None,
    }


def _page_url(base_url, preserve_params, page_number):
    """A library URL for `page_number` that preserves the active q/category."""
    params = {**preserve_params, "page": page_number}
    return f"{base_url}?{urlencode(params)}"


def _connection_picker_options(principal) -> list[dict] | None:
    """The Ask form's `Model` `<select>` options, in picker order, or
    `None` when there's nothing to pick from.

    A thin wrapper over `models.registry.bindings.picker_options`
    (final-review finding 8 -- lifted out of this function and its
    near-identical `tools.vision.views` copy, which the same helper
    now backs), fixed to the chat capability and `rag.answer`. See that
    function's own docstring for the full option-building rule (labels,
    the primary/env-override preselection, the `None`-when-empty case).
    `principal` is REQUIRED -- a connection this principal may not use is
    dropped from the options (`models.registry.access.ModelAccess`, IA-2
    T14).
    """
    return picker_options("chat", RAG_ANSWER_ROLE, access=model_access_for(principal))


class AskPageView(TemplateView):
    """GET /rag/ -- renders the minimal ask-a-question page."""

    template_name = "rag/ask.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["categories"] = Category.objects.all()
        # W2 (ADR 0014 §18): a plain count, never a query-time join into
        # retrieval itself (ADR 0005's tabular rows/schema are never
        # embedded, so this has no bearing on what a question actually
        # searches) -- server-rendered, ONLY when the library holds at
        # least one tabular Document, so an install with none never shows
        # a note about a shape of document it doesn't have. `status=READY`
        # (W2/W6 review MINOR 5): a FAILED or still-PENDING tabular row was
        # never actually stored -- counting it here would tell an operator
        # "N rows are stored but not searchable" when N includes rows that
        # aren't stored at all yet (or ever, if ingest failed).
        principal = principal_for_request(self.request)
        context["connection_options"] = _connection_picker_options(principal)
        context["tabular_count"] = listable_documents(principal).filter(
            doc_type=Document.DocType.TABULAR, status=Document.Status.READY).count()
        return context


class DocumentsView(TemplateView):
    """GET /rag/documents/ -- the document library: a sidebar for browsing by
    category and a paginated, searchable main list (ADR 0009 / ADR
    0009-followup library UI contract).

    Server-rendered and query-param driven so it scales to large libraries
    without client-side JS: `category`, `q`, and `page` are all plain GET
    params, so every state (a category, a search, a page) is a bookmarkable/
    shareable URL and works with JS disabled.

    Query params:
    - `category`: sidebar filter -- a `Category.name`, or the literal
      "Uncategorized" for `Document.category IS NULL`. Ignored whenever `q`
      is also given (search always searches every category).
    - `q`: case-insensitive title search (`title__icontains`) across ALL
      documents, regardless of `category`.
    - `page`: 1-indexed page number into the current result set, page size
      `DOCUMENTS_PAGE_SIZE`.
    """

    template_name = "rag/documents.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        request = self.request
        query = request.GET.get("q", "").strip()
        category_param = request.GET.get("category", "").strip()

        principal = principal_for_request(request)
        # ONE `labelling_entitlements` READ FOR THE WHOLE REQUEST, off
        # the identity row the gate middleware has already fetched. Two
        # readers need it: the banner's name lookup just below, and the
        # "With selected..." card's own `label_choices` further down.
        choices = labelling_entitlements(
            principal, settings_row=identity_settings_row_for(request))
        # THE LISTING'S OWN FUNCTION, for the rows AND for every count
        # above them, so the two can never disagree. A sidebar that says
        # "Finance (14)" to a member who may open none of them is a leak
        # of exactly the fact the labels exist to hide, and it is the
        # easiest one to forget because it renders no document.
        visible = listable_documents(principal)
        # THE ENTITLEMENT FILTER COMPOSES, IT NEVER WIDENS (phase 2).
        # It narrows the queryset `listable_documents` already returned,
        # so a `?entitlement=` naming something this viewer may not see
        # answers the empty honest state rather than leaking a row --
        # the filter cannot reach a document the viewer could not have
        # listed without it.
        #
        # APPLIED BEFORE EVERY COUNT, for the reason stated just above
        # about the sidebar: the shelf counts and the rows come off ONE
        # queryset, so a shelf can never advertise a document the list
        # will not show.
        entitlement_filter = _entitlement_filter(request, choices)
        if entitlement_filter["active"]:
            visible = (visible.filter(
                entitlement_labels__entitlement_id=entitlement_filter["id"])
                if entitlement_filter["id"] is not None else visible.none())
        context["entitlement_filter"] = entitlement_filter
        total_count = visible.count()
        uncategorized_count = visible.filter(category__isnull=True).count()
        # TWO CATEGORY LISTS, and the split is load-bearing.
        #
        # `sidebar_categories` is a READING surface: its counts must come
        # off `visible`, because a sidebar that says "Finance (14)" to a
        # member who may open none of them leaks exactly the fact the
        # labels exist to hide -- and it is the easiest leak to forget,
        # because it renders no document.
        #
        # `context["categories"]` stays EVERY category, unchanged from
        # today, because it is also the upload form's own
        # `<select name="category">` (`documents.html:456-460`) -- a
        # WRITE-side control. Narrowing it would drop every empty category
        # from that picker for everyone, on an `open` box included, and
        # remove an existing capability (uploading into an existing empty
        # category) that no rule asks to remove.
        sidebar_categories = (
            Category.objects.annotate(
                doc_count=Count("documents", filter=Q(documents__in=visible), distinct=True))
            .filter(doc_count__gt=0)
            .order_by("name")
        )
        categories = Category.objects.all().order_by("name")

        # -- Sidebar: All / each Category (with doc counts) / Uncategorized.
        base_url = reverse("rag-documents")
        entry_kwargs = {"base_url": base_url, "query": query,
                        "category_param": category_param,
                        "extra": entitlement_filter["params"]}
        sidebar = [_sidebar_entry("All", total_count, **entry_kwargs)]
        sidebar += [
            _sidebar_entry(category.name, category.doc_count, category_value=category.name,
                           category_id=category.id, **entry_kwargs)
            for category in sidebar_categories
        ]
        sidebar.append(
            _sidebar_entry(
                UNCATEGORIZED, uncategorized_count, category_value=UNCATEGORIZED, **entry_kwargs
            )
        )

        # -- Main list: search (all categories) > category filter > all docs.
        show_category_column = True
        if query:
            documents = visible.filter(title__icontains=query)
        elif category_param == UNCATEGORIZED:
            documents = visible.filter(category__isnull=True)
            show_category_column = False
        elif category_param:
            documents = visible.filter(category__name=category_param)
            show_category_column = False
        else:
            documents = visible

        documents = documents.select_related("category", "workstream").order_by("title")

        paginator = Paginator(documents, DOCUMENTS_PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page"))
        # AFTER pagination, over the page's own rows: one prefetch, no
        # query per row, and `Paginator` still counts and slices a plain
        # `Document` queryset exactly as it does today.
        page_obj.object_list = page_obj.object_list.prefetch_related(
            "entitlement_labels__entitlement")
        context["label_choices"] = choices
        # ONE ACTION, NOT N (spec section 22.34), and since fix round 2
        # ONE WRITER: the "With selected…" card above the table is the
        # only control on this page that changes a label, and it renders
        # only when this is True -- a member who owns no entitlement sees
        # no writing surface at all, and the Labels column is read-only
        # chips for everybody. `bool(...)` over the same tuple
        # `label_choices` already holds: no second query, no second
        # predicate.
        context["may_label_any"] = bool(context["label_choices"])
        # ONE VALUE, THREADED DOWN (whole-branch review item 5): the
        # Download/transcript links below (`documents.html:535-538`) route
        # through `readable_document`, which 404s for exactly the same
        # administrator-with-content-off case `listable_documents` above
        # already widens past for the ROW. `visibility.permits(document)`
        # answers that per row with NO extra query -- it reads the same
        # prefetched `entitlement_labels` the labels column already
        # forces -- rather than a second `readable_documents(principal)`
        # filter call per row.
        visibility = document_visibility(principal)
        # HOISTED OUT OF THE ROW LOOP (whole-branch review finding 6):
        # both are per-PRINCIPAL facts, identical on every row of this
        # render, so computing them inside `may_label_document` per row
        # paid two extra identity queries per row for nothing -- on top
        # of that function's own `.filter(...).exists()`, which bypassed
        # the `prefetch_related("entitlement_labels__entitlement")`
        # above entirely. Passed in below so the per-row call costs no
        # query at all, the same hoist `visibility` above already gets.
        is_admin_flag = is_admin(principal)
        owned_ids = owned_entitlement_ids(principal)
        context["rows"] = [
            {
                "document": document,
                "labels": [label.entitlement.name
                           for label in document.entitlement_labels.all()],
                # NO `label_ids` since fix round 2: its one reader was the
                # per-row `<select multiple>`'s `selected` attribute, and
                # that control is gone. The names above are all a
                # read-only chip needs.
                "may_label": may_label_document(principal, document,
                                                is_admin_=is_admin_flag, owned=owned_ids),
                # ROUND 12 WHOLE-BRANCH REVIEW A-2/B-2: a SEPARATE key
                # from `may_label` above -- `may_administer_document` is
                # WIDER (it also admits a chat-scoped document's own
                # uploader), and the two questions are genuinely
                # different ("may write a label" vs "may delete/
                # re-ingest this row"). Reusing `may_label` for the
                # Delete/Re-ingest section would have rendered those
                # buttons render-vs-gate consistent with the OLD,
                # narrower gate rather than the one `document_delete`/
                # `document_reingest` actually enforce now.
                "may_administer": may_administer_document(
                    principal, document, is_admin_=is_admin_flag, owned=owned_ids),
                "readable": visibility.permits(document),
            }
            for document in page_obj.object_list
        ]

        # -- Prev/Next URLs, built in Python (not the template) so the
        # querystring convention lives in one place. Each preserves the
        # active q/category (page number excluded) so paging never loses the
        # other state; None when there's no page in that direction.
        preserve_params = {}
        if query:
            preserve_params["q"] = query
        if category_param:
            preserve_params["category"] = category_param
        page_obj.prev_url = (
            _page_url(base_url, preserve_params, page_obj.previous_page_number())
            if page_obj.has_previous()
            else None
        )
        page_obj.next_url = (
            _page_url(base_url, preserve_params, page_obj.next_page_number())
            if page_obj.has_next()
            else None
        )

        # -- "Clear search" link: drop `q` but keep the current `category`
        # (if any), so clearing a search returns to the shelf you were on.
        clear_search_url = (
            f"{base_url}?{urlencode({'category': category_param})}" if category_param else base_url
        )

        context.update(
            {
                "sidebar": sidebar,
                "page_obj": page_obj,
                "query": query,
                "category_param": category_param,
                "show_category_column": show_category_column,
                "clear_search_url": clear_search_url,
                "categories": categories,
                "may_upload": _may_upload(principal),
                # THE SAME SENTENCE the POST refusal answers with,
                # threaded through so `documents.html`'s own
                # `{% else %}` never restates a second spelling of it
                # (finding 6, T13 review).
                "upload_forbidden_message": _UPLOAD_FORBIDDEN_MESSAGE,
            }
        )
        # `?pin_into=<pk>` (spec §14, author decision 25). A BANNER
        # naming the stream and a Pin control per universal readable row
        # -- not a second page. Resolved through the seam, so a stream
        # this caller may not reach renders nothing at all rather than a
        # banner they cannot act on. `pin_target` answers BOTH the scope
        # and the display name off ONE row read (final-polish round: this
        # used to be `workstream_scope` plus a second, separate
        # `workstream_name` call, each resolving the identical row for
        # itself); `WorkstreamScope` itself still carries no display
        # field (`agents.workstreams.pin_target`'s own docstring says
        # why), so `pin_target`'s return is a `(scope, name)` pair, not a
        # widened `WorkstreamScope`.
        #
        # GATED ON `scope.may_upload`, NOT JUST VISIBILITY (fix round 1,
        # controller review finding 1): pinning is owner-only
        # (`rag-workstream-pin`'s own gate is `scope is None or not
        # scope.may_upload`), and in WS-1 every VISIBLE stream also
        # happens to be one this principal may manage -- there is no
        # share recipient yet. That coincidence stops being true the
        # moment WS-2 lands a read-only recipient, who would otherwise
        # see a banner and Pin buttons that always 404 at the route. A
        # scope visible-but-not-manageable is treated exactly like an
        # invalid id: no banner, no controls -- the SAME "renders nothing
        # at all" shape this comment already promises for a stream this
        # caller may not reach, so the page and the route can never
        # drift apart on who may pin.
        from agents.workstreams import pin_target

        pin_into = request.GET.get("pin_into", "")
        pin_into_id = int(pin_into) if pin_into.isdecimal() else None
        resolved = pin_target(principal, pin_into_id) if pin_into_id is not None else None
        pin_into_scope, pin_into_name = resolved if resolved is not None else (None, "")
        if pin_into_scope is not None and not pin_into_scope.may_upload:
            pin_into_scope, pin_into_name = None, ""
        context["pin_into"] = pin_into_scope
        context["pin_into_name"] = pin_into_name
        return context


_DELETE_FORBIDDEN_MESSAGE = (
    "Deleting a document is an administrator action on this box, or an "
    "action for an owner of one of the document's entitlements."
)
_REINGEST_FORBIDDEN_MESSAGE = (
    "Re-ingesting a document is an administrator action on this box, or an "
    "action for an owner of one of the document's entitlements."
)
_UPLOAD_FORBIDDEN_MESSAGE = (
    "Adding documents to the library needs an entitlement this account does not "
    "hold. Ask an administrator to grant it."
)


def _administered_document(request, doc_id, forbidden_message):
    """`(principal, document)` for a handler that administers a document,
    or `(principal, HttpResponseForbidden)` when this principal may not.

    C-32: `document_delete` and `document_reingest` carried this prologue
    verbatim, comment block and all, differing only in which of the two
    forbidden sentences they hand back. The MESSAGE stays a parameter --
    "Deleting" and "Re-ingesting" are what the operator reads, and a
    shared verb would make both vaguer.

    404 BEFORE 403, unchanged: a document nobody may administer and a
    document that does not exist are the same answer to a stranger, and
    `get_object_or_404` runs first so it stays that way.
    """
    principal = principal_for_request(request)
    document = get_object_or_404(Document, pk=doc_id)
    if not may_administer_document(principal, document):
        return principal, HttpResponseForbidden(forbidden_message)
    return principal, document


def _readable_document_or_404(request, doc_id):
    """`(principal, document)` for a handler that reads a document, or
    raises `Http404`.

    C-32: `document_file` and `document_transcript` carried this prologue
    verbatim including its comment. "Not readable" and "not there" are
    deliberately the same answer -- the module's own 404 house rule.

    CLASS L: content, not a row. 404 outside `readable_document` --
    INCLUDING for an administrator with the content setting off: the row
    is theirs to manage, the bytes are not theirs to read.

    THE TWO-STEP CONTAINMENT RESOLUTION (spec §8.1) lives on
    `readable_document` itself -- see that function's own docstring for
    the full rationale, including RULING G's admin case.
    """
    principal = principal_for_request(request)
    document = readable_document(principal, doc_id)
    if document is None:
        raise Http404(f"No document {doc_id}.")
    return principal, document


@require_POST
def document_delete(request, doc_id: int):
    """POST /rag/documents/<doc_id>/delete/ -- delete a document.

    Delegates to `services.delete_document`, which tears down the vector
    chunks, the managed-store files, and the `Document` row together
    (ADR 0009). Redirects back to the library on success.

    ADMINISTRATION, not reading (spec section 11.3): removing a document
    is an operator action, and `is_admin`, or an owner of one of the
    document's entitlements, may do it to a document they cannot read.
    403, not 404: this is a refusal on a class of ACTION, not a claim
    that the row does not exist -- the member can already see the row's
    title on the library page, so hiding its existence here would be a
    secrecy the surrounding page does not keep.

    ROUND 12 WHOLE-BRANCH REVIEW A-2/B-2 WIDENS THIS TO A THIRD
    ADMITTING CASE: the UPLOADER of THEIR OWN conversation-scoped
    document (`tools.rag.access.may_administer_document`, above --
    `may_label_document` alone left a chat-scoped document's own
    uploader with no Delete and no Re-ingest at all, undelivering the
    brief's own stated reason for showing the row -- "find/delete
    path"). THE CHEAP-GATE-BEFORE-ANY-ROW-READ SHORTCUT IA-1 ESTABLISHED
    IS GONE as a consequence: chat-scope ownership is a fact about THIS
    SPECIFIC ROW, not something a caller can be cheaply ruled out of
    before it is resolved, the way "holds no entitlement at all" could
    rule out the label-ownership half. A principal with NO standing at
    all (not admin, no entitlement, not this document's own uploader)
    now gets a 404 for a `doc_id` that does not exist, where it used to
    get 403 regardless -- the identical enumeration signal this route
    already accepts for every OTHER unauthorized `doc_id` (a real row
    this principal has no standing over still answers 403, unchanged).
    """
    principal, document = _administered_document(request, doc_id, _DELETE_FORBIDDEN_MESSAGE)
    if isinstance(document, HttpResponseForbidden):
        return document
    services.delete_document(document)
    return redirect("rag-documents")


@require_POST
def workstream_pin(request, ws_id):
    """POST `/rag/workstreams/<ws_id>/pin/` -- `action` = `pin` or
    `unpin`.

    CLASS O, NOT L, and the row it is addressed by is the STREAM, not the
    document. `rag-document-labels-bulk` is the nearest precedent for a
    `tools/rag` route whose predicate is not about reading the document's
    bytes; this one goes further and is about a row in ANOTHER COLUMN
    entirely, which is why the stream half resolves through the seam
    rather than through anything in `tools/rag`.

    TWO RESOLUTIONS, BOTH 404-SHAPED: the stream through
    `agents.workstreams.workstream_scope`, which answers `None` for a
    stream this caller may not be in AND for one that does not exist; and
    the document through `readable_document`. The NAMED refusals of spec
    §8.2 -- the document is contained, the cap is reached -- are messages
    on a row the caller can already see, not 404s.

    THE DOCUMENT HALF IS ITSELF TWO RESOLUTIONS -- `readable_document`'s
    own containment-first pair, the same one `document_file`/
    `document_transcript` use. Never the target `scope`'s own id, which
    would silently admit a document contained in a THIRD stream this
    principal also happens to read.

    `next` IS VALIDATED (CONSOLIDATION WAVE, audit A B5's own
    "highlight": an orchestrator-verified open-redirect class) through
    `agents.workstreams.validated_next_url` -- the SAME same-origin
    guard `agents/chat/service.py::validated_next_url` and `tools/
    vision/views.py::_validated_next_url` already put on their own
    `next` fields, reached through the sanctioned `agents.workstreams`
    crossing since `tools/rag` may not import `agents.chat` directly.
    BEFORE THIS FIX this route redirected to an UNVALIDATED `next`,
    the one caller-supplied redirect target on this box with no guard
    on it at all.
    """
    from agents.workstreams import validated_next_url, workstream_scope
    from tools.rag.workstreams import pin_document, unpin_document

    principal = principal_for_request(request)
    scope = workstream_scope(principal, ws_id)
    if scope is None or not scope.may_upload:
        raise Http404("No such workstream.")
    action = request.POST.get("action", "")
    if action == "unpin":
        message = unpin_document(principal, scope, request.POST.get("pin", ""))
    elif action == "pin":
        raw = request.POST.get("document", "")
        document = readable_document(principal, int(raw)) if raw.isdecimal() else None
        if document is None:
            raise Http404("No such document.")
        message = pin_document(principal, scope, document)
    else:
        # ITEM 6 (Coherence Wave D): flash-and-redirect, not a raw 400 --
        # the house convention every other mutation on this platform uses
        # (`identity/views.py:317-321`, `agents/chat/views/settings.py:
        # 74-79`), never a bare plain-text page with no shell, no
        # sidebar, no flash. Reached from a plain zero-JS
        # `<form>`/hidden-`action` pair (`rag/templates/rag/documents.
        # html:973`, `rag/templates/rag/panels/documents.html:144,175`),
        # the same shelled-form class F7/M4 already converted four of.
        message = "Unknown action. Expected 'pin' or 'unpin'."
        messages.error(request, message)
        return redirect(validated_next_url(request) or
                        reverse("chat-workstream", args=[ws_id]))
    if message:
        messages.error(request, message)
    return redirect(validated_next_url(request) or
                    reverse("chat-workstream", args=[ws_id]))


@require_POST
def document_labels_bulk(request):
    """POST /rag/documents/labels/ -- add or remove ONE set of
    entitlements across MANY documents, in one action.

    THE OWNER'S ONE-ACTION RULE (spec section 22.34). The inbox gap
    (spec section 21) makes this routine rather than occasional: a
    watcher-ingested document arrives unlabelled, and an administrator
    who had to label forty of them one at a time would be doing exactly
    the tedium the owner named as a failure.

    ADD OR REMOVE, NEVER "SET TO EXACTLY THIS". This was once one of two
    label routes, and the per-document one carried SET semantics -- a
    hidden field deciding whether a POST is destructive was the thing
    decision 29 refused, so they were two URLs. The SET route had no
    caller and is gone (C-36); this endpoint's semantics are unchanged,
    and they are now the only ones there are. Somebody selecting forty
    documents to add one label must not silently strip the labels those
    documents already carry.

    TARGETS: either `documents` (a list of ids) or `category` (a name) --
    the second is the same action at a different cardinality, and it
    STORES NO RULE. A document added to that category afterwards is
    unlabelled, deliberately: a stored category-to-entitlement mapping
    would be a second labelling authority beside `DocumentEntitlement`,
    applying itself to rows nobody reviewed.

    RESOLVED AGAINST THE RAW `Document` TABLE
    (`services.documents_targeted_for_labelling`), deliberately NOT
    `listable_documents` -- the same choice `document_delete`/
    `document_reingest` already make for this identical class-R family
    (ADMINISTRATION, not reading -- spec section 11.3, decision 9).
    `may_label_document` below is the ONLY gate, checked PER DOCUMENT:
    resolving the candidate set through a visibility filter first would
    make a document under an entitlement the caller does not hold
    silently DISAPPEAR from the count instead of being skipped and
    counted, which is the one thing this route promises an operator
    selecting a page of rows. THE RAW READ LIVES IN `services.py`, not
    here: this file's own guard (`foundation/ops/tests/
    test_column_boundaries.py`) refuses any literal `Document.objects`
    in `tools/rag/views.py`, so the one sanctioned administration-side
    exception is named and kept out of it entirely.

    PER-DOCUMENT PERMISSION, not all-or-nothing. Each document is checked
    with the same `may_label_document` `DocumentsListView` renders the
    Labels column's own gate from; one the caller has no standing over is
    skipped and counted, so an administrator selecting a page of rows is
    not refused wholesale by one row.

    ONE AUDIT EVENT PER DOCUMENT, not one per submit -- `set_document_
    labels` is called once per changed document, because that is the
    catalogue's existing grain: `library.document_labelled` names a
    `target_key` that is a document id, and a single event carrying
    forty ids would be a row whose target is a list, unqueryable by
    `identity.audit.for_target`. The cost is forty rows for forty
    documents, which is what an audit trail is for.
    """
    principal = principal_for_request(request)
    action = request.POST.get("action", "")
    if action not in ("apply", "remove"):
        # F7's flash-and-redirect (M4, Wave C review), for the reason
        # stated in full at `identity/views.py::user_edit`. This one
        # qualifies because the Document library is a plain zero-JS form
        # on a page inside the shell with a messages region -- not an
        # API-shaped endpoint where a 400 is the designed contract.
        messages.error(request, f"{action!r} is not a recognised action.")
        return redirect("rag-documents")

    raw = request.POST.getlist("entitlements")
    if not raw or any(not value.isdecimal() for value in raw):
        messages.error(request, "Choose at least one entitlement from the list.")
        return redirect("rag-documents")
    wanted = {int(value) for value in raw}
    offered = {pk for pk, _name in labelling_entitlements(principal)}
    if not wanted <= offered:
        messages.error(request, "You may only add or remove entitlements you own.")
        return redirect("rag-documents")

    category = request.POST.get("category", "").strip()
    ids = [value for value in request.POST.getlist("documents") if value.isdecimal()]
    # RAW ROWS, on purpose -- `services.documents_targeted_for_labelling`'s
    # own docstring has the full reasoning: this is administration, not a
    # reading surface, and the guard at `foundation/ops/tests/
    # test_column_boundaries.py` keeps every literal `Document.objects`
    # out of this file specifically so a reading surface can never drift
    # onto the raw manager by accident.
    try:
        targets = services.documents_targeted_for_labelling(ids=ids, category=category)
    except services.BulkLabelTargetSetTooLargeError as exc:
        # A-3: the category branch's own bound (`services.
        # MAX_BULK_LABEL_TARGETS`) -- the id branch already gets one for
        # free from the framework's 1000-field POST cap. Same refusal
        # shape as every other designed rejection on this route: a flash
        # naming the problem, nothing written, redirect back to the page.
        messages.error(request, str(exc))
        return redirect("rag-documents")

    changed = skipped = 0
    # C-05: hoisted out of the loop, exactly as `DocumentsListView` does
    # (`views.py` above, `is_admin_flag`/`owned_ids`). `may_label_document`'s
    # own docstring asks a caller amortizing per-row cost to pass these;
    # leaving them at `None` re-answered "is this principal an admin" and
    # "which entitlements do they own" once per target document.
    is_admin_flag = is_admin(principal)
    owned_ids = owned_entitlement_ids(principal)
    for document in targets:
        if not may_label_document(principal, document,
                                  is_admin_=is_admin_flag, owned=owned_ids):
            skipped += 1
            continue
        current = set(document_label_ids(document))
        updated = (current | wanted) if action == "apply" else (current - wanted)
        if updated != current:
            # THE EXISTING WRITER, per document: it writes the
            # difference, audits each direction, and re-stamps the chunk
            # cache inside its own transaction. A bulk path with its own
            # SQL would be a second writer of the `entitlements` key,
            # which is the one thing `tools/rag/labels.py` exists to
            # prevent.
            set_document_labels(principal, document, updated,
                                labelled_by=user_for_request(request))
            changed += 1
    # NAMED BY VERB, not a shared "Updated" (round 2 review, Minor 1):
    # the confirmation of a shelf-wide REMOVAL -- which can make every
    # document on that shelf follow the library posture -- must not read
    # identically to the confirmation of an addition. The endpoint
    # already knows which branch ran; saying so costs one line and is the
    # only feedback a person gets after the redirect.
    verb = "Added labels to" if action == "apply" else "Removed labels from"
    note = f"{verb} {changed} document(s)."
    if skipped:
        note += f" {skipped} skipped — you do not own a label on them."
    messages.info(request, note)
    return redirect("rag-documents")


@require_POST
def category_rename(request, cat_id: int):
    """POST /rag/categories/<cat_id>/rename/ -- rename a category. Rejects a
    blank name or one that collides (case-insensitively) with another shelf."""
    category = get_object_or_404(Category, pk=cat_id)
    new_name = normalize_category_name(request.POST.get("name", ""))
    if not new_name:
        messages.error(request, "Category name can't be blank.")
        return redirect("rag-documents")
    clash = Category.objects.filter(name__iexact=new_name).exclude(pk=category.pk).exists()
    if clash:
        messages.error(request, f"A category named “{new_name}” already exists.")
        return redirect("rag-documents")
    category.name = new_name
    category.save(update_fields=["name"])
    messages.info(request, "Category renamed.")
    return redirect("rag-documents")


@require_POST
def category_delete(request, cat_id: int):
    """POST /rag/categories/<cat_id>/delete/ -- delete a category; its
    documents fall back to Uncategorized via the model's SET_NULL."""
    category = get_object_or_404(Category, pk=cat_id)
    category.delete()
    messages.info(request, "Category deleted; its documents moved to Uncategorized.")
    return redirect("rag-documents")


def _may_upload(principal) -> bool:
    """Whether `principal` may add documents to the library AT ALL.

    THE MIRROR OF `tools/vision/views.py::_may_generate`, deliberately
    identical in shape: one predicate, two callers (the POST and the
    page), one named cross-column seam.

    `rag.ingest` is `mutates=True`, so no AGENT can be granted it -- a
    different question from whether a PERSON reaches this form, and one
    the tool-label page keeps apart by listing mutating tools as
    page-only (spec section 22.35).
    """
    from agents.entitlements import tool_access_for
    from tools.rag.tools import RAG_INGEST_TOOL_KEY

    return tool_access_for(principal).allows(RAG_INGEST_TOOL_KEY)


@require_POST
def document_upload(request):
    """POST /rag/documents/upload/ -- accept uploaded files, stream each into
    the watch inbox (`INGEST_INBOX_DIR/<category>/<name>`), then queue it for
    `rag.ingest` directly (`tools.rag.ingest.enqueue_ingest(..., move=True)`)
    -- watcher-independent: the job is enqueued immediately, from this
    request, not left for the folder watcher to notice later.

    A re-upload byte-identical to what's already staged at that path queues
    nothing (`enqueue_ingest` returns `None`, `stage_document`'s own dedup)
    -- counted honestly as "unchanged", never folded into the "Queued N
    file(s)" message (W1 review MAJOR 1); its own message points at
    Re-ingest for actually re-running ingestion on the same bytes.

    Race with the watcher (T2 review MINOR): the deployed `watch_folder`
    service polls this SAME inbox directory out-of-band, so on a large/
    slow-to-stage file the watcher's own quiescence enqueue can win the
    race and move `dest` out from under this request before its own
    `enqueue_ingest` call gets there. `stage_document`'s
    `transaction.atomic()` protects the Document row itself (only one side
    ever creates/updates it), but the LOSING side's `store.move_file` then
    raises `FileNotFoundError` (its source is already gone). That's handled
    per-file, not as a request-level failure: a `FileNotFoundError` here is
    counted as "queued" ONLY once a `Document` row is confirmed to actually
    exist at this original path (T4 carry-over hardening -- see the
    in-loop comment; the prior cut inferred "the watcher must have won"
    without checking, which would have silently swallowed a genuine
    staging failure); any OTHER exception is logged and that one file is
    skipped (named in its own error message), without aborting the rest of
    the loop -- uploading several files must never 500 partway through and
    leave the remaining ones never staged at all.

    Size cap (T4): each file's `upload.size` is checked against
    `RagSettings.get_solo().max_upload_bytes` BEFORE a single byte is
    written to the inbox -- an oversized file gets its own rejection
    message (never batched with the unsupported-extension list, since the
    limit itself is worth naming) and the loop continues with the rest.
    Duration cap (T7 review m2): a video/audio file over
    `RagSettings.max_media_seconds` can't be checked this early (probing
    needs the file written to disk first) -- `ingest.enqueue_ingest`
    raises `ingest.MediaDurationExceededError` for one, caught below and
    given the same own-message-verbatim treatment. Page cap (T10): a
    scanned PDF/image over `RagSettings.max_document_pages` is the exact
    same shape, one field over -- `ingest.enqueue_ingest` raises
    `ingest.DocumentPageCapExceededError`, caught by name (not by the
    generic `except Exception` branch below) for the same reason.

    Category comes from the `new_category` text field if given, else the
    `category` select ("" / "Uncategorized" -> the inbox root). Unsupported
    extensions are skipped (never staged/queued). Redirects to the library
    with a status message.

    Tabular honesty (W2, ADR 0014 §18): a successfully-queued CSV/XLSX
    (`ext in tools.rag.readers.TABULAR_EXTS`) counts toward the ordinary
    "Queued N file(s)" summary like any other accepted file -- it IS
    queued, and it WILL be ingested -- but gets its own extra flash line
    naming what that ingest actually produces (`DocumentRow`s, never
    pgvector chunks, ADR 0005): "{name} stored as a table; not searchable
    yet." No retrieval code path changes; this is copy only.

    A document the WATCHER ingests arrives UNLABELLED and follows the library
    posture until somebody labels it. Per-inbox default labels are deferred
    (spec section 21); the mitigation is the bulk labelling above -- select
    what arrived, or a whole category, and label it in one action.

    Placement (spec §9, owner decision 5): an upload naming a `workstream`
    resolves it through the `agents.workstreams.workstream_scope` seam,
    which answers `None` for a stream this principal may not upload to
    AND for one that does not exist -- the two are indistinguishable to
    this view, so both raise `Http404`, never `HttpResponseForbidden`
    (the route is row-addressed, T-decision "a stream the uploader may
    not upload to is a 404"). With a resolved stream, the WORKSTREAM
    PANEL'S OWN request (no `conversation` field -- see round 12 below)
    lets the stream's own `default_upload_placement` (`"universal"`/
    `"contained"`) win if set; otherwise the POSTed `placement` field is
    read and MUST be one of those two values -- an absent or
    unrecognised one is REFUSED, naming the `placement` field, as a
    flashed message and a redirect (Coherence Wave D, item 6 -- was a
    bare 400 until then; see the branch's own comment below), never a
    silent fallback to either value (author decision 17: this is the
    one field where a server-side default would undo an owner's
    decision nobody actually made). A truthy `remember_placement` on
    that same request writes the chosen value back as the stream's own
    default via `set_upload_placement_default`, so later uploads to
    this stream stop asking. Every accepted file's `enqueue_ingest` call
    then forwards `workstream_id` -- the stream's id when the resolved
    placement is `"contained"`, `None` otherwise (which is what keeps
    an upload outside any stream, or a `"universal"` placement inside
    one, producing an ordinary universal document).

    THE CHAT DOOR'S OWN SEPARATE-POST PATH IS RETIRED (round 13,
    message-bound attachments; owner feedback: "we shouldn't process
    unless the message is actually submitted with the file"). Through
    round 12 this view ALSO accepted a `conversation` field
    (`chat/_attach_files.html`'s own hidden field), a THIRD placement
    value ("This chat only"), and validated a `next` round-trip back to
    the conversation page -- an upload that ran the moment files were
    chosen, entirely separate from whether a message was ever sent. That
    whole path is GONE: the file input and the placement chooser now
    live INSIDE `#turn-form` (`chat/conversation.html`), and a
    chat-carried file is staged by `tools.rag.services.stage_turn_
    attachments`, called from `agents.chat.service.start_turn` at
    turn-create time -- never by this view, which now serves ONLY the
    library page's own upload form and the workstream Documents panel's
    (`rag/panels/documents.html`), exactly as it did before round 12
    ever added the chat case. `DocumentAttachment` (round 11 review
    I-5's junction table) is written ONLY by that new staging path now;
    this view creates no such row, for anybody, under any field name.
    """
    principal = principal_for_request(request)
    if not _may_upload(principal):
        return HttpResponseForbidden(_UPLOAD_FORBIDDEN_MESSAGE)

    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "No files were selected to upload.")
        return redirect("rag-documents")

    # THE STREAM, IF THIS UPLOAD IS INSIDE ONE (spec §9.1). Resolved
    # through the seam, which answers `None` for a stream this principal
    # may not be in AND for one that does not exist -- the caller cannot
    # tell them apart, and answers 404 to both.
    from agents.workstreams import set_upload_placement_default, workstream_scope

    scope = None
    raw_stream = request.POST.get("workstream", "")
    if raw_stream:
        scope = workstream_scope(principal, int(raw_stream)) \
            if raw_stream.isdecimal() else None
        if scope is None or not scope.may_upload:
            raise Http404("No such workstream.")

    # THE FORM'S PLACEMENT, resolved to ONE value (author decision 18)
    # rather than several booleans a template could combine into a
    # state that does not exist. ONE DOOR ONLY, as of round 13 (the
    # chat door's own three-value chooser -- `is_chat_door`,
    # `_CHAT_PLACEMENTS_IN_STREAM`/`_LOOSE` -- is retired from this
    # view entirely; see this function's own docstring): round-11
    # semantics, UNCHANGED -- auto-applies a remembered
    # `default_upload_placement` when one is set, offers only
    # `_PLACEMENTS`'s two values otherwise.
    placement = None
    document_scope = Document.Scope.UNIVERSAL
    if scope is not None:
        if scope.default_upload_placement:
            placement = scope.default_upload_placement
        else:
            placement = request.POST.get("placement", "")
            if placement not in _PLACEMENTS:
                # NEITHER RADIO PRESELECTED, AND THE VIEW ENFORCES IT
                # (author decision 17). An absent or unrecognised
                # `placement` on an in-stream upload IS STILL REFUSED,
                # NAMING THE FIELD, never a fallback to either value:
                # this is the one field where a server-side fallback
                # would silently undo an owner decision. ITEM 6
                # (Coherence Wave D): the refusal used to be a raw 400;
                # it is flash-and-redirect now, the SAME shape the "no
                # files selected" refusal just above this function
                # already uses -- a bare plain-text page was never part
                # of the decision this comment argues for, only the
                # refusal itself.
                messages.error(
                    request,
                    "Choose where this file should live — the universal library, or this "
                    "workstream only. The 'placement' field is required for an upload "
                    "inside a workstream."
                )
                return redirect("rag-documents")
            if request.POST.get("remember_placement"):
                set_upload_placement_default(principal, scope.workstream_id, placement)

    # THE UPLOADER'S OWN LABELS, checked against what this page offered
    # them and nothing else. `may_label_document` is the wrong predicate
    # here and the difference matters: a brand-new document is
    # UNLABELLED, and `may_label_document` answers False for an
    # unlabelled document held by a non-admin (there is no entitlement
    # owner to delegate from). Applied literally, an entitlement owner
    # could see the picker and never write a label -- a control that
    # silently does nothing.
    #
    # `offered` is `labelling_entitlements(principal)`, the same function
    # the picker renders from, so a POST naming anything else is a stale
    # form or a hand-made request and gets one honest message.
    raw_labels = request.POST.getlist("entitlements")
    offered = {pk for pk, _name in labelling_entitlements(principal)}
    if any(not value.isdecimal() for value in raw_labels) or \
            not {int(value) for value in raw_labels} <= offered:
        messages.error(request, "Labels are chosen from the list, not typed.")
        return redirect("rag-documents")
    wanted_labels = {int(value) for value in raw_labels}

    new_category = normalize_category_name(request.POST.get("new_category", ""))
    selected = request.POST.get("category", "").strip()
    if selected.lower() == "uncategorized":
        selected = ""
    category = new_category or selected

    inbox = Path(settings.INGEST_INBOX_DIR).resolve()
    target_dir = (inbox / category).resolve() if category else inbox
    if not target_dir.is_relative_to(inbox):
        messages.error(request, "Invalid category name.")
        return redirect("rag-documents")
    # H13 review round 1, finding 4: `only_if_created=True` -- when
    # `category` is empty, `target_dir` IS `settings.INGEST_INBOX_DIR`
    # itself, a directory shared across every category and every future
    # upload, not one this call owns -- never force-chmod a pre-existing
    # one. A per-category subdirectory this call is the one creating
    # (the common case) is still tightened exactly as before.
    create_owner_only_dir(target_dir, only_if_created=True)

    # `document_scope` can only ever be UNIVERSAL on this view now
    # (round 13 retires the chat door's own "This chat only" placement
    # value from here entirely -- see this function's own docstring),
    # so every upload stages under the ordinary watched inbox, exactly
    # as it did before round 12 ever introduced a second staging
    # directory for this view to choose between.
    max_upload_bytes = settings_row_for(request).max_upload_bytes
    human_cap = human_bytes(max_upload_bytes)
    upload_exts = ingest.supported_exts()

    # C-14: the per-file write/hash-compare/enqueue body -- the exception
    # ladder, the hash-before-enqueue ordering, the unlink-on-cap-rejection,
    # and the watcher-race check -- lives in `ingest.stage_and_enqueue_one`
    # now, shared with `services.stage_turn_attachments`'s identical loop
    # (that function's own docstring has the full "two doors, one body"
    # reasoning). This loop keeps only ITS OWN bookkeeping: the flashes, the
    # tabular info line, and label application -- `services.py`'s loop keeps
    # its own (the `_attach` calls and `staged_document_ids`) instead.
    queued, unchanged, rejected, failed = 0, [], [], []
    for upload in files:
        outcome = ingest.stage_and_enqueue_one(
            upload, target_dir,
            category=category or None, actor=principal,
            workstream_id=(scope.workstream_id if placement == "contained" else None),
            document_scope=document_scope, max_upload_bytes=max_upload_bytes,
            upload_exts=upload_exts, log_label="document_upload",
        )
        if outcome.kind == "rejected":
            rejected.append(outcome.name)
        elif outcome.kind == "oversize":
            messages.error(
                request,
                f"{outcome.name} is larger than the {human_cap} limit — raise it in RAG "
                "settings, or add it to the inbox folder directly.",
            )
        elif outcome.kind == "refused":
            # FOUR PRODUCERS of this outcome, each `stage_and_enqueue_one`'s
            # own by-name `except` clause: `MediaDurationExceededError` (T7),
            # `DocumentPageCapExceededError` (T10), and `StageRefused` for
            # EITHER of its two reasons (round-3 hardening) -- B-2's
            # re-stage refusal (a takeover this uploader may not perform)
            # or B-3's containment refusal (the resolved path is outside
            # this door's own owned directories). A fifth, earlier source
            # of this same outcome kind -- `SymlinkRefused` (B-3's write
            # door: the destination name is already taken by a symlink) --
            # is caught before this ladder even starts, in this same
            # function's own write; same "refused" kind, not folded in
            # here. Every one of their own messages already names the
            # limit/reason and how to fix it, so they're shown verbatim --
            # not folded into the generic "couldn't queue" copy the
            # `failed` summary below shows, and not counted there either
            # (that summary's "try uploading again" advice would be
            # misleading for any of them; raising a cap, trimming the
            # file, renaming the upload, or clearing the symlink are the
            # actual fixes).
            messages.error(request, outcome.reason)
        elif outcome.kind == "queued":
            queued += 1
            if outcome.is_tabular:
                # W2 (ADR 0014 §18): a tabular file (CSV/XLSX) IS queued and
                # WILL be ingested -- into `DocumentRow`/`Document.
                # tabular_schema` SQL rows, never pgvector chunks (ADR
                # 0005) -- so it belongs in the "Queued" count above like
                # any other accepted file, but gets an HONEST second line
                # naming what that actually means: it will be stored, not
                # made searchable. Per-file, not batched, so each name is
                # named.
                messages.info(request, f"{outcome.name} stored as a table; not searchable yet.")
            if wanted_labels and outcome.document is not None and not outcome.raced:
                # A raced file's `document` is a row THIS request did not
                # stage (the watcher won) -- the pre-extraction code never
                # reached the label block for it at all (`continue` inside
                # `except FileNotFoundError`, before this code ever ran).
                # `set_document_labels` writes the EXACT set (deletes
                # anything not selected, plus an audit row per change), so
                # applying it here would silently relabel -- possibly
                # strip labels off -- a document this request has no
                # actual claim on.
                set_document_labels(principal, outcome.document, wanted_labels)
        elif outcome.kind == "unchanged":
            unchanged.append(outcome.name)
        elif (
            outcome.kind == "failed"
            and outcome.document is not None
            and outcome.document.status_detail == ingest._QUEUE_QUOTA_EXCEEDED_DETAIL
        ):
            # ONE-CASE BRANCH (C-7 review, fix round 2): staged but the
            # enqueue itself was rejected for hitting this caller's own
            # queue cap -- `_enqueue_ingest_job` already wrote an honest
            # FAILED row + `status_detail` for it, same as every other
            # `failed` outcome below, but "try uploading again" (the
            # generic summary's own advice) is misleading here: retrying
            # immediately hits the same cap. Surface THIS document's own
            # `status_detail` instead, so the operator reads "wait for
            # one to finish" rather than being told to retry a request
            # that will fail again the same way.
            messages.error(request, f"{outcome.name}: {outcome.document.status_detail}")
        else:
            # Staged but the enqueue itself was rejected (`_enqueue_ingest_job`
            # already wrote an honest FAILED row + status_detail for it) --
            # not "unchanged", a genuine failure to queue.
            failed.append(outcome.name)

    if queued:
        messages.info(request, f"Queued {queued} file(s) — track them on the Queue page.")
    if unchanged:
        messages.info(
            request,
            f"{len(unchanged)} file(s) already in the library and unchanged — "
            "use Re-ingest to process them again.",
        )
    if rejected:
        messages.error(request, f"Skipped unsupported file(s): {', '.join(rejected)}.")
    if failed:
        messages.error(request, f"Couldn't queue {', '.join(failed)} for ingest — try uploading again.")
    return redirect("rag-documents")


@require_POST
def document_reingest(request, doc_id: int):
    """POST /rag/documents/<doc_id>/reingest/ -- re-queue a FAILED (or
    READY) document for another `rag.ingest` pass, from its retained
    managed-store copy (`tools.rag.ingest.enqueue_reingest` -- never the
    original upload/watch-folder location, which may no longer exist; ADR
    0009's "the box owns its data"). Redirects to the library with a status
    message.

    404s exactly like `document_delete`/`category_rename` (`get_object_or_404`)
    for an unknown doc_id -- a stale/hand-typed URL reads the same either way,
    no distinct copy needed.

    A PENDING/PROCESSING document is refused with an "already being
    processed" message rather than silently double-queued -- a second
    `rag.ingest` job for the same Document mid-flight would race the first
    one's own PROCESSING -> READY/FAILED status writes.
    """
    # ADMINISTRATION, not reading (spec section 11.3) -- see
    # `document_delete`'s own comment for the widened rule's full
    # reasoning (round 12 whole-branch review A-2/B-2: this is now
    # `may_administer_document`, the SAME gate `document_delete` uses,
    # on the same class of action -- a chat-scoped document's own
    # uploader is admitted here too, and this is in fact THE repair path
    # `tools.rag.ingest._ingest_prose`'s own comment names for a chat-
    # scoped document that silently missed its `conversation` chunk
    # stamp: Re-ingest, not a re-visit of the (now closed) label page).
    principal, document = _administered_document(request, doc_id, _REINGEST_FORBIDDEN_MESSAGE)
    if isinstance(document, HttpResponseForbidden):
        return document

    if document.status in (Document.Status.PENDING, Document.Status.PROCESSING):
        messages.error(request, f"“{document.title}” is already being processed.")
        return redirect("rag-documents")

    try:
        job_id = ingest.enqueue_reingest(document, actor=principal)
    except FileNotFoundError:
        messages.error(
            request,
            f"“{document.title}”'s stored file is missing — it can't be re-ingested; re-upload it instead.",
        )
        return redirect("rag-documents")

    if job_id is None:
        document.refresh_from_db()
        messages.error(request, document.status_detail or _QUEUE_ENQUEUE_FAILED_MESSAGE)
        return redirect("rag-documents")

    messages.info(request, f"Queued “{document.title}” for re-ingestion.")
    return redirect("rag-documents")


_RANGE_HEADER_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class _RangeUnsatisfiable:
    """Sentinel `_parse_range` returns for a Range header it understood
    (a single, well-formed `bytes=` request) that is UNSATISFIABLE against
    this file's actual size (RFC 9110 §14.2's 416 case: the start offset
    is at or past EOF, including every range on a genuinely empty file) --
    deliberately distinct from `None` ("this header wasn't one this
    endpoint accepts at all; IGNORE it and serve a plain 200"), so
    `document_file` can tell those two cases apart without conflating a
    real out-of-bounds request with a header it never understood in the
    first place. A class (compared with `is`), not `True`/a string/an
    `Enum` member -- there is exactly one sentinel value this module ever
    needs, and `is _RangeUnsatisfiable` reads unambiguously as identity
    comparison against a type, never mistakable for a truthy tuple."""


RANGE_UNSATISFIABLE = _RangeUnsatisfiable()


def _parse_range(range_header: str, file_size: int) -> tuple[int, int] | _RangeUnsatisfiable | None:
    """Parse a `Range: bytes=...` header (T10, Safari's own requirement
    for playable inline video/audio) against a known `file_size`, per RFC
    9110 §14.1.2 (syntax)/§14.2 (semantics) -- T10 review MAJOR 2, the
    prior version's `end >= file_size` outright REJECTED a range instead
    of clamping it, had no suffix-range support at all, and (via a shared
    `None` return) answered 416 for a header this endpoint should have
    simply ignored.

    Three-way return, not two:
    - `(start, end)` inclusive byte offsets -- a SATISFIABLE single range,
      serve a 206 slice.
    - `RANGE_UNSATISFIABLE` -- a syntactically understood single-range
      `bytes=` request whose start is at/past `file_size` (RFC 9110
      §14.2) -- answer 416 with `Content-Range: bytes */<file_size>`.
    - `None` -- anything this function doesn't accept as a single
      satisfiable-or-unsatisfiable byte range: a multi-range request
      (`bytes=0-10,20-30`), an unknown unit (`items=0-10`), unparseable
      syntax (`bytes=abc-def`), `start > end` (`bytes=5-2`), or the
      suffix form's own two degenerate inputs (`bytes=-`, no digits at
      all, and `bytes=-0`, "the last zero bytes", a request no real
      client means literally) -- the caller IGNORES the header entirely
      and serves a plain 200, per RFC 9110 §14.2's "a server MAY ignore
      the Range header field" for a request it doesn't support, rather
      than treating "I don't understand this" the same as "I understood
      it and it's out of bounds".

    Supports both shapes an `HTMLMediaElement` actually sends -- `bytes=
    START-END` and the open-ended `bytes=START-` (end defaults to the
    last byte) -- PLUS the suffix form `bytes=-N` ("the last N bytes",
    RFC 9110 §14.1.2): simple enough to support outright now that this
    function is being hardened, rather than the prior version's
    deliberate omission of it.

    An `end` that reaches or exceeds `file_size` is CLAMPED to
    `file_size - 1`, never rejected -- a player that always asks for
    `bytes=0-<huge>` speculatively (a real, common `<video>` pattern)
    gets a satisfiable 206 for the whole remaining file rather than a 416
    that breaks playback outright.
    """
    match = _RANGE_HEADER_RE.match(range_header.strip())
    if not match:
        return None
    start_str, end_str = match.group(1), match.group(2)

    if not start_str:
        # Suffix form, `bytes=-N` -- "the last N bytes". `bytes=-` (no
        # digits at all) and `bytes=-0` ("the last zero bytes") are both
        # treated as unparseable/not-meant-literally -- ignore, not 416.
        if not end_str or end_str == "0":
            return None
        if file_size == 0:
            return RANGE_UNSATISFIABLE
        suffix_length = int(end_str)
        start = max(0, file_size - suffix_length)
        return start, file_size - 1

    start = int(start_str)
    if start >= file_size:
        return RANGE_UNSATISFIABLE
    if not end_str:
        return start, file_size - 1
    end = int(end_str)
    if start > end:
        return None
    return start, min(end, file_size - 1)


def _iter_file_range(path: str, start: int, length: int, chunk_size: int = 64 * 1024):
    """Yield `length` bytes from `path` starting at byte `start`, in
    `chunk_size` pieces -- the streaming body for `document_file`'s 206
    Partial Content response. A plain generator (not `FileResponse`, which
    has no notion of an END offset -- it would stream from `start` straight
    through to EOF regardless of the range's own end) so a bounded range
    request never reads/sends more than the bytes it actually asked for."""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def document_file(request, doc_id: int):
    """GET /rag/documents/<doc_id>/file/ -- stream the original ingested file.

    Serves strictly by `Document` primary key -- the request never supplies
    a filesystem path, so there is no path-traversal surface. Add
    `?download=1` (or `?download=true`) to force a `Content-Disposition:
    attachment` download; otherwise the file streams inline so the browser
    can render it (PDF viewer, plain text, etc.).

    HTTP Range support (T10, hardened T10 review MAJOR 2 to RFC 9110
    §14.1.2/§14.2), video/audio `media_type` only: Safari refuses to play
    an inline `<video>`/`<audio>` element served without `Accept-Ranges`/
    206 support at all, so an AV document's response always carries
    `Accept-Ranges: bytes` -- on EVERY branch below, including the plain
    `FileResponse` path at the bottom (a rangeless request, or a `Range`
    header this endpoint ignored -- see `_parse_range`'s own docstring for
    which shapes that is) -- that header is what tells the BROWSER it may
    issue a follow-up range request in the first place; that same plain
    path also sets `content_type` from `document.media_type` for an AV
    document (not just for the two `_TEXT_LIKE_EXTENSIONS` inline cases),
    so a rangeless/ignored-Range AV request still gets the right MIME type
    to play inline.

    A `Range` header `_parse_range` parses to a satisfiable `(start, end)`
    is honored with a 206 Partial Content slice (`_iter_file_range`); one
    it parses as UNSATISFIABLE (`RANGE_UNSATISFIABLE` -- the start offset
    at/past EOF, RFC 9110 §14.2) answers 416 (`Content-Range: bytes */
    <size>`, no body); one it can't make sense of at all (`None` -- a
    multi-range request, an unknown unit, unparseable syntax, `start >
    end`, or `bytes=-`/`bytes=-0`) is IGNORED entirely, falling through to
    the exact same plain `FileResponse` path a rangeless request takes,
    per RFC 9110 §14.2's "a server MAY ignore the Range header field" for
    a request it doesn't support -- never a 416 for a header this
    endpoint simply didn't understand. A non-AV document is completely
    unaffected either way -- Range is only ever read off `request.headers`
    when `is_av`.

    `readable_document` is the gate: 404 outside it, including for an
    administrator with the content setting off -- the row is theirs to
    manage, the bytes are not theirs to read.

    B-8 (round-3 hardening): both response paths -- the plain
    `FileResponse` below and the 206 `StreamingHttpResponse` range slice
    above -- are marked `Cache-Control: private, no-store, max-age=0`
    with `Cookie` added to `Vary`, via `foundation.http.mark_private`, so
    an entitlement-gated document never lingers in a shared browser's
    disk cache or a LAN caching proxy after the session that fetched it
    is gone.
    """
    principal, document = _readable_document_or_404(request, doc_id)

    source_path = document.source_path
    if not source_path or not os.path.isfile(source_path):
        raise Http404(
            f"Source file for document {doc_id} was not found on disk "
            f"(it may have moved or been deleted since ingest)."
        )

    filename = os.path.basename(source_path) or document.title
    download = request.GET.get("download", "").lower() in ("1", "true")
    extension = os.path.splitext(source_path)[1].lower()
    is_av = document.media_type.startswith(("video/", "audio/"))

    content_type = None
    if not download and extension in _TEXT_LIKE_EXTENSIONS:
        content_type = "text/plain; charset=utf-8"
    elif is_av:
        content_type = document.media_type

    range_header = request.headers.get("Range", "") if is_av else ""
    if range_header:
        file_size = os.path.getsize(source_path)
        parsed = _parse_range(range_header, file_size)
        if parsed is RANGE_UNSATISFIABLE:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{file_size}"
            response["Accept-Ranges"] = "bytes"
            return response
        if parsed is not None:
            start, end = parsed
            length = end - start + 1
            response = StreamingHttpResponse(
                _iter_file_range(source_path, start, length),
                status=206,
                content_type=content_type or "application/octet-stream",
            )
            response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            response["Content-Length"] = str(length)
            response["Accept-Ranges"] = "bytes"
            return mark_private(response)
        # `parsed is None`: a Range header this endpoint doesn't accept
        # (multi-range/unknown-unit/unparseable/start>end/`bytes=-0`) --
        # RFC 9110 §14.2 permits ignoring it outright; fall through to the
        # exact same plain response a rangeless request gets, below.

    response = FileResponse(
        open(source_path, "rb"),  # noqa: SIM115 -- FileResponse owns and closes this handle; it must outlive this frame
        as_attachment=download,
        filename=filename,
        content_type=content_type,
    )
    if is_av:
        response["Accept-Ranges"] = "bytes"
    return mark_private(response)


def document_transcript(request, doc_id: int):
    """GET /rag/documents/<doc_id>/transcript/ -- a zero-JS page listing
    this document's transcription/extraction sidecar (T10), `[mm:ss] text`
    per segment for audio/video or `-- p. N --` headers + text for a
    page-keyed (scanned PDF/image) sidecar.

    Reads `extract.json` via `tools.rag.sidecar.read_sidecar` (T10
    review MAJOR 1) -- a lightweight, standalone, model-free and
    worker-free parse -- rather than importing `tools.rag.media`: that
    module's own docstring is explicit that it must NEVER be imported
    from `tools/rag/views.py` (an HTTP-request thread must not risk
    pulling in worker-only machinery that needs ffmpeg/a transcriber
    client just to render a page of already-finished text). This view only
    ever reads a sidecar some earlier queued job already finished writing.
    `tools.rag.sidecar` is a LEAF beside `readers.py`/`transcode.py`/
    `extract.py` -- see that module's own docstring for the layering law
    -- so importing it here crosses no boundary this view's own docstring
    warns against.

    404 (never 500) when there's no sidecar for this document yet (still
    processing, never was media, missing, corrupt, non-UTF-8, or valid
    JSON of the wrong shape -- `read_sidecar`/`display_segments` reduce
    every one of those to "nothing usable" rather than raising, T10
    review MAJOR 1) -- `Document.has_transcript` is exactly the file-
    exists half of this same check, gating the library table's own link
    to this page, so this 404 is reachable only by a stale link, a direct
    URL guess, or a sidecar that exists on disk but fails to parse/shape-
    check (a case `has_transcript`'s own stat-only check can't detect;
    see that property's docstring).

    B-8 (round-3 hardening): marked `Cache-Control: private, no-store,
    max-age=0` with `Cookie` added to `Vary`, same as `document_file` --
    the transcript is entitlement-gated content too.
    """
    principal, document = _readable_document_or_404(request, doc_id)
    sidecar_path = store.sidecar_path(document.id)
    sidecar = read_sidecar(sidecar_path)
    if sidecar is None:
        raise Http404(f"No transcript is available for document {doc_id}.")

    return mark_private(render(
        request,
        "rag/transcript.html",
        {"document": document, "lines": display_segments(sidecar)},
    ))


# The Ask-time picker's override pk named no usable connection --
# it was deleted/renamed since the page loaded, never existed, or doesn't
# carry the "chat" capability (`models.registry.bindings.resolve_connection_named`
# raises `ValueError` for all three; they read identically to the operator,
# so they share one cause-accurate message rather than three).
_UNREGISTERED_CONNECTION_MESSAGE = (
    "That model is no longer registered — pick another in the model console."
)

# THE MODEL HALF (IA-2 T14 review finding 1): a picked connection that
# resolves fine but that `ModelAccess` forbids is a DIFFERENT fact from
# one that is gone -- "no longer registered" would be a false sentence
# for a model that is registered and simply not this account's to use.
# 403, not the 503 `_unregistered_connection_response` gives, and NO
# `setup_url`: `inference-console` is class S and 403s the very member
# this message is for. The sentence itself is `models.registry.bindings.
# MODEL_FORBIDDEN_MESSAGE` (whole-branch review item 3): this file used
# to carry its own private copy, byte-identical to `tools/vision/
# views.py`'s and a differently-named one in `agents/runtime/
# preflight.py`.

# T6: distinct 503 copy for the queue itself being unusable, kept apart from
# `_model_unavailable_response`'s "no model" causes above -- these are a
# different failure entirely (the jobs tables, not a model binding) and
# must not be conflated with "go connect a model" wording.
_QUEUE_UNAVAILABLE_MESSAGE = (
    "The queue isn't ready yet — run database migrations, then ask again."
)
_QUEUE_ENQUEUE_FAILED_MESSAGE = "Couldn't add your question to the queue — nothing was queued."
_JOB_NOT_FOUND_MESSAGE = (
    "That question is no longer in the queue — check Ask history for the answer."
)


_JSON_PARSE_ERROR = "The request body could not be read as JSON."


def _json(payload: dict, *, status: int = 200) -> JsonResponse:
    """The one JSON responder for this module's two API endpoints.

    `DjangoJSONEncoder`, not the bare `json` default: `JobStatus.
    created_at`/`started_at` are `datetime`s, and this encoder renders
    them ISO-8601 with a `Z` suffix -- the shape `rest_framework.
    renderers.JSONRenderer` produced before C-55 removed it. The one
    measurable difference is microsecond precision (three digits here,
    six there); no template on this box reads either field, and
    `tools/rag/tests/test_ask_api_contract.py` pins the shape rather
    than the digit count.

    `safe=False` is deliberately NOT passed: every body this module
    returns is a dict, and the day one is not, the guard should fire."""
    return JsonResponse(payload, status=status, encoder=DjangoJSONEncoder)


def _json_body(request) -> dict | None:
    """The request body as a mapping, or `None` when it cannot be read.

    TWO ENCODINGS, because DRF's default parser list accepted both and
    real callers use both: a JSON content type is parsed with
    `json.loads`; anything else (form-urlencoded, multipart) is read off
    `request.POST`, which is how `identity/tests/test_route_matrix.py`'s
    own `rag-ask` driver posts. A JSON content type whose body is not a
    JSON OBJECT -- unparseable, or a bare list/string/number -- answers
    `None`, which the caller turns into a 400. Before C-55 that case was
    DRF's own `{"detail": "JSON parse error - ..."}`; it is this
    platform's `{"error": ...}` now, which is the shape `ask.html`
    actually reads."""
    if request.content_type and request.content_type.split(";")[0].strip() == "application/json":
        try:
            parsed = json.loads(request.body or b"{}")
        except (ValueError, UnicodeDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return request.POST.dict()


def _model_unavailable_response(causes: dict[str, str], resolved: dict[str, ResolvedModel]) -> JsonResponse:
    """The friendly 503 for `AskView`'s pre-check -- a thin `JsonResponse`
    wrapper around `tools.rag.messages.model_unavailable_message` (the pure,
    transport-free formatting; shared with the queued `rag.ask` job's
    pre-run re-check, `tools.rag.jobs.run_ask`, so both surfaces agree on
    the exact same copy for the exact same cause map -- see that module's
    docstring)."""
    return _json(
        {"error": model_unavailable_message(causes, resolved), "setup_url": reverse("inference-console")},
        status=503,
    )


def _queue_unavailable_response() -> JsonResponse:
    """The friendly 503 for "the queue itself is unusable" (T6,
    `_QUEUE_UNAVAILABLE_MESSAGE`) -- shared by `AskView`'s enqueue-time
    `QueueUnavailable` catch and `AskJobStatusView`'s own, replacing two
    verbatim inline copies of the same `{"error": ..., "setup_url": ...}`
    body."""
    return _json(
        {"error": _QUEUE_UNAVAILABLE_MESSAGE, "setup_url": reverse("inference-console")},
        status=503,
    )


def _precheck_models(
    answer_override: ResolvedModel | None = None,
) -> tuple[JsonResponse | None, dict[str, ResolvedModel]]:
    """Resolve + health-check both roles. Returns `(response, resolved)`:
    `response` is a 503 JsonResponse if either role is unavailable, else
    `None`; `resolved` is every role that DID resolve (a subset of
    `PRECHECK_ROLES` on failure, both of them on success), keyed by
    role -- returned for a caller that wants the exact `ResolvedModel`
    without re-deriving it a second way, though `AskView` (T6) no
    longer needs it past checking whether `causes` came back empty: the
    job itself (`tools.rag.jobs.run_ask`) re-resolves fresh at run
    time and owns the Ask history write.

    `answer_override`: when given, stands in for `rag.answer`'s
    own `resolve()` call -- it has already been validated by
    `resolve_connection_named` before this is called, so it can never
    contribute an `UNBOUND` cause, only `UNREACHABLE` if its endpoint
    fails the health check below. `rag.embed` is unaffected either way.

    `tools.rag.jobs._precheck` is this function's sibling for the queued
    `rag.ask` job's pre-run re-check. The RESOLVE loop just below is
    still a deliberately-duplicated twin of that function's own (see
    `jobs.py`'s module docstring for why the resolve loop itself isn't
    shared code) -- keep that half in sync by hand if this cause matrix
    ever changes. The dedup + health-check probe that follows it, by
    contrast, is one shared function now (`tools.rag.messages.
    unreachable_endpoints`, C-15), so a change to THAT half only has to
    happen once.
    """
    resolved: dict[str, ResolvedModel] = {}
    causes: dict[str, str] = {}

    for role in PRECHECK_ROLES:
        if role == RAG_ANSWER_ROLE and answer_override is not None:
            resolved[role] = answer_override
            continue
        try:
            resolved[role] = resolve(role)
        except ValueError:
            logger.debug("No inference binding resolved for role %r", role, exc_info=True)
            causes[role] = UNBOUND
        except Exception:  # noqa: BLE001 -- log detail, then degrade to the friendly 503
            logger.exception("Unexpected error resolving inference binding for role %r", role)
            causes[role] = UNBOUND

    for role in unreachable_endpoints(resolved, log_prefix="AskView"):
        causes[role] = UNREACHABLE

    if not causes:
        return None, resolved
    return _model_unavailable_response(causes, resolved), resolved


def _unregistered_connection_response() -> JsonResponse:
    return _json(
        {"error": _UNREGISTERED_CONNECTION_MESSAGE, "setup_url": reverse("inference-console")},
        status=503,
    )


def AskView(request) -> JsonResponse:
    """POST {"question": str, "connection": str | null} -> 202 {"job_id",
    "state": "queued", "position", "priority", "status_url"}.

    A `priority` key is still ACCEPTED and still parses as a whole,
    positive number for every caller (a malformed value is still an
    honest 400), but it is not part of the request shape this endpoint
    offers -- see the next paragraph.

    `priority` is NEVER honoured, for ANY caller -- anonymous, member, or
    administrator (C-4, review round 1: a caller-supplied value is
    parsed and range-checked, same as ever, so a malformed one is still
    an honest 400, and then unconditionally dropped). Queue priority is
    queue policy, derived once by `models.contracts.queue.
    resolve_client_priority` -- see that function's docstring, and the
    call just above `enqueue()` below, for the full rationale. An
    administrator who wants `rag.ask` jobs to run at a different
    priority already has the tool for that: the Queue page's own
    settings form, which is audited and applies to the job kind as a
    whole, not to whichever one request an administrator happened to be
    looking at.

    T6 (the execution-queue build): this no longer answers the question
    inline. It validates, pre-checks, and enqueues a `rag.ask` job
    (`models.contracts.queue.enqueue`) -- the SAME `tools.rag.jobs.run_ask`
    handler the queue worker runs for every other caller, so there is
    exactly one code path in the platform that ever calls
    `tools.rag.retrieval.answer_question`. The page (`ask.html`) polls
    `AskJobStatusView` (below) at `status_url` until the job finishes.

    Before enqueuing, a cheap pre-check resolves both bindings the eventual
    job actually depends on -- `rag.answer` (chat) and `rag.embed`
    (embedding) --
    and health-checks each *distinct* engine endpoint once (the two roles
    normally share one Ollama, and a health check is a blocking HTTP round
    trip -- up to a few seconds when the engine is down -- so checking twice
    would double that cost for no benefit). A role with no binding configured
    (`resolve()` raises `ValueError`) is an ordinary "go connect one"
    outcome and only gets a debug-level log line; any other failure (a
    broken `INFERENCE_BINDING_PROVIDER` dotted path, a DB error, an
    unexpected `is_healthy` blowup) is logged via `logger.exception` in
    addition to the friendly response, so a misconfiguration leaves a
    server-side trace instead of just reading as "no model connected". The
    two causes -- "not set up" (unbound) vs. "unreachable" (a binding
    exists but its engine failed the health check) -- are tracked separately
    per role so the 503 message never claims more or less breakage than is
    actually true (see `_model_unavailable_response`).

    Ask-time model picker: an optional `connection` field (a
    `ModelConnection` pk, as a string -- the Ask form's `<select>` value)
    stands in for `rag.answer`'s durable binding for THIS question only;
    absent or blank is exactly today's role path. `rag.embed` is NEVER
    overridable -- embeddings are index-welded, not a per-question choice --
    so its own pre-check always walks the role path regardless. The picked
    connection is resolved via `models.registry.bindings.resolve_connection_named`
    (pk + "chat" capability) before the pre-check runs at all: a pk naming no
    usable connection is a caller error (deleted/renamed since the page
    loaded, or never chat-capable) that reads identically to the operator
    either way, so it short-circuits straight to `_UNREGISTERED_CONNECTION_MESSAGE`
    rather than entering the pre-check's cause matrix. Once resolved, the
    override stands in for `rag.answer` in the *same* pre-check machinery
    (`_precheck_models(answer_override=...)`), so an unreachable picked
    endpoint gets the same cause-accurate "chat model is unreachable" wording
    an unreachable role binding would -- naming the picked model's situation,
    not a stale one. The picked connection's pk (never its name) is the only
    override state threaded into the enqueued job's payload
    (`tools.rag.jobs.plan_ask`/`run_ask` re-resolve it fresh, at plan and
    run time respectively) -- `answered_by` is never known at enqueue time
    (the question hasn't been answered yet), only once the job actually
    finishes; see `AskJobStatusView` for where that shows up.

    A PLAIN DJANGO VIEW SINCE C-55. It was a DRF `APIView` for a JSON
    encoder, a body parser and a method dispatcher, which is three
    stdlib/Django lines; `config/settings.py` configured no serializers
    and no permission classes, so little else of the framework was ever
    in play. One consequence is deliberate and is not a regression: DRF
    enforced CSRF only for session-authenticated callers, so an
    anonymous cross-site POST carried no token requirement at all;
    Django's middleware now enforces it for every POST --
    `CsrfViewMiddleware` covers this endpoint like every other POST on
    the box, and `ask.html` has always sent the token.

    STILL NAMED `AskView`, NOT A CLASS ANY MORE (orchestrator ruling on
    C-55: the by-name sweep a lowercase rename would need touches ~33
    files across four columns for no behavioural gain, so this is the
    brief's own documented fallback -- a plain function keeping the old
    name rather than a half-finished rename).
    """
    if request.method != "POST":
        return _json({"error": "Method not allowed."}, status=405)

    body = _json_body(request)
    if body is None:
        return _json({"error": _JSON_PARSE_ERROR}, status=400)

    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return _json(
            {"error": "'question' is required and must be a non-empty string."},
            status=400,
        )

    # `priority` is optional -- absent/blank means "walk the queue's own
    # priority chain" (models.contracts.queue.enqueue's `priority=None`
    # rung: the job kind's default, then the queue-wide default). When
    # given, it must parse as a whole number and be positive -- the
    # same two-step validation and error-copy grammar
    # `_history_limit_update` (below) already uses for its own
    # whole-number field.
    priority: int | None = None
    priority_param = body.get("priority")
    if priority_param not in (None, ""):
        try:
            priority = int(priority_param)
        except (TypeError, ValueError):
            return _json({"error": "Priority must be a whole number."}, status=400)
        if priority <= 0:
            return _json({"error": "Priority must be a positive number."}, status=400)

    # An absent/blank `connection` is exactly today's role
    # path (answer_override stays None). A non-blank one must resolve
    # to a real, chat-capable ModelConnection -- a bad pk (unparseable,
    # unknown, or not chat-capable) is a caller error, reported the
    # same way regardless of which of those three it was.
    connection_param = body.get("connection")
    answer_override: ResolvedModel | None = None
    if connection_param not in (None, ""):
        try:
            override_pk = int(connection_param)
        except (TypeError, ValueError):
            return _unregistered_connection_response()
        access = model_access_for(principal_for_request(request))
        # THE PERMISSION CAUSE, CHECKED FIRST and asked of the ACCESS
        # VALUE (never by matching the caught exception's message):
        # a registered connection this account may not use is a 403,
        # not the 503 "no longer registered" the except block below
        # gives for a connection that is actually gone.
        if not access.allows(override_pk):
            return _json({"error": MODEL_FORBIDDEN_MESSAGE}, status=403)
        try:
            answer_override, _override_name = resolve_connection_named(
                override_pk, "chat", access=access)
        except ValueError:
            return _unregistered_connection_response()

    # `_precheck_models`'s `resolved` return is deliberately unused past
    # this point (T6): it exists only to prove `causes` is empty. The
    # exact model that ends up answering is re-resolved, fresh, inside
    # the queued job itself (`tools.rag.jobs.run_ask`) -- see that
    # module's docstring for why a queued job never trusts an
    # enqueue-time snapshot as its run-time truth.
    precheck_failure, _resolved_models = _precheck_models(answer_override)
    if precheck_failure is not None:
        return precheck_failure

    category = body.get("category") or None

    # The payload mirrors exactly what `tools.rag.jobs.plan_ask`/
    # `run_ask` expect (see that module's docstring for the shape):
    # `connection` carries the picked pk -- never a resolved model or a
    # display name -- so both the enqueue-time planner and the run-time
    # re-check resolve it themselves, fresh, rather than trusting
    # anything decided here. Absent/blank stays absent, exactly like
    # `_resolve_answer`'s own `payload.get("connection") not in (None,
    # "")` check expects.
    payload = {
        "question": question.strip(),
        "category": category,
        # WHO ASKED -- the acting principal, stamped into every job
        # payload (the acting rule).
        **payload_fields(principal_for_request(request)),
    }
    if connection_param not in (None, ""):
        payload["connection"] = str(override_pk)

    # C-4: PRIORITY IS QUEUE POLICY, not request data, for EVERY caller
    # -- anonymous, member, or administrator. `models.contracts.queue.
    # resolve_client_priority`'s own docstring has the full rationale;
    # this is the only call site in the tree that has ever forwarded a
    # client-supplied value at all, and after review round 1 it forwards
    # none: an `is_admin` gate here would have made "may this principal
    # reach an admin SURFACE" (true for EVERY caller on an open box --
    # there is nobody to hide an admin surface from, and D-2 already
    # proved this exact route answers an anonymous, cross-site POST on
    # one) stand in for "did an administrator actually choose this
    # priority", which it never really answers even for a signed-in
    # superuser -- the Queue page's own settings form is where that
    # choice belongs.
    priority = resolve_client_priority(priority)

    try:
        job_id = enqueue("rag.ask", payload, priority=priority)
        job_status = get_job(job_id)
    except QueueUnavailable:
        return _queue_unavailable_response()
    except QueueQuotaExceeded as exc:
        # C-7 (H39): an honest refusal naming the quota -- 429, not the
        # generic 503 below, matching `agents.chat.service.start_turn`'s
        # own `QueueQuotaExceeded` catch.
        return _json({"error": str(exc)}, status=429)
    except Exception:  # noqa: BLE001 -- log detail, then degrade to a clean 503
        logger.exception("Failed to enqueue rag.ask job")
        return _json({"error": _QUEUE_ENQUEUE_FAILED_MESSAGE}, status=503)

    return _json(
        {
            "job_id": job_id,
            "state": "queued",
            "position": job_status.position,
            "priority": job_status.priority,
            "status_url": reverse("rag-ask-status", args=[job_id]),
        },
        status=202,
    )


def _answered_by_from_model_refs(model_refs: list[dict]) -> str:
    """The answer-role model's display name from a job's `model_refs`
    snapshot (`models.queue.backend.JobStatus.models`, the same list of
    plain dicts `models.queue.models.InferenceJob.model_refs` stores --
    `{"role", "engine", "endpoint", "model_id", "connection_name",
    "footprint_bytes"}`, see that field's docstring).

    `connection_name` when the picked-connection override named one, else
    `model_id` (the role path's own `ModelRef.connection_name` is always
    `""` -- see `tools.rag.jobs.plan_ask`) -- the same "override name, or
    the model" fallback shape `tools.rag.jobs.run_ask`'s own finished-job
    `answered_by` uses, just read off the claim-time snapshot instead of a
    fresh role-primary lookup (this is a point-in-time RUNNING-state
    approximation; a bound connection's *display name* on the un-overridden
    role path only becomes available once `run_ask` actually finishes and
    calls `answer_role_primary()` itself)."""
    for ref in model_refs:
        if ref.get("role") == RAG_ANSWER_ROLE:
            return ref.get("connection_name") or ref.get("model_id") or ""
    return ""


def AskJobStatusView(request, job_id: int) -> JsonResponse:
    """GET /rag/ask/jobs/<job_id>/ -- the Ask page's poll target for a
    `rag.ask` job enqueued by `AskView` (T6).

    A thin `JsonResponse` wrapper around `models.contracts.queue.get_job` --
    ALWAYS 200 for a readable job (queued/running/cancelled/failed/
    succeeded all report their state via the response BODY, never the HTTP
    status), matching this platform's never-500 convention. Only two
    situations get a non-200 status: an unknown job id (404 -- it may have
    aged out of `JobSettings.retention_limit`'s terminal-job pruning; the
    body points at Ask history instead) and the queue itself being
    unreachable (503, `QueueUnavailable` -- the same unmigrated-window
    tolerance `AskView`'s own enqueue call gets).

    Per state:
    - queued: {"state", "position", "priority", "submitted_at"}.
    - running: {"state", "started_at", "answered_by", "progress"} --
      `_answered_by_from_model_refs` reads the answer role's name off the
      job's claim-time `model_refs` snapshot. `progress` (T3) is `job.
      progress` verbatim (`None` for a job that hasn't reported one yet --
      `rag.ask`'s own handler doesn't today, see `tools.rag.jobs.
      run_ask`'s docstring, but this key is generic across every job kind)
      -- an ADDITIVE key: `ask.html`'s poller (`pollJobStatus`) reads only
      the keys it already knows about and ignores the rest, so this key
      appearing costs it nothing; wiring the page itself to actually
      DISPLAY it is out of this task's scope (`ask.html` is untouched).
    - cancelled: {"state"} -- nothing else to say.
    - failed: {"state", "error", "setup_url"}. `setup_url` is included on
      EVERY failed job, not only ones whose stored error is the
      model-unavailable copy -- the simplest honest rule (a failed job
      could fail for other reasons the operator would still want a way
      back to the model console for) and avoids depending on the stored
      error's exact wording ever staying stable.
    - succeeded: `job.result` (already `{"answer", "citations",
      "answered_by", ...}` -- `tools.rag.jobs.run_ask`'s return value,
      stored verbatim by the worker) spread first, with `"state"` set
      LAST so it always wins the merge -- a strict superset of the pre-T6
      synchronous 200 body, so anything that read that body before still
      finds every key it looked for here, and `job.result` can never
      shadow the job's actual state even if a future `run_ask` ever added
      a "state" key of its own.

    STILL NAMED `AskJobStatusView`, NOT A CLASS ANY MORE (orchestrator
    ruling on C-55: the by-name sweep a lowercase rename would need
    touches ~33 files across four columns for no behavioural gain, so
    this is the brief's own documented fallback -- a plain function
    keeping the old name rather than a half-finished rename; see
    `AskView`'s own docstring for the same note).
    """
    if request.method != "GET":
        return _json({"error": "Method not allowed."}, status=405)

    # IA-1: `may_see_job_id` is `is_admin`-or-owner -- ROW visibility,
    # asked BEFORE the queue itself, so a member polling a job they
    # may not see gets the same 404 an unknown id gets, never a
    # different shape that would confirm the row exists.
    principal = principal_for_request(request)
    if not may_see_job_id(principal, job_id):
        return _json(
            {"error": _JOB_NOT_FOUND_MESSAGE, "history_url": reverse("rag-history")},
            status=404,
        )
    try:
        job = get_job(job_id)
    except QueueUnavailable:
        return _queue_unavailable_response()

    if job is None:
        return _json(
            {"error": _JOB_NOT_FOUND_MESSAGE, "history_url": reverse("rag-history")},
            status=404,
        )

    # CONTENT visibility is the second question, asked only once the
    # row itself is known visible: `sees_all_content` alone answers
    # it for an open box or a content-enabled administrator with no
    # further query; otherwise the row's own payload (fetched through
    # the sanctioned `models.queue.visibility.visible_jobs` seam,
    # never `models.queue.models` directly -- see that module's
    # docstring) decides whether THIS principal is the actor.
    if sees_all_content(principal):
        hidden = False
    else:
        row = visible_queue_jobs(principal).filter(pk=job_id).first()
        hidden = not may_read_job_content(principal, row.payload if row else None)

    if job.state == "queued":
        body = {
            "state": job.state,
            "position": job.position,
            "priority": job.priority,
            "submitted_at": job.created_at,
        }
    elif job.state == "running":
        body = {
            "state": job.state,
            "started_at": job.started_at,
            "answered_by": _answered_by_from_model_refs(job.models),
            "progress": job.progress,
        }
    elif job.state == "cancelled":
        body = {"state": job.state}
    elif job.state == "failed":
        # `error` is content -- it can quote payload back -- so it
        # follows the same content-hidden gate as the answer below,
        # the rule `models.queue.views._present_row` and `tools.
        # vision.views`'s queue card apply too. `setup_url` is not
        # content (it is a fixed console link) and is kept either way.
        body = {
            "state": job.state,
            "error": "" if hidden else job.error,
            "setup_url": reverse("inference-console"),
        }
    else:
        # "succeeded": job.result is tools.rag.jobs.run_ask's own
        # return dict, stored verbatim by the worker -- spread it
        # FIRST, then "state" last, so a later dict-literal key
        # always wins. When content is hidden, `job.result` (the
        # answer, the citations) is dropped entirely rather than
        # spread -- `JobStatus.summary` is `summarize_job`'s output,
        # which for `rag.ask` IS THE QUESTION, so hiding the answer
        # and leaving the question would not be hiding anything.
        body = {"state": job.state} if hidden else {**(job.result or {}), "state": job.state}

    if hidden:
        body["content_hidden"] = True
    return _json(body)


def _precheck_embed_role() -> tuple[str | None, ResolvedModel | None]:
    """W6 (ADR 0014 §18): resolve `rag.embed` and health-check its endpoint
    ALONE -- the search page (`SearchView`, below) never calls an LLM at
    all, so it has no `rag.answer` opinion to check alongside it (unlike
    `_precheck_models`, which `AskView` always calls to check the pair
    together). Same resolve-then-health-check shape and cause vocabulary
    (`UNBOUND`/`UNREACHABLE`, `tools.rag.messages.model_unavailable_
    message`) as that function's own per-role loop, just for one role.

    Returns `(message, resolved)`: `message` is `model_unavailable_
    message`'s copy when the role is unbound/unreachable (`resolved` is
    `None` in that case); `message` is `None` and `resolved` is the
    `ResolvedModel` when the role is available. `model_unavailable_
    message`'s own `len(causes) == 1` branch (see that function's
    docstring) is what fires here -- a solo-role cause dict can never trip
    its two-role "both unbound"/"same endpoint" branches.
    """
    try:
        embed_resolved = resolve(RAG_EMBED_ROLE)
    except ValueError:
        logger.debug("SearchView: no inference binding resolved for role %r", RAG_EMBED_ROLE, exc_info=True)
        return model_unavailable_message({RAG_EMBED_ROLE: UNBOUND}, {}), None
    except Exception:  # noqa: BLE001 -- log detail, then degrade to the friendly copy
        logger.exception("SearchView: unexpected error resolving inference binding for role %r", RAG_EMBED_ROLE)
        return model_unavailable_message({RAG_EMBED_ROLE: UNBOUND}, {}), None

    healthy = not unreachable_endpoints(
        {RAG_EMBED_ROLE: embed_resolved}, log_prefix="SearchView")

    if not healthy:
        return (
            model_unavailable_message({RAG_EMBED_ROLE: UNREACHABLE}, {RAG_EMBED_ROLE: embed_resolved}),
            None,
        )

    return None, embed_resolved


# W6: a query longer than this is rejected before retrieval runs at all.
# Ask (`ask.html`'s `<textarea>`) has no analogous cap today -- this is a
# NEW bound, specific to this page, chosen because retrieval here runs
# SYNCHRONOUSLY in the request (one embedding call + one SQL query, no
# queue -- see `SearchView`'s own docstring), so an unbounded query string
# is an unbounded embedding-call payload with no queue between it and the
# request thread.
SEARCH_QUERY_MAX_LENGTH = 2000


class SearchView(TemplateView):
    """GET /rag/search/?q=...&category=... -- the retrieval-only search
    page (W6, ADR 0014 §18): the score floor's visible surface, and a
    plain way to see what semantic (or hybrid) search actually retrieves
    for a query without spending an LLM call on it.

    Zero JS, server-rendered: the form re-submits as a GET (so a search is
    a bookmarkable/shareable URL, matching `DocumentsView`'s own
    convention), and every result renders directly from this view's
    context -- no polling, no client-side fetch.

    Runs `tools.rag.retrieval.retrieve_nodes`/`apply_score_floor` --
    THE SAME retrieval `AskView`'s queued `rag.ask` job runs (top_k,
    hybrid-if-live, category filter, and the score floor, all identical) --
    but SYNCHRONOUSLY, in this request, never through the execution queue:
    a search is one embedding call plus one SQL query, holds no LLM, and
    the embed model this needs is already resident for every OTHER RAG
    surface on this install (Ask, ingest) -- queuing it would add latency
    and a poll loop for work that costs less than the health check
    `AskView`'s own pre-check already pays inline. No `AskRecord` is
    written either -- a search is not a question with an answer to keep a
    history of.

    An empty `q` renders the bare form -- no precheck, no retrieval. A
    query longer than `SEARCH_QUERY_MAX_LENGTH` is rejected inline before
    any model call. Otherwise: the `rag.embed` role is pre-checked alone
    (`_precheck_embed_role`) -- unbound/unreachable renders the existing
    `model_unavailable_message` copy inline, 200, never a 503 (this page
    has no queue to fail into); a retrieval failure surviving that precheck
    is caught in two ways (W6 review MINOR 4): a `ValueError` (today, only
    `tools.rag.index.get_vector_store`'s "the resolved embed binding has
    no recorded dimension" case) carries its own actionable operator-facing
    message, so it's rendered VERBATIM (Django's default autoescape, no
    `|safe`) rather than flattened to generic copy the operator couldn't
    act on; any other exception (the engine going away between the health
    check and the real call, or any other runtime failure) is caught,
    logged, and rendered as `tools.rag.messages.SEARCH_FAILED_MESSAGE`,
    also inline, 200, never a 500. A precheck/retrieval failure and zero
    results are NEVER conflated -- see the three mutually exclusive context
    keys below.

    W6 review NIT: on an install where nothing has ever been ingested (no
    live `rag_chunks` table -- `tools.rag.index.live_store_shape() is
    None`), this view short-circuits straight to the honest
    `SEARCH_NO_RESULTS_MESSAGE` empty state WITHOUT building a vector store
    or calling `retrieve_nodes` at all -- constructing a `PGVectorStore`
    and calling `.retrieve(...)` on a table that doesn't exist yet would
    otherwise have `PGVectorStore._initialize()` (installed llama-index-
    vector-stores-postgres) create it as a side effect, idempotent DDL a
    plain read-only GET has no business issuing. That `live_store_shape()`
    probe is wrapped in its own try/except (a `ProgrammingError`/
    `OperationalError` -- same never-500 degrade as `_top_k_context_
    window_fit_check` below): logged at `warning` with `exc_info=True`,
    rendered as `SEARCH_FAILED_MESSAGE` inline, 200, same as any other
    retrieval failure.

    Context (mutually exclusive once `q` is non-blank and within the
    length cap): `error` (the length-cap or embedding-failure copy),
    `model_unavailable` (the embed-role precheck copy), or `results` (a
    list, possibly empty) alongside `empty_message` when it's empty --
    `tools.rag.messages.search_score_floor_message(floor)` when
    something was actually retrieved AND a non-zero floor is set and live
    (W5: suspended while the store is hybrid, same as Ask; W6 review MAJOR
    1: an empty/no-match library or category with a floor set still
    retrieves NOTHING, and must get `SEARCH_NO_RESULTS_MESSAGE`, not the
    floor copy -- naming the floor as the reason when nothing was even
    retrieved for it to reject would be dishonest), else
    `SEARCH_NO_RESULTS_MESSAGE`.
    """

    template_name = "rag/search.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request

        query = request.GET.get("q", "").strip()
        category_param = request.GET.get("category", "").strip()
        context.update(
            {
                "query": query,
                "category_param": category_param,
                "categories": Category.objects.all(),
                "results": None,
            }
        )

        if not query:
            return context

        if len(query) > SEARCH_QUERY_MAX_LENGTH:
            context["error"] = (
                f"Search query is too long — keep it under {SEARCH_QUERY_MAX_LENGTH} characters."
            )
            return context

        model_unavailable, embed_resolved = _precheck_embed_role()
        if model_unavailable is not None:
            context["model_unavailable"] = model_unavailable
            return context

        # W6 review NIT: short-circuit before touching the vector store at
        # all when `rag_chunks`' live table doesn't exist yet
        # (`live_store_shape() is None` -- nothing prose has ever been
        # ingested on this install). Without this, `retrieve_nodes` below
        # would build a `PGVectorStore` and call `.retrieve(...)` on it,
        # and `PGVectorStore._initialize()` (installed llama-index-vector-
        # stores-postgres) creates the (empty) chunk table as a side effect
        # of that first call -- idempotent DDL, harmless to correctness,
        # but a plain GET on a read-only search page has no business
        # issuing a CREATE TABLE. Skipping straight to the honest
        # "nothing matched" empty state is both cheaper and more honest
        # than letting a search silently materialize a table.
        from django.db import OperationalError, ProgrammingError

        try:
            live_shape_is_none = rag_index.live_store_shape() is None
        except (ProgrammingError, OperationalError):
            logger.warning(
                "SearchView: live_store_shape() failed for a search query",
                exc_info=True,
            )
            context["error"] = SEARCH_FAILED_MESSAGE
            return context
        if live_shape_is_none:
            context["results"] = []
            context["empty_message"] = SEARCH_NO_RESULTS_MESSAGE
            return context

        settings_row = settings_row_for(request)
        try:
            nodes, hybrid, _index = retrieval.retrieve_nodes(
                query, category_param or None, settings_row, embed_resolved=embed_resolved,
                visibility=document_visibility(principal_for_request(request)),
            )
        except ValueError as exc:
            # W6 review MINOR 4: `tools.rag.index.get_vector_store` raises
            # `ValueError` (never caught by anything upstream of this view)
            # with an ACTIONABLE operator-facing message when the resolved
            # `rag.embed` binding has no recorded `embed_dim` -- e.g. "set
            # one for this connection in the model console before it can be
            # used". The blanket `except Exception` below would have turned
            # that specific, fixable-right-now message into the generic
            # `SEARCH_FAILED_MESSAGE` ("try again"), which is actively
            # unhelpful here: trying again can't fix a binding with no
            # recorded dimension. Rendered inline via Django's default
            # autoescape (`{{ error }}` in search.html carries no `|safe`),
            # same never-500 degrade as every other branch here, just with
            # the real cause instead of a generic one.
            logger.warning("SearchView: retrieval failed for a search query (%s)", exc)
            context["error"] = str(exc)
            return context
        except Exception:  # noqa: BLE001 -- log detail, then degrade to a clean inline error
            logger.exception("SearchView: retrieval failed for a search query")
            context["error"] = SEARCH_FAILED_MESSAGE
            return context

        surviving = retrieval.apply_score_floor(
            nodes, settings_row.retrieval_score_floor, hybrid=hybrid
        )
        context["results"] = [_search_result_for(node, hybrid=hybrid) for node in surviving]
        if not surviving:
            floor = settings_row.retrieval_score_floor
            # W2/W6 review MAJOR 1: mirror `answer_question`'s own
            # `nodes and score_floor > 0.0 and not surviving_nodes` guard
            # (tools/rag/retrieval.py) -- WITHOUT the `nodes` (raw,
            # pre-floor) term, an empty/no-match library or category with a
            # non-zero floor set retrieves NOTHING (`nodes == []`), yet
            # still printed the floor copy ("Nothing scored above the
            # retrieval floor (0.50).") as if something had been retrieved
            # and rejected by the floor. Only claim the floor was the
            # reason when something was actually retrieved for it to reject.
            context["empty_message"] = (
                search_score_floor_message(floor)
                if nodes and floor > 0.0 and not hybrid
                else SEARCH_NO_RESULTS_MESSAGE
            )
        return context


class HistoryView(TemplateView):
    """GET /rag/history/ -- the Ask history page.

    Newest-first list of every `AskRecord` this principal may see
    (`tools.rag.access.visible_ask_records` -- IA-1: an Ask record holds
    a QUESTION and an ANSWER, so it is content, and an administrator with
    the content setting off sees only their own). `tools.rag.services.
    record_ask` already prunes on insert, so this is that function's own
    queryset with no further filtering beyond visibility. No pagination
    (see the template comment): the retention limit already bounds the
    page, and an operator who wants to see fewer just lowers it.

    AND NOTHING ELSE, SINCE UI-1. This view used to hand the template six
    operator policies as well, and the page rendered five settings cards
    ABOVE the first history row: the retention limit sat on the page it
    limits, and then four unrelated caps accumulated behind it because
    that page was simply where the first one had landed. Every one of
    them now lives on `LibrarySettingsView` below. This is a reading
    surface, and it reads.
    """

    template_name = "rag/history.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["records"] = visible_ask_records(principal_for_request(self.request))
        return context


class LibrarySettingsView(TemplateView):
    """GET /rag/settings/ -- "Library", every operator policy
    the library obeys, on one page (UI-1 work item D).

    THE FORMS MOVED HERE FROM THE HISTORY PAGE (UI-1), AND THEIR SEVEN
    URLS BECAME ONE (S2, Coherence Wave C). Every card here posts to
    `rag-settings-update` with a hidden `field` input naming which form
    submitted; `library_settings_update` dispatches to the handler for
    that field. The seven per-field URLs these used to post to are
    retired -- seven paths, seven route classes and seven route-matrix
    drivers for seven writes to one table on one page was a touch-point
    count that grew with every field added, and one endpoint is the shape
    `models.queue.views.queue_settings_update` already had.

    `record_count` is here rather than on the history page because the
    sentence it belongs to is here ("N of LIMIT kept", under the
    retention field). It comes off `visible_ask_records`, the SAME
    visibility-filtered queryset the history page lists from -- a count
    taken from a bare `AskRecord.objects.count()` would leak exactly the
    fact that filter exists to hide.

    Class S (`identity/routes.py`): the endpoint its forms post to is
    administrator-only, so the page that carries them is too. The template additionally gates the cards on
    `identity_is_admin`, which is what keeps the render and the gate
    agreeing on an OPEN box, where everybody is an administrator.
    """

    template_name = "rag/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["record_count"] = visible_ask_records(
            principal_for_request(self.request)
        ).count()
        settings_row = settings_row_for(self.request)
        context["history_limit"] = settings_row.history_limit
        # GB, one decimal, `foundation.format.bytes_to_gb` (T10) -- the SAME base
        # `_upload_cap_update` stores with (T4) -- `floatformat:1`
        # in the template rounds this to exactly what an operator typed
        # (e.g. stored from "8.5" reads back as "8.5", not a long float
        # tail from the bytes round-trip).
        context["max_upload_gb"] = bytes_to_gb(settings_row.max_upload_bytes)
        # Minutes, the SAME 60x base `_media_duration_update` stores
        # with (T7) -- rendered unconditionally (like `max_upload_gb` above),
        # regardless of whether the "media" feature is enabled: the field is
        # real and operator-editable now, the same way `max_upload_bytes`
        # was rendered before any upload cap was actually enforced.
        context["max_media_minutes"] = settings_row.max_media_seconds / 60
        # A plain whole-number count (T8 review minor 4), like
        # `history_limit` -- no unit conversion needed, unlike the two
        # fields above (bytes-to-GB, seconds-to-minutes): the stored value
        # IS the page count an operator would type.
        context["max_document_pages"] = settings_row.max_document_pages
        # W4 (ADR 0014 §14): plain values, no unit conversion -- `top_k` is
        # a count (like `history_limit`/`max_document_pages`) and the score
        # floor is already the [0, 1] cosine-similarity value an operator
        # types (like `max_document_pages`, no bytes/minutes round-trip
        # needed; `_retrieval_score_floor_update` rounds it to 2dp
        # on save, so what's read back here is always already display-
        # ready -- W4 review MINOR 4/5).
        context["retrieval_top_k"] = settings_row.retrieval_top_k
        context["retrieval_score_floor"] = settings_row.retrieval_score_floor
        # W4 review MINOR 8: the template used to hardcode "1024 tokens" in
        # its top_k caveat copy -- the exact `ingest.CHUNK_TOKENS` value the
        # fit check itself multiplies by (`_retrieval_top_k_update`),
        # duplicated as a literal that could silently drift from the real
        # constant. Passed through so the copy and the check can never
        # disagree.
        context["chunk_tokens"] = ingest.CHUNK_TOKENS
        # W5 (ADR 0014 §18): the TOGGLE's current value (not the live
        # table's shape -- there's no cheap way to read that here without
        # the same `pg_attribute` lookup `get_vector_store` already pays at
        # ask time, and this page has no other reason to touch the DB
        # connection the store uses). The template's caveat copy is
        # careful to describe the toggle/rebuild relationship rather than
        # claim this reflects what's live right now.
        context["hybrid_search"] = settings_row.hybrid_search
        return context


def _history_limit_update(request):
    """`field=history_limit` -- update `RagSettings.history_limit`.
    Blank/zero/negative/non-numeric is a clean form error (never
    a 500), same never-500 shape as every other settings write in this
    codebase (see `models/registry/views.py::connection_add`'s
    `context_window` validation).

    Lowering the limit does NOT prune anything here, on the spot -- pruning
    only ever happens on the next successful Ask
    (`tools.rag.services.record_ask` -> `_prune_ask_records`), which is
    exactly what the template's caveat next to this field says.

    S1 (Coherence Wave B): `history_limit` is a `PositiveIntegerField`
    (Postgres `integer`, max `2**31-1`) and this view -- unlike this
    module's OTHER five settings writes, which all go through
    `_ragsettings_field_update` -- hand-rolled its own parse/validate and
    never got the `foundation.settings_bounds` ceiling check the rest of
    them did (T10 review MINOR 5). An operator-typed `history_limit`
    beyond the column's real ceiling used to reach `.save()` uncaught, a
    `django.db.utils.DataError`, on this never-500 surface.

    S3 (Coherence Wave B): audited, `LIBRARY_SETTINGS_UPDATED` -- the
    same action every other `RagSettings` field write below uses (one
    action per settings DOMAIN, not one per column; see that action's
    own docstring in `identity/contracts/actions.py`), written inside
    the same `transaction.atomic()` block as the row save, the identical
    "a failed audit insert must roll the write back" shape `identity.
    services.set_posture` established.
    """
    raw_limit = request.POST.get("history_limit", "").strip()
    try:
        limit = int(raw_limit)
    except ValueError:
        messages.error(request, "History limit must be a whole number.")
        return settings_redirect(request, "rag-settings")
    if limit <= 0:
        messages.error(request, "History limit must be a positive number.")
        return settings_redirect(request, "rag-settings")
    if exceeds_field_ceiling(limit, max_stored=POSITIVE_INT_FIELD_MAX):
        messages.error(request, "History limit is too large.")
        return settings_redirect(request, "rag-settings")

    settings_row = settings_row_for(request)
    settings_row.history_limit = limit
    with transaction.atomic():
        settings_row.save(update_fields=["history_limit"])
        audit.record(
            principal_for_request(request), actions.LIBRARY_SETTINGS_UPDATED,
            target_type="ragsettings", target_key=settings_row.pk,
            field="history_limit", to=limit,
        )
    messages.info(request, f"History limit set to {limit}.")
    return settings_redirect(request, "rag-settings")


# T4: the same "must be a number of GB." / "...greater than zero." copy
# shape `models.registry.views.connection_add` uses for its own
# `footprint_gb` field -- a size, like a memory footprint, is naturally
# fractional (unlike `history_limit`'s whole-number count), so this gets
# the float-GB grammar rather than `_history_limit_update`'s int one.
_MAX_UPLOAD_GB_NOT_A_NUMBER_MESSAGE = "Maximum upload size must be a number of GB."
_MAX_UPLOAD_GB_NOT_POSITIVE_MESSAGE = "Maximum upload size must be greater than zero."
# T10 review MINOR 5: same "must be greater than zero." copy shape, the
# upper-bound sibling -- `max_upload_bytes` is a `BigIntegerField`
# (Postgres `bigint`, max 2**63-1); see `_ragsettings_field_update`'s own
# docstring for why this is checked at all (an operator-typed 1e308 GB
# overflows `foundation.format.gb_to_bytes`'s `round()` to an uncaught
# `OverflowError` -- a 500 -- without it).
_MAX_UPLOAD_GB_TOO_LARGE_MESSAGE = "Maximum upload size is too large."

# T7: the same "must be a number of minutes." / "...greater than zero."
# copy shape `_MAX_UPLOAD_GB_NOT_A_NUMBER_MESSAGE`/`_MAX_UPLOAD_GB_NOT_POSITIVE_MESSAGE`
# use above -- a duration, like a size, is naturally fractional (half a
# minute is a real value an operator might type), so this gets the same
# float grammar `_upload_cap_update` does, not `history_limit`'s
# whole-number one.
_MAX_MEDIA_MINUTES_NOT_A_NUMBER_MESSAGE = "Maximum media duration must be a number of minutes."
_MAX_MEDIA_MINUTES_NOT_POSITIVE_MESSAGE = "Maximum media duration must be greater than zero."
# T10 review MINOR 5: `max_media_seconds` is a `PositiveIntegerField`
# (Postgres `integer`, max 2**31-1, the SAME real ceiling
# `_MAX_DOCUMENT_PAGES_TOO_LARGE_MESSAGE` below guards for
# `max_document_pages` -- both columns are the same Django field type) --
# an operator-typed minutes value large enough that `round(minutes * 60)`
# either overflows to `inf` (`OverflowError`, same failure class as the
# GB field above) or simply exceeds what the column can store (a DB
# `DataError`) must be rejected here, not surfaced as a 500.
_MAX_MEDIA_MINUTES_TOO_LARGE_MESSAGE = "Maximum media duration is too large."

# T8 review minor 4: the same "must be a whole number." / "...greater than
# zero." copy shape `_history_limit_update`'s own `history_limit` field
# uses -- a page count, like a history-row count, is naturally a whole
# number (there's no such thing as half a page in this cap's own terms),
# so this gets `history_limit`'s int grammar, not the float grammar
# `_upload_cap_update`/`_media_duration_update` use for
# their naturally-fractional GB/minute fields.
_MAX_DOCUMENT_PAGES_NOT_A_NUMBER_MESSAGE = "Maximum document pages must be a whole number."
_MAX_DOCUMENT_PAGES_NOT_POSITIVE_MESSAGE = "Maximum document pages must be greater than zero."
# T10 review MINOR 5: `max_document_pages` is a `PositiveIntegerField`
# (Postgres `integer`, max 2**31-1) -- an operator-typed value beyond
# that (e.g. `9e39`, parsed as a valid whole number by plain `int()`, no
# scientific-notation rejection needed for a bare digit string that long)
# used to reach `settings_row.save()` unchecked and 500 as an uncaught
# `django.db.utils.DataError`.
_MAX_DOCUMENT_PAGES_TOO_LARGE_MESSAGE = "Maximum document pages is too large."

# T10 review MINOR 5, promoted to `foundation.settings_bounds` (S1,
# Coherence Wave B): the real Postgres column ceilings the three
# `_ragsettings_field_update` callers below check their STORED value
# against -- `max_upload_bytes` is a `BigIntegerField` (`bigint`, signed
# 8-byte, true max 2**63-1, `BIGINT_FIELD_MAX`); `max_media_seconds`/
# `max_document_pages` are both `PositiveIntegerField` (`integer`, signed
# 4-byte with a `>= 0` CHECK constraint -- Django's "positive" int fields
# are NOT unsigned, they're a plain signed column plus a constraint, so
# the true ceiling is still 2**31-1, not 2**32-1, `POSITIVE_INT_FIELD_
# MAX`). Checking against the field's own real capacity (not an
# arbitrary round number) means this rejects exactly the inputs that
# would otherwise reach `settings_row.save()` and 500 as an uncaught
# `django.db.utils.DataError` -- nothing more, nothing less. Moved out of
# this module because `_history_limit_update` above (same module) and
# every `JobSettings` field (`models/queue/views.py`) needed the identical
# check and could not import a `tools/rag`-private constant across a
# column boundary.


def _ragsettings_field_update(
    request,
    *,
    raw_key: str,
    field: str,
    cast,
    to_stored,
    max_stored: float,
    not_a_number_message: str,
    not_positive_message: str,
    too_large_message: str,
    success_message,
    min_stored: int = 1,
    raw_min: float | None = None,
    raw_max: float | None = None,
    extra_check: Callable[[Any], str | None] | None = None,
):
    """Shared BODY (T10, audit D9; W4 review item 6) for the five
    RagSettings POST handlers below -- the three GB/minutes/pages caps
    (`_upload_cap_update`/`_media_duration_update`/
    `_document_pages_update`) AND the two W4 retrieval knobs
    (`_retrieval_top_k_update`/`_retrieval_score_floor_
    update`, folded in by this review after having been deliberately left
    out at W4 time -- see the stale comment that used to sit where this
    docstring does; a third bounded field arriving with W5 made "two
    fields whose own small, meaningful range needs a plain-bound check"
    common enough to be worth the shared body after all) -- the same
    parse -> validate -> store -> redirect shape five times over, differing
    only in which POST field/model field they touch, how the typed value is
    parsed (`cast`: `float` for the three naturally-fractional GB/minute/
    score fields, `int` for the two whole-number count fields), how the
    parsed value is converted to what's actually stored (`to_stored`:
    `foundation.format.gb_to_bytes` / `round(minutes * 60)` / identity / `round(
    floor, 2)` with -0.0 normalization -- see
    `_retrieval_score_floor_update`'s own docstring), the ceiling/floor the STORED value
    must respect (`max_stored`/`min_stored` below), and their own error/
    success copy -- kept VERBATIM per call site below, since every existing
    test pins these exact strings.

    `_history_limit_update` (above) is deliberately NOT folded in here
    even though it shares the int grammar with the page-count field: it
    validates `RagSettings.history_limit`, not a unit-converted physical
    cap, and stays its own small view rather than force-fitting a sixth,
    unit-less caller onto a helper whose whole point is the unit
    conversion step.

    BODIES only -- every `@require_POST` view function below keeps its own
    name/signature unchanged, so `tools/rag/urls.py` still binds each by
    its own function object and nothing outside this module needs to know
    they share one implementation.

    `math.isfinite` only matters for the float-cast fields (T4 review
    MAJOR: `float("inf")`/`float("nan")` parse without raising, so an
    unguarded `round(gb * 1024**3)` was an uncaught `OverflowError`, and
    `nan <= 0` is `False`, so `nan` would otherwise sail past the
    positivity check below too) -- skipped when `cast` is `int`, since
    `int("inf")`/`int("nan")` already raise `ValueError` on their own; a
    redundant check there would just be dead code, never actually
    reachable for an int field.

    `min_stored` (W4 review item 6; default `1`, matching every field this
    helper served before this review) is the STORED value's real lower
    bound. `value <= 0` is skipped entirely when `min_stored == 0` --
    `retrieval_score_floor`'s `0.0` is a valid, intentional "off" value, not
    something `not_positive_message`'s "must be greater than zero" grammar
    should ever reject, so a field with `min_stored=0` gets NO upfront
    positivity check at all, only the post-`to_stored` `stored < min_stored`
    check below (which for `min_stored=0` rejects a stored value that's
    genuinely negative -- e.g. an operator-typed `-0.1` -- while letting a
    stored `0.0` through). Every other field keeps `min_stored=1`'s original
    two-check shape: `value <= 0` catches an operator-typed non-positive
    number immediately, and `stored < min_stored` (T10 re-review MINOR 4)
    catches the DIFFERENT failure of a tiny-but-positive `value` (e.g.
    `1e-15` GB) rounding all the way down to a stored `0` -- `value > 0`
    alone doesn't prove `to_stored(value)` preserved that.

    `max_stored` (T10 review MINOR 5) closes the gap `math.isfinite` leaves
    open: it guards `value` itself (the pre-conversion number an operator
    typed), not what `to_stored(value)` actually computes from it. A
    `value` that is itself finite (`1e308` is a perfectly finite `float`)
    can still make `to_stored` overflow -- `gb_to_bytes(1e308)` is
    `round(1e308 * 1024**3)`, and `1e308 * 1024**3` itself overflows to
    `inf` as a `float` (it exceeds `1e308`'s own near-max magnitude), so
    `round(inf)` raises an uncaught `OverflowError` -- or simply land on a
    number too large for the column/range being written. Both failure modes
    are caught the same way here: compute `stored = to_stored(value)`
    inside a `try`, treating an `OverflowError` from THAT call identically
    to `stored` coming back merely too big, and reject both with
    `too_large_message` before ever reaching `settings_row.save()`.
    `max_stored` is each caller's own real ceiling -- the three legacy cap
    fields pass their real Postgres column ceiling
    (`foundation.settings_bounds.BIGINT_FIELD_MAX`/`POSITIVE_INT_FIELD_MAX`,
    a huge number guarding only against an overflow/DB error);
    `retrieval_top_k`/`retrieval_score_floor` instead
    pass their own small, MEANINGFUL range ceiling (`RagSettings.
    RETRIEVAL_TOP_K_MAX`/`RETRIEVAL_SCORE_FLOOR_MAX`) -- this rejects
    exactly the inputs that would otherwise 500 or configure a value
    outside the field's real, intended range, and nothing else.

    `extra_check` (W4 review item 6): an OPTIONAL `callable(value) -> str |
    None`, evaluated after every bounds/stored check above passes and
    BEFORE the settings row is fetched below -- a validation step some
    field needs beyond a plain numeric range, e.g. `retrieval_top_k`'s
    context-window fit check (`_top_k_context_window_fit_check`), which
    needs to resolve a model binding, not just compare numbers. A check
    that needs the settings row itself takes it from
    `settings_row_for(request)` in the caller's own closure (S6), never
    by fetching a second copy -- "before the row is fetched" is no longer
    true of the REQUEST, only of this function: the fit check's own ask
    is what warms the request's stash, and the read below answers from it. Returning a
    non-`None` string rejects the save with that exact message (the SAME
    clean-form-error, never-500 shape as every other rejection above); `None`
    means "no objection, proceed." `None` (no `extra_check` at all) for
    every field that doesn't need one -- the three legacy cap fields, and
    `retrieval_score_floor` (a plain [0, 1] range needs nothing beyond the
    bounds checks already above).

    `raw_min`/`raw_max` (W4 spot-check review item 1) are optional bounds
    checked against the RAW `value` an operator typed, BEFORE `to_stored`
    ever runs. They exist because the `stored < min_stored`/`stored >
    max_stored` checks below validate what `to_stored` PRODUCES, not what
    was typed -- for a `to_stored` that itself rounds (`retrieval_score_
    floor`'s `_rounded_score_floor`, W4 review MINOR 4/5), an out-of-range
    raw input can round right back INTO range and slip past both of those
    checks unrejected: `-0.001` rounds to `-0.0`, normalized to `0.0`;
    `1.004` rounds to `1.0` -- both land inside `[0.0, 1.0]` even though
    neither raw value did. `None` (the default) skips the corresponding
    check entirely -- every field but `retrieval_score_floor` passes
    neither, since only a rounding `to_stored` opens this gap in the first
    place; the four other fields' `to_stored` (`gb_to_bytes`, `round(
    minutes * 60)`, and the two identity conversions) never turns an
    out-of-bounds raw value into an in-bounds stored one, so the
    post-`to_stored` checks already catch everything for them.
    """
    raw_value = request.POST.get(raw_key, "").strip()
    try:
        value = cast(raw_value)
    except ValueError:
        messages.error(request, not_a_number_message)
        return settings_redirect(request, "rag-settings")
    if cast is float and not math.isfinite(value):
        messages.error(request, not_a_number_message)
        return settings_redirect(request, "rag-settings")
    if raw_min is not None and value < raw_min:
        messages.error(request, not_positive_message)
        return settings_redirect(request, "rag-settings")
    if raw_max is not None and value > raw_max:
        messages.error(request, too_large_message)
        return settings_redirect(request, "rag-settings")
    if min_stored != 0 and value <= 0:
        messages.error(request, not_positive_message)
        return settings_redirect(request, "rag-settings")

    try:
        stored = to_stored(value)
    except OverflowError:
        messages.error(request, too_large_message)
        return settings_redirect(request, "rag-settings")
    if stored < min_stored:
        messages.error(request, not_positive_message)
        return settings_redirect(request, "rag-settings")
    if exceeds_field_ceiling(stored, max_stored=max_stored):
        messages.error(request, too_large_message)
        return settings_redirect(request, "rag-settings")

    if extra_check is not None:
        extra_error = extra_check(value)
        if extra_error is not None:
            messages.error(request, extra_error)
            return settings_redirect(request, "rag-settings")

    settings_row = settings_row_for(request)
    setattr(settings_row, field, stored)
    with transaction.atomic():
        settings_row.save(update_fields=[field])
        # S3 (Coherence Wave B): the same `LIBRARY_SETTINGS_UPDATED`
        # action `_history_limit_update` above uses -- one action per
        # settings DOMAIN, `detail` carrying which field changed --
        # written inside the same transaction as the row save.
        audit.record(
            principal_for_request(request), actions.LIBRARY_SETTINGS_UPDATED,
            target_type="ragsettings", target_key=settings_row.pk,
            field=field, to=stored,
        )
    messages.info(request, success_message(value, stored))
    return settings_redirect(request, "rag-settings")


def _upload_cap_update(request):
    """`field=max_upload_gb` -- update
    `RagSettings.max_upload_bytes` (T4), the sibling settings write to
    `_history_limit_update` above -- a separate view/form rather than a
    second field folded into that one, so each cap can be saved
    independently without also re-validating (and re-submitting) the
    other.

    Entered in GB (decimals allowed, unlike `history_limit`'s whole-number
    field) -- blank/non-numeric/non-positive is a clean form error, never
    a 500, same never-500 shape as `_history_limit_update` and mirroring
    `models.registry.views.connection_add`'s own `footprint_gb` copy
    shape exactly (see the constants above). Stored via
    `foundation.format.gb_to_bytes` -- the SAME 1024 base `foundation.format.
    human_bytes` renders with, so a value the operator typed (e.g. 8.5)
    reads back identically (`HistoryView`'s own `max_upload_gb` context,
    test-pinned round-trip).

    Takes effect immediately for the NEXT upload/watch-folder check --
    nothing already queued is re-evaluated retroactively. Body shared with
    the two sibling views below via `_ragsettings_field_update` (T10).
    """
    return _ragsettings_field_update(
        request,
        raw_key="max_upload_gb",
        field="max_upload_bytes",
        cast=float,
        to_stored=gb_to_bytes,
        max_stored=BIGINT_FIELD_MAX,
        not_a_number_message=_MAX_UPLOAD_GB_NOT_A_NUMBER_MESSAGE,
        not_positive_message=_MAX_UPLOAD_GB_NOT_POSITIVE_MESSAGE,
        too_large_message=_MAX_UPLOAD_GB_TOO_LARGE_MESSAGE,
        success_message=lambda gb, stored: f"Maximum upload size set to {gb:.1f} GB.",
    )


def _media_duration_update(request):
    """`field=max_media_minutes` -- update
    `RagSettings.max_media_seconds` (T7), the media-duration sibling of
    `_upload_cap_update` above -- it's true now (T7 wires
    `tools.rag.ingest._check_media_duration` to actually enforce this
    cap), so the field renders and is editable regardless of whether the
    "media" feature is enabled, the same way `max_upload_bytes` was
    editable before any media medium existed to enforce it against.

    Entered in MINUTES (decimals allowed) -- blank/non-numeric/non-positive/
    non-finite is a clean form error, never a 500, the exact same
    never-500/reject-inf-and-nan shape `_upload_cap_update` uses
    (see `_ragsettings_field_update`'s own docstring for why
    `math.isfinite` matters here, not just a bare positivity check).
    Stored as `round(minutes * 60)` -- the SAME 60x base `HistoryView`'s
    own `max_media_minutes` context divides by, so a value the operator
    typed (e.g. 90) reads back identically (test-pinned round-trip,
    matching `max_upload_gb`'s own).

    Takes effect immediately for the NEXT stage/transcribe check -- nothing
    already staged or queued is re-evaluated retroactively. Body shared
    with the two sibling views via `_ragsettings_field_update` (T10).
    """
    return _ragsettings_field_update(
        request,
        raw_key="max_media_minutes",
        field="max_media_seconds",
        cast=float,
        to_stored=lambda minutes: round(minutes * 60),
        max_stored=POSITIVE_INT_FIELD_MAX,
        not_a_number_message=_MAX_MEDIA_MINUTES_NOT_A_NUMBER_MESSAGE,
        not_positive_message=_MAX_MEDIA_MINUTES_NOT_POSITIVE_MESSAGE,
        too_large_message=_MAX_MEDIA_MINUTES_TOO_LARGE_MESSAGE,
        success_message=lambda minutes, stored: f"Maximum media duration set to {minutes:.1f} minutes.",
    )


def _document_pages_update(request):
    """`field=max_document_pages` -- update
    `RagSettings.max_document_pages` (T8 review minor 4), the page-count
    sibling of `_media_duration_update` above -- a separate view/
    form rather than a field folded into that one, same rationale as
    every other independent settings write on this page.

    Entered as a plain page COUNT (whole number, unlike the two fields
    above) -- blank/non-numeric/non-positive is a clean form error, never
    a 500, the exact `_history_limit_update` int-field shape (see that
    view's own docstring).

    Takes effect immediately for the NEXT stage/extract check -- nothing
    already staged or queued is re-evaluated retroactively. Body shared
    with the two sibling views above via `_ragsettings_field_update` (T10)
    -- `to_stored` is the identity function here: a page count IS the
    value stored, no unit conversion needed, unlike the two fields above.
    """
    return _ragsettings_field_update(
        request,
        raw_key="max_document_pages",
        field="max_document_pages",
        cast=int,
        to_stored=lambda pages: pages,
        max_stored=POSITIVE_INT_FIELD_MAX,
        not_a_number_message=_MAX_DOCUMENT_PAGES_NOT_A_NUMBER_MESSAGE,
        not_positive_message=_MAX_DOCUMENT_PAGES_NOT_POSITIVE_MESSAGE,
        too_large_message=_MAX_DOCUMENT_PAGES_TOO_LARGE_MESSAGE,
        success_message=lambda pages, stored: f"Maximum document pages set to {pages}.",
    )


# W4 (ADR 0014 §14): retrieval settings copy. Both fields below now route
# through `_ragsettings_field_update` (W4 review item 6) -- `max_stored`/
# `min_stored` there are each caller's OWN ceiling/floor, not necessarily a
# Postgres column ceiling, so a field with a small, meaningful range of its
# own (`RagSettings.RETRIEVAL_TOP_K_MIN`..`_MAX`, `RETRIEVAL_SCORE_FLOOR_
# MIN`..`_MAX`) fits the shared helper exactly as well as the three legacy
# cap fields' huge column ceilings did.
_RETRIEVAL_TOP_K_NOT_A_NUMBER_MESSAGE = "Retrieval top_k must be a whole number."
_RETRIEVAL_TOP_K_TOO_SMALL_MESSAGE = (
    f"Retrieval top_k must be at least {RagSettings.RETRIEVAL_TOP_K_MIN}."
)
_RETRIEVAL_TOP_K_TOO_LARGE_MESSAGE = (
    f"Retrieval top_k can't be more than {RagSettings.RETRIEVAL_TOP_K_MAX}."
)
_RETRIEVAL_SCORE_FLOOR_NOT_A_NUMBER_MESSAGE = "Retrieval score floor must be a number."
_RETRIEVAL_SCORE_FLOOR_OUT_OF_RANGE_MESSAGE = (
    f"Retrieval score floor must be between {RagSettings.RETRIEVAL_SCORE_FLOOR_MIN:.1f} "
    f"and {RagSettings.RETRIEVAL_SCORE_FLOOR_MAX:.1f}."
)


def _resolved_answer_context_window() -> int | None:
    """`rag.answer`'s configured context window (`ResolvedModel.config.get(
    "context_window")`), or `None` when it's unknown -- lifted out of what
    used to be `_top_k_context_window_fit_check`'s own body (W4 review
    MINOR 6) so W5's `_effective_k_fit_check` below can share it rather
    than re-resolving the binding a second, independent way.

    A per-connection OVERRIDE (`models.registry.bindings.resolved_from_
    connection`'s own docstring: unset means "no opinion", so it's simply
    absent from `config`, not `None` written into it -- the engine adapter's
    own bounded default applies instead, invisible to this check), cast to
    `int` (a `context_window` override is never written as anything but an
    int today, but nothing enforces that -- a string value would otherwise
    reach a `required_tokens > context_window` comparison and raise an
    uncaught `TypeError`, a 500, rather than degrading cleanly).

    Two distinct ways the window can be UNKNOWN, logged differently (W4
    review MINOR 6): `rag.answer` has no binding at all -- `resolve()`
    raises the documented `ValueError` -- is the EXPECTED, everyday case
    (an operator hasn't bound the role yet), logged at `debug`. Anything
    else -- an `OperationalError` out of a DB-backed `resolve()`, or a
    `context_window` override that can't `int()`-cast -- is NOT that
    documented shape, so it's logged at `warning` with `exc_info=True`
    (never silently swallowed) before degrading the same way.
    """
    try:
        context_window = resolve(RAG_ANSWER_ROLE).config.get("context_window")
    except ValueError:
        context_window = None
        logger.debug(
            "_resolved_answer_context_window: rag.answer's context window is unknown "
            "(no binding, or a binding with no context_window override) -- skipping the fit check"
        )
    except Exception:  # noqa: BLE001 -- unexpected (e.g. a DB error resolving the binding); degrade loudly, never 500
        context_window = None
        logger.warning(
            "_resolved_answer_context_window: unexpected error resolving rag.answer's "
            "context window -- degrading to unknown window (fit check skipped)",
            exc_info=True,
        )
    else:
        if context_window is not None:
            try:
                context_window = int(context_window)
            except ValueError:
                context_window = None
                logger.warning(
                    "_resolved_answer_context_window: rag.answer's context_window override "
                    "is not an integer -- degrading to unknown window (fit check skipped)",
                    exc_info=True,
                )
    return context_window or None


def _effective_k_fit_check(effective_k: int, top_k: int) -> str | None:
    """The shared context-window arithmetic (W5 review S7) behind BOTH
    `_retrieval_top_k_update` (via `_top_k_context_window_fit_
    check`, `effective_k = top_k`, doubled while hybrid is already on) and
    `_hybrid_search_update` (`effective_k = 2 * the STORED top_k`,
    checked before the toggle is saved ON) -- ONE helper so the two views
    cannot independently drift on what "fits" means. Returns an error
    message string when it doesn't fit, `None` when it does (or when
    there's nothing concrete to check against at all).

    `effective_k` (not `top_k`) is what this checks against -- in HYBRID
    mode, `tools.rag.retrieval.answer_question` sets `sparse_top_k =
    retrieval_top_k`, and the store's own `_hybrid_query` concatenates
    dense + sparse results without fusion -- up to `similarity_top_k +
    sparse_top_k` deduped nodes, i.e. DOUBLE `top_k`, not `top_k` itself.

    `top_k` is taken separately (W5 review m3) so the refusal MESSAGE can
    name what the operator actually typed/has stored, not a term
    (`effective_k`) that appears nowhere in the UI: `top_k=4` for a
    non-hybrid install (where `effective_k == top_k`, and printing
    "effective_k=4" for an operator who only ever set `top_k` would be a
    confusing, undocumented synonym), and `top_k=4 (×2 for hybrid = 8
    chunks)` when the two differ -- W5 review N5(a)'s original concern
    (a message that dropped the doubling entirely, printing the bare
    `top_k` while actually rejecting on double that) still holds; this
    keeps BOTH numbers in the sentence rather than picking one.

    Same degrade-on-unknown-window behavior W4 shipped (W4 review MINOR 6;
    W5 review N5(b)): when `rag.answer` is unbound, or bound with no
    `context_window` override, there's nothing concrete to check against,
    so the value is ACCEPTED with no fit check at all -- this is the
    intended degrade, not a gap a later reader should "fix" into a refusal.

    Save-time only (W4 review MINOR 7): validates the pairing ONCE, against
    whatever `rag.answer` is bound to right now. A later rebind to a
    smaller-window model is not re-checked, and `answer_question` never
    clamps `top_k`/`sparse_top_k` at ask time either -- a stale value that
    no longer fits surfaces as a real synthesis-time failure, not a silent
    truncation.
    """
    context_window = _resolved_answer_context_window()
    if not context_window:
        logger.debug(
            "_effective_k_fit_check: rag.answer's context window is unknown; "
            "accepting effective_k=%s without a fit check",
            effective_k,
        )
        return None

    required_tokens = effective_k * ingest.CHUNK_TOKENS + ingest.RESPONSE_RESERVE
    if required_tokens > context_window:
        k_desc = (
            f"top_k={top_k}"
            if effective_k == top_k
            else f"top_k={top_k} (×2 for hybrid = {effective_k} chunks)"
        )
        return (
            f"{k_desc} would need about {required_tokens} tokens "
            f"({effective_k} × {ingest.CHUNK_TOKENS} chunk tokens + {ingest.RESPONSE_RESERVE} "
            f"reserved for the prompt and answer), but the bound chat model's context "
            f"window is only {context_window} tokens — lower top_k, or raise the model's "
            "context window in the model console."
        )
    return None


def _top_k_context_window_fit_check(top_k: int, settings_row) -> str | None:
    """`extra_check` callable for `_retrieval_top_k_update`'s
    `_ragsettings_field_update` call (W4, ADR 0014 §14; W4 review MINOR 6):
    a `top_k` too large for the bound `rag.answer` model's own context
    window would configure a value the model can never actually be asked
    with (an oversized synthesis prompt truncates or errors deep in the
    engine, far from this form, the next time someone asks a question --
    the failure would surface nowhere near its actual cause).

    W5 (ADR 0014 §18): while `RagSettings.hybrid_search` is on, HYBRID
    mode's `sparse_top_k = retrieval_top_k` means the store can return up
    to `2 * top_k` deduped nodes, not `top_k` -- so this checks
    `effective_k = top_k * 2` in that case, `top_k` itself otherwise, via
    the shared `_effective_k_fit_check` (also used by
    `_hybrid_search_update`, so the two views cannot drift on the doubling rule).

    W5 review m2: doubling on the TOGGLE alone under-counted the DISABLE
    window -- an operator who flips `hybrid_search` off without
    re-encoding yet is still querying the LIVE table, which is still
    hybrid (composition rule 2 keys off `rag_index.live_store_shape()`,
    never the toggle) until that re-encode actually rebuilds it, so
    `sparse_top_k` is still populated and the store can still return
    double. Doubles when EITHER the toggle OR the live table's own shape
    is hybrid -- one `live_store_shape()` read at save time (a single
    cheap `pg_attribute` lookup, same cost `get_vector_store()` already
    pays per call) is an acceptable price for a correct check;
    `live_store_shape() is None` (nothing ingested yet -- no live table to
    be hybrid) falls back to the toggle alone, matching every other
    `None`-means-"nothing to disagree with yet" reading of that helper
    elsewhere in this module.

    That read is wrapped in its own try/except (a `ProgrammingError`/
    `OperationalError` -- the table not existing yet in some deployment
    order, or any other DB hiccup on this single cheap lookup): the save
    this check gates must never 500 on it, so an error here degrades
    exactly like the documented `None` case above (toggle alone), logged
    at `warning` with `exc_info=True` since -- unlike `live_store_shape()`
    returning a legitimate `None` -- it is not an expected shape.

    `settings_row` (S6) is THIS REQUEST's row, threaded in by
    `_retrieval_top_k_update` rather than fetched here. It used to be a
    second, independent `get_solo()` -- the only place on this page where
    one POST paid two reads of the same row, microseconds apart, one for
    the toggle this check reads and one for the row
    `_ragsettings_field_update` then saves onto. Required, not optional:
    this function is never called except as that handler's `extra_check`,
    so there is no caller without a row to hand it, and a default would
    only make the second read reachable again by accident.
    """
    from django.db import OperationalError, ProgrammingError

    hybrid_toggle = settings_row.hybrid_search
    try:
        live_shape = rag_index.live_store_shape()
    except (ProgrammingError, OperationalError):
        live_shape = None
        logger.warning(
            "_top_k_context_window_fit_check: live_store_shape() failed -- "
            "degrading to the hybrid_search toggle alone for the doubling check",
            exc_info=True,
        )
    hybrid = hybrid_toggle or (live_shape is not None and live_shape["hybrid"])
    effective_k = top_k * 2 if hybrid else top_k
    return _effective_k_fit_check(effective_k, top_k)


def _retrieval_top_k_update(request):
    """`field=retrieval_top_k` -- update
    `RagSettings.retrieval_top_k` (W4, ADR 0014 §14): how many vector chunks
    `tools.rag.retrieval.answer_question` retrieves per question.

    Whole-number bounds check (`RagSettings.RETRIEVAL_TOP_K_MIN`..`_MAX`,
    i.e. 1..50 today) -- the same never-500, clean-form-error int grammar
    `_history_limit_update`/`_document_pages_update` use, via
    `_ragsettings_field_update` (W4 review item 6: `to_stored` is identity,
    `max_stored` is `RETRIEVAL_TOP_K_MAX` rather than a Postgres column
    ceiling, and `min_stored` stays the default `1` -- matching `MIN`).

    Context-window fit check, additional to every sibling settings field on
    this page: threaded in as `extra_check` -- see
    `_top_k_context_window_fit_check`'s own docstring for the exact rule
    (including its save-time-only caveat, W4 review MINOR 7).
    """
    return _ragsettings_field_update(
        request,
        raw_key="retrieval_top_k",
        field="retrieval_top_k",
        cast=int,
        to_stored=lambda top_k: top_k,
        max_stored=RagSettings.RETRIEVAL_TOP_K_MAX,
        not_a_number_message=_RETRIEVAL_TOP_K_NOT_A_NUMBER_MESSAGE,
        not_positive_message=_RETRIEVAL_TOP_K_TOO_SMALL_MESSAGE,
        too_large_message=_RETRIEVAL_TOP_K_TOO_LARGE_MESSAGE,
        success_message=lambda top_k, stored: f"Retrieval top_k set to {top_k}.",
        # S6: the fit check reads `hybrid_search` off THIS REQUEST's row
        # rather than fetching a second copy of it. Looked up through the
        # module rather than closed over directly so the non-vacuity
        # mutation in `TestTheSingleSettingsReadPerRequest` can replace
        # it the way a monkeypatch expects to be able to.
        extra_check=lambda top_k: _top_k_context_window_fit_check(
            top_k, settings_row_for(request)
        ),
    )


def _rounded_score_floor(floor: float) -> float:
    """`to_stored` for `_retrieval_score_floor_update` (W4 review
    MINOR 4/5): rounds to 2 decimal places -- matching the form's own
    `step="0.01"` and the `:.2f` success-message grammar -- so an untouched
    re-save of whatever value the settings page itself rendered back can
    never drift the stored value (an operator-typed `0.005`, otherwise
    stored raw, would sit one step below what the 2dp UI can even
    represent, and every subsequent "no-op" re-save of a value that ALREADY
    looked rounded would still be silently lossy). Also normalizes a
    rounded `-0.0` to plain `0.0` -- `round(-0.0, 2)` is `-0.0`, floating
    point's own signed-zero, which is numerically equal to `0.0` (`-0.0 ==
    0.0` is `True`) but renders as the confusing `"-0.00"` under `:.2f`
    formatting rather than the intended `"0.00"` ("off")."""
    rounded = round(floor, 2)
    return 0.0 if rounded == 0.0 else rounded


def _retrieval_score_floor_update(request):
    """`field=retrieval_score_floor` -- update
    `RagSettings.retrieval_score_floor` (W4, ADR 0014 §14): the minimum
    RAW COSINE SIMILARITY (W4 review MINOR 3 -- this store's own score,
    verified against pgvector: `1 - cosine_distance`, NOT a normalized
    [0, 1] "confidence"; a useful threshold depends on which embedding
    model is bound to ingestion/retrieval, there's no universal value) a
    retrieved chunk must clear to reach synthesis/citations at all -- `0.0`
    is OFF.

    A float bounded `RagSettings.RETRIEVAL_SCORE_FLOOR_MIN`..`_MAX` (0.0..1.0
    today, the form's own input bounds -- kept even though a raw cosine
    similarity can technically go negative, since this store's own
    similarity search never surfaces a usefully-negative match) -- blank/
    non-numeric/NaN/inf/out-of-range is a clean form error, never a 500,
    the same `math.isfinite` guard `_ragsettings_field_update`'s float
    fields use. Routed through that shared helper (W4 review item 6) with
    `min_stored=0` -- unlike every OTHER `_ragsettings_field_update` caller,
    `0.0` is this field's valid, intentional default, not a value
    `not_positive_message` should ever reject -- and `to_stored=
    _rounded_score_floor` (W4 review MINOR 4/5) rounding/normalizing what's
    actually stored.

    `raw_min`/`raw_max` (W4 spot-check review item 1) are also passed here,
    pinned to this field's own `RETRIEVAL_SCORE_FLOOR_MIN`/`_MAX` --
    `_rounded_score_floor` ROUNDS, so checking bounds on `stored` alone (as
    every other bounded field here safely does) would accept an
    out-of-range raw input that happens to round back into range, e.g.
    `-0.001` (rounds to `-0.0`, normalized to `0.0`) or `1.004` (rounds to
    `1.0`) -- both would otherwise sail past the post-rounding checks even
    though neither was actually a value between 0.0 and 1.0.
    """
    return _ragsettings_field_update(
        request,
        raw_key="retrieval_score_floor",
        field="retrieval_score_floor",
        cast=float,
        to_stored=_rounded_score_floor,
        max_stored=RagSettings.RETRIEVAL_SCORE_FLOOR_MAX,
        min_stored=0,
        raw_min=RagSettings.RETRIEVAL_SCORE_FLOOR_MIN,
        raw_max=RagSettings.RETRIEVAL_SCORE_FLOOR_MAX,
        not_a_number_message=_RETRIEVAL_SCORE_FLOOR_NOT_A_NUMBER_MESSAGE,
        not_positive_message=_RETRIEVAL_SCORE_FLOOR_OUT_OF_RANGE_MESSAGE,
        too_large_message=_RETRIEVAL_SCORE_FLOOR_OUT_OF_RANGE_MESSAGE,
        success_message=lambda floor, stored: f"Retrieval score floor set to {stored:.2f}.",
    )


# W5 (ADR 0014 §18): every clause below is written to stay literally true
# regardless of when an operator reads it relative to the next re-encode --
# `tools.rag.index.get_vector_store`'s shape rule means the store built
# for the NEXT ask still describes whatever table currently EXISTS, not
# this toggle, until a re-encode actually rebuilds it.
_HYBRID_ENABLE_MESSAGE = (
    "Hybrid search enabled. Re-encode the index (Models → rag.embed → Re-encode) to "
    "rebuild the chunk table — search stays semantic-only until then. The rebuild "
    "re-embeds every chunk in the library and can take a long time. After the rebuild, "
    "the score floor is no longer applied."
)
# W5 review N6: names the floor explicitly, and the same "stays as it is"
# language as the keyword-matching clause -- composition rule 2
# (`tools.rag.retrieval.answer_question`) branches on the LIVE store's
# own `hybrid_search` field, not this toggle, so the floor stays suspended
# for exactly as long as keyword matching stays live: until the re-encode
# actually completes. An operator disabling this specifically to get their
# floor back must not be left believing it already has.
_HYBRID_DISABLE_MESSAGE = (
    "Hybrid search disabled. Re-encode the index to rebuild the chunk table — keyword "
    "matching, and the suspended score floor, both stay as they are until then."
)
# W5 review m4: every OTHER RagSettings field on this page rejects an
# unparseable POST value with a clean form error rather than silently
# picking a default -- this checkbox used to be the one exception,
# treating literally any value other than the exact string "on" (a typo'd
# "true"/"1"/"yes" from a hand-built request, say) as "off" with no
# complaint at all. `""` (name present, empty value) and `"off"` are
# still accepted as "off" -- an unchecked HTML checkbox omits the field
# entirely, but nothing stops a form/client from sending either of those
# two explicitly.
_HYBRID_SEARCH_INVALID_VALUE_MESSAGE = (
    'Invalid hybrid_search value -- expected "on", or the field left unset/"off".'
)


def _hybrid_search_update(request):
    """`field=hybrid_search` -- update `RagSettings.
    hybrid_search` (W5, ADR 0014 §18): the operator toggle for hybrid
    keyword+vector search.

    A checkbox, not a bounded number, so it does NOT route through
    `_ragsettings_field_update` -- that helper's shape (parse a number,
    check it against a range, convert, store) fits its five siblings above
    but has nothing to offer a two-value boolean; this gets its own small
    body instead of a forced-fit sixth caller (matching that helper's own
    docstring, which explicitly scopes it to numeric fields).

    Enabling re-runs the SAME context-window fit check `_retrieval_top_k_
    update` runs, against DOUBLE the currently-stored `top_k`
    (`_effective_k_fit_check`, shared with that view so the two can never
    independently drift on the doubling rule -- W5 review S7): HYBRID
    mode's `sparse_top_k = retrieval_top_k` means the store can return up
    to `2 * top_k` deduped nodes, and an already-saved top_k that fits
    singly might not fit doubled. Refused with the same clean-form-error,
    never-500 shape as every other settings write on this page -- see
    `_effective_k_fit_check`'s own docstring for the exact rule, including
    its "unknown window, accept" degrade (W5 review N5(b)). Disabling never
    re-runs this check: dropping to dense-only only ever SHRINKS the token
    budget the currently-saved top_k already passed.

    Flipping this toggle saves the field immediately but changes NOTHING
    about what's actually queried until a re-encode (Inference -> rag.embed
    -> Re-encode) rebuilds the chunk table in the new shape -- both flash
    messages below (`_HYBRID_ENABLE_MESSAGE`/`_HYBRID_DISABLE_MESSAGE`) say
    so explicitly, and the enable-direction one (W5 review R2) is careful
    to say the score floor is STILL applied UNTIL the rebuild, not that it
    stops immediately -- composition rule 2 keys off the LIVE store's own
    `hybrid_search` field, not this toggle.

    W5 review m4: only `"on"` (checked), `""` (present but empty), and
    `"off"` are recognized values for the raw POST field -- an unchecked
    HTML checkbox simply omits it, which `.get(..., "")` treats the same
    as `""`. Anything else (e.g. a hand-built request sending `"true"`)
    is a clean form error, the same never-500 "reject what we can't
    honestly parse" shape every sibling field on this page already uses,
    rather than silently falling through to "off" the way a bare `== "on"`
    comparison used to.
    """
    settings_row = settings_row_for(request)
    raw_hybrid_search = request.POST.get("hybrid_search", "")
    if raw_hybrid_search not in ("on", "", "off"):
        messages.error(request, _HYBRID_SEARCH_INVALID_VALUE_MESSAGE)
        return settings_redirect(request, "rag-settings")
    enable = raw_hybrid_search == "on"

    if enable:
        effective_k = settings_row.retrieval_top_k * 2
        error = _effective_k_fit_check(effective_k, settings_row.retrieval_top_k)
        if error is not None:
            messages.error(request, error)
            return settings_redirect(request, "rag-settings")

    settings_row.hybrid_search = enable
    with transaction.atomic():
        settings_row.save(update_fields=["hybrid_search"])
        # S3 (Coherence Wave B): same `LIBRARY_SETTINGS_UPDATED` action
        # every other `RagSettings` field write in this module uses.
        audit.record(
            principal_for_request(request), actions.LIBRARY_SETTINGS_UPDATED,
            target_type="ragsettings", target_key=settings_row.pk,
            field="hybrid_search", to=enable,
        )
    messages.info(request, _HYBRID_ENABLE_MESSAGE if enable else _HYBRID_DISABLE_MESSAGE)
    return settings_redirect(request, "rag-settings")


# --- S2 (Coherence Wave C): ONE dispatched settings endpoint ---------------
#
# THE SEVEN PER-FIELD URLS ARE RETIRED. `rag-history-settings` and its six
# siblings each had their own path, their own `ROUTE_RULES` class and their
# own route-matrix driver -- twenty-one touch points to keep in step for
# seven writes to ONE table on ONE page (7 URLs x 3 places each; `urls.py`'s
# own version of this sentence counts them the other way round, "seven
# paths, seven route classes and seven route-matrix drivers"), and three
# more every time a field was added. `models.queue.views.queue_settings_update` had already
# settled the other shape for `JobSettings` (one endpoint, a hidden field
# saying which form submitted); the settings-backend audit's S2 named the
# divergence and the orchestrator's ruling made the queue's shape the
# sanctioned one. `docs/EXTENDING.md`'s backend recipe now states it as a
# rule, so the next settings page starts here rather than re-deciding.
#
# KEYED BY THE POST FIELD NAME each form already submits (`history_limit`,
# `max_upload_gb`, ...), not by an invented action vocabulary: the hidden
# `field` input and the input it names are the same string, so a form
# cannot dispatch to a handler that reads some OTHER form's field.
#
# One caveat, and it is the checkbox (N3, Wave C review): `hybrid_search`
# is the one field whose key is ABSENT from the POST when the control is
# unchecked -- that absence IS the off signal, and `_hybrid_search_update`
# reads it as one. So "the key the `field` value names is always present"
# is false for exactly that form; what holds for all seven is that the
# `field` value always names the input THIS form owns, checked or not.
#
# THE HANDLERS THEMSELVES ARE UNCHANGED -- same parse, same bounds checks,
# same `foundation.settings_bounds` ceilings, same audit row, same flash
# copy, same redirect. They lost only their `@require_POST` decorator and
# their public names; this endpoint carries the decorator once.
_LIBRARY_SETTINGS_FIELDS = {
    "history_limit": _history_limit_update,
    "max_upload_gb": _upload_cap_update,
    "max_media_minutes": _media_duration_update,
    "max_document_pages": _document_pages_update,
    "retrieval_top_k": _retrieval_top_k_update,
    "retrieval_score_floor": _retrieval_score_floor_update,
    "hybrid_search": _hybrid_search_update,
}


@require_POST
def library_settings_update(request):
    """POST /rag/settings/update/ -- the ONE write endpoint the Library
    page's seven forms post to (S2, Coherence Wave C). A hidden `field`
    input says which form submitted, and `_LIBRARY_SETTINGS_FIELDS` above
    maps it to the handler that validates and saves that one field.

    AN UNRECOGNISED OR MISSING `field` IS A FLASH AND A REDIRECT, never a
    500 and never a raw 400: the F7 convention this branch applied to
    every other settings surface. It is deliberately NOT a silent no-op
    either -- an operator whose tampered-with or stale form saved nothing
    must be told, or an unchanged page reads as success.

    NOTHING IS SAVED BEFORE THE DISPATCH, so a refusal here cannot leave
    `RagSettings` half-written: the handlers are the only writers, and one
    of them either runs entire or does not run.

    EVERY WAY BACK TO THIS PAGE -- this dispatcher's refusal and all
    seven handlers' own success and error redirects -- goes through
    `foundation.settings_area.settings_redirect`, which carries the
    assistant panel's open flag when the submitting form's `action`
    carried one (persistence round). Saving a setting with the panel up
    must not shut it; a save without it redirects exactly where it
    always did.
    """
    field = request.POST.get("field", "").strip()
    handler = _LIBRARY_SETTINGS_FIELDS.get(field)
    if handler is None:
        messages.error(request, f"{field!r} is not a recognised settings form.")
        return settings_redirect(request, "rag-settings")
    return handler(request)
