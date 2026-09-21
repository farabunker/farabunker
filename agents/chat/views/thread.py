"""One conversation, read.

A GET here NEVER 503s. An unbound chat role, an unreachable engine, and
an unavailable queue are all reasons a NEW turn cannot be queued -- they
are not reasons the operator may not read what was already said. The 503
belongs to the POST; this page shows a banner and renders the
thread.

Reading an agent that was later disabled is not a special case here:
`Conversation.agent` is `on_delete=models.PROTECT` (`agents/models.py`),
so an agent can never be deleted out from under a conversation that
references it, and a disabled agent (`enabled=False`) is still a
perfectly readable row -- `visible_agents`'s `enabled=True` filter is a
STARTABILITY rule for the index and the start form, not a readability
rule for a thread that already exists. This view never calls
`visible_agents` at all; it reads `conversation.agent` directly, so a
disabled agent's past conversation still renders in full.
"""
from __future__ import annotations

from django.urls import NoReverseMatch, reverse
from django.views.generic import TemplateView

from agents.attachments import attachments_for
from agents.chat.pickers import chat_picker_options
from agents.chat.rendering import thread_cards
from agents.chat.sidebar import sidebar_context
from agents.chat.service import (
    MAX_POLL_DURATION_MS, MAX_TRANSPORT_RETRIES, POLL_INTERVAL_MS,
    composer_attach_context, visible_conversation_or_404,
)
from agents.entitlements import tool_access_for, wall_for
from agents.models import Share
from agents.runtime.preflight import dropped_tool_notes, preflight_turn
from agents.shares import shares_for
from agents.usage import (
    BAND_FULL, DISCLOSURE_BODY, DISCLOSURE_SUMMARY, ENGINE_DEFAULT_SENTENCE, FULL_CLAUSE,
    WINDOW_SOURCE_ENGINE_DEFAULT, WINDOW_SOURCE_UNBOUND, context_usage, meter_segments,
    truncation_clause,
)
from agents.visibility import may_post_to, may_read_conversation_shares
from agents.workstreams import scope_for_conversation
from identity.access import accounts_on, is_admin, share_subjects
from identity.request import principal_for_request, settings_row_for
from models.contracts.bindings import effective_context_window


def thread_context(request, conversation, *, selected: str | None = None) -> dict:
    """Everything the conversation page needs, built ONCE.

    The 503 re-render (below) calls this too, so an error response can
    never show a different thread -- or a different picker selection --
    than a success would. That is the same reason
    `tools/vision/views.py::_create_page_context` exists.

    `selected` is an ARGUMENT rather than a `request.GET` read, because
    the 503 re-render happens on a POST: the pick is in `request.POST`
    there, and a builder that only ever looked at the query string
    would silently revert the operator's chosen model on the one
    render where they are least able to notice. `None` means "read the
    query string", which is the GET path's own behaviour.
    """
    if selected is None:
        selected = request.GET.get("connection", "")
    # THE SAME PREFLIGHT `start_turn` runs, computed here for a
    # DIFFERENT purpose: `start_turn` turns a refusal into a 503,
    # this turns it into a BANNER, NOT A STATUS CODE. A GET always
    # renders 200 -- `check.message` is only copy the template may show
    # above the message form (this module's own 503 re-render docstring
    # above says the same thing; restated here because this is the
    # call site that makes it true). Computing it
    # with the SAME function `preflight_turn`, over the SAME
    # `(agent, connection)` pair `start_turn` was just given, is also
    # what makes a granted-but-unregistered tool's note survive the
    # no-JS redirect: the thread's own GET recomputes it identically.
    principal = principal_for_request(request)
    # `accounts_on()` FIRST (review finding, 2026-08-31): `sees_all_
    # content` answers True unconditionally on an open box (there is
    # nobody for anything to be hidden from), so without this the
    # `shares_for` query below ran on every open-box thread GET --
    # exactly the zero-permission-query rule every other visibility
    # body in this column tests its open branch first to keep true.
    may_see_shares = accounts_on() and may_read_conversation_shares(principal, conversation)
    # ONE `IdentitySettings` READ for this whole render (settings_row
    # lesson): fetched once here and threaded into `tool_access_for`
    # below AND into `sidebar_context` at the bottom of this function,
    # rather than each computing its own.
    settings_row = settings_row_for(request)
    # ROUND 9's ATTACH DOOR (owner feedback: "I also don't have the
    # ability in the chat to add any additional objects like photos/
    # documents/pdfs/ect"). THE GENERAL PREDICATE, not the Documents
    # panel's OWNER-ONLY one: `_working_context`'s own `may_upload_tool`
    # answers False for anyone but the stream's owner (that section is
    # itself `is_owner`-gated first), but reading and posting into a
    # conversation is not an ownership question here -- any principal
    # who holds `rag.ingest` at all may see this door, in or out of a
    # workstream.
    # I1 (round 9 review): this SAME no-wall `ToolAccess` value is handed
    # to `preflight_turn` below too (its own `tool_access=` keyword) --
    # `held_entitlement_ids`/`tool_entitlement_ids()` are unpinned, real
    # queries, and without threading this through, `preflight_turn`'s own
    # internal `tool_access_for` call would re-run both for the identical
    # principal on the identical render. See that function's own
    # docstring for exactly when it reuses this value and when it does
    # not (a non-empty wall always recomputes fresh).
    rag_tool_access = tool_access_for(principal, settings_row=settings_row)
    # THE STREAM SCOPE, resolved whenever there IS one -- round 11
    # review I-3 widens this from "only when `may_attach_files`" (this
    # round's first cut) to unconditional: the attachments strip below
    # needs THIS SAME `WorkstreamScope` (wall included) to corpus-filter
    # honestly even for a principal with no attach rights at all (a
    # `view`-level share recipient, say), and computing it a second time
    # for that purpose would be the exact re-derivation `agents.
    # attachments.attached_documents`'s own `settings_row` threading
    # exists to avoid one query below. `None` for a loose conversation,
    # unchanged.
    stream_scope = scope_for_conversation(principal, conversation)
    # AUDIT 2, F5: THE ONE COMPOSER'S GATE PREDICATE, COMPUTED IN ONE
    # PLACE. `chat/_composer.html` reads exactly two keys to decide
    # whether its attach door renders, and this surface used to compute
    # both itself, in an expression structurally identical to `composer_
    # attach_context`'s own. That function's docstring recorded why it
    # had not been shared -- "reusing IT here would mean threading an
    # already-resolved scope through two call shapes that share no
    # caller" -- and ROUND-18-CONFIRM R7 retired exactly that objection
    # by giving it `may_upload=`. One fragment, one predicate now.
    #
    # `may_upload_here` IS `stream_scope.may_upload` under another name
    # -- `may_manage_workstream` under yet another (`agents/workstreams.
    # py::_scope_from_row`), the SAME predicate `rag/panels/documents.
    # html`'s own Upload section gates on. Passing it in is not trusting
    # a different layer's math; this render resolved that exact scope
    # above, for the attachments strip, and `composer_attach_context`'s
    # own `may_upload=` docstring gives the full reasoning.
    #
    # THE FK IS DEREFERENCED ONLY WHERE THE OLD CODE DID (audit-2
    # confirm, finding 3). `visible_conversations` only
    # `select_related("agent")`, so `conversation.workstream` is a real
    # query -- and `attach_workstream` is `None` for anybody who may not
    # upload, so on that branch the row is never returned and must never
    # be fetched. `workstream_id` is a local column on the row already in
    # hand, and it is all `composer_attach_context` needs to answer both
    # keys; the ROW goes in only on the branch that returns it. Passing
    # `workstream=None` WITHOUT the id would be a behaviour change, not
    # an optimisation -- it reads as "loose conversation" and flips
    # `may_attach_files` back to True for a viewer this door must refuse.
    #
    # For a LOOSE conversation both are `None` and `may_attach_files`
    # falls back to naming ONLY the tool-access predicate, unchanged.
    may_upload_here = bool(stream_scope is not None and stream_scope.may_upload)
    attach_context = composer_attach_context(
        principal,
        workstream=conversation.workstream if may_upload_here else None,
        workstream_id=conversation.workstream_id,
        tool_access=rag_tool_access, may_upload=may_upload_here,
    )
    # E4: read once, pass to both consumers below (`preflight_turn` and
    # the picker) -- this function's own `conversation` is the only one
    # either needs, and reading it twice for the SAME conversation on the
    # SAME render was one indexed read this page never had to pay twice.
    #
    # CONSOLIDATION WAVE (audit A, F10): `wall_for`'s OWN docstring
    # already records that `agents.runtime.loop` was taught to read
    # `WorkstreamScope.wall` off a scope it already holds rather than
    # call `wall_for` fresh, and lists THIS function among the callers
    # that have "no `WorkstreamScope` of its own in hand" -- which
    # stopped being true the moment `stream_scope`, above, started
    # carrying `.wall` for this exact conversation. `wall_for(
    # conversation)` re-ran `wall_ids(workstream_id)` for a value
    # already in hand whenever `stream_scope` resolved. The `else` leg
    # is NOT cosmetic: `workstream_scope` answers `None` for a principal
    # who may READ a shared conversation but is not admitted to its
    # stream (spec §17.3's own anomaly, the identical one `agents.
    # runtime.loop`'s own docstring describes), and the wall must still
    # bind for that principal even though no scope is in hand to read it
    # off -- `wall_for` stays the fallback for exactly that case.
    wall = stream_scope.wall if stream_scope is not None else wall_for(conversation)
    check = preflight_turn(conversation.agent, selected, actor=principal,
                           conversation=conversation, wall=wall,
                           tool_access=rag_tool_access)
    # THE PAGE PAYS NOTHING FOR THE CEILING. `check.resolved` is the
    # `ResolvedModel` for this turn's bound model -- the PICKED
    # connection when the operator used the picker, this agent's role
    # binding otherwise -- and the preflight above already holds it.
    #
    # THE BRANCH IS `check.resolved is None`, NEVER `check.ok` (spec
    # review m1). `preflight_turn`'s NO_TOOL_CALLING leg returns a
    # refusal carrying a real `ResolvedModel`: a turn that cannot run
    # because the bound model will not call tools still has a known
    # ceiling, and the meter shows it. Only a genuinely absent binding
    # -- an unbound role, an unregistered pick, a label refusal --
    # renders the ceiling-free line, beside the `unavailable` banner
    # this page already carries.
    if check.resolved is None:
        window, window_source = 0, WINDOW_SOURCE_UNBOUND
    else:
        window, window_source = effective_context_window(check.resolved)
    context_meter_usage = context_usage(conversation, conversation.agent,
                                        window=window, window_source=window_source)
    # RENDER-VS-GATE: this sentence names OPERATOR CONFIGURATION, so a
    # non-admin's render never BUILDS it -- it is not a template `{% if
    # %}` over a string that was computed anyway. `settings_row` is the
    # one already fetched for this render.
    engine_default_sentence = (
        ENGINE_DEFAULT_SENTENCE
        if context_meter_usage.window_source == WINDOW_SOURCE_ENGINE_DEFAULT
        and is_admin(principal, settings_row=settings_row)
        else ""
    )
    # `?pending=` is a raw query-string read, never validated by the URL
    # resolver the way a path segment is -- a caller can send anything
    # (`?pending=abc`, `?pending=-1`). Accepted only when it is a bare
    # digit string (`str.isdecimal`, which "-1" fails on its leading
    # "-"), and `reverse()` is called HERE, in Python, wrapped in its
    # own `try/except`, rather than left to the template's `{% url %}`
    # tag -- a `NoReverseMatch` inside a template render is an
    # uncaught exception the response layer cannot turn into a 200,
    # i.e. the 500 this fix removes.
    raw_pending = request.GET.get("pending", "")
    pending_turn_id = raw_pending if raw_pending.isdecimal() else ""
    pending_status_url = None
    if pending_turn_id:
        try:
            pending_status_url = reverse("chat-turn-status", args=[pending_turn_id])
        except NoReverseMatch:
            pending_status_url = None
    # ROUND 13 (message-bound attachments): computed ONCE, here, ahead
    # of the dict literal below -- `thread_cards` needs it grouped BY
    # TURN so each user turn's own card renders its own chips (`agents.
    # chat.rendering.turn_card`'s own keyword). CONSOLIDATION WAVE
    # (audit A F3 / audit B S9): the grouping itself -- resolve the
    # stream scope, call `attached_documents`, build a `defaultdict`
    # keyed on `row.get("turn_id")` -- used to be written out here AND
    # in `turns.py` AND in `all_conversations.py`; all three now call
    # `agents.attachments.attachments_for`, the one place that grouping
    # lives. `turns.py`'s own freshness requirement (a fresh query on
    # every poll tick) is unaffected -- it just calls the shared function
    # fresh, the same as it always called this same logic fresh.
    attachments_by_turn = attachments_for(principal, conversation, stream=stream_scope,
                                          settings_row=settings_row)
    return {
        "conversation": conversation,
        "agent": conversation.agent,
        "cards": thread_cards(conversation, attachments_by_turn=attachments_by_turn),
        "picker": chat_picker_options(principal, selected, wall=wall),
        "selected_connection": selected,
        # ROUND 18: `chat/_composer.html`'s own `composer_placeholder`
        # parameter -- built here, not typed as a template literal, so
        # a lost/renamed agent still interpolates correctly.
        "composer_placeholder": f"Message {conversation.agent.name}…",
        # Declared ONCE, in `agents/chat/service.py`, and handed to the
        # template from here (M1: `thread.py` never imports `turns.py`).
        "poll_interval_ms": POLL_INTERVAL_MS,
        "max_transport_retries": MAX_TRANSPORT_RETRIES,
        "max_poll_duration_ms": MAX_POLL_DURATION_MS,
        # `?pending=<turn_id>` is the no-JS path's answer to "where did
        # my message go": the redirect carries it, the page renders that
        # turn as pending, and the poller picks it up from
        # here on a JS-enabled load. One key serves both paths.
        "pending_turn_id": pending_turn_id,
        # Built above, in Python -- never `{% url %}` in the template,
        # which has no way to catch a `NoReverseMatch` and would 500 on
        # a malformed `?pending=`. `None` (not `""`) when there is
        # nothing to poll, which the template's own `{% if %}` treats
        # the same way.
        "pending_status_url": pending_status_url,
        "setup_url": reverse("inference-console"),
        # `check.message` when the chat role is unbound, the picked
        # connection no longer resolves, or the bound model cannot call
        # this agent's granted tools -- names the ROLE, never a model
        # (`preflight_turn`'s own contract). `""` when the turn could be
        # queued right now, which `_unavailable.html`'s `{% if %}`
        # treats as "nothing to show".
        "unavailable": check.message if not check.ok else "",
        # FEATURE A. The line is server-rendered in full; the poller
        # rewrites two number spans and unhides one pre-rendered clause,
        # and composes no prose at all (spec decisions 21-22).
        "context_usage": context_meter_usage,
        "context_meter": meter_segments(context_meter_usage),
        "context_truncation_clause": truncation_clause(context_meter_usage),
        "context_full_clause": (FULL_CLAUSE if context_meter_usage.band == BAND_FULL
                                else ""),
        "context_engine_default_sentence": engine_default_sentence,
        "context_disclosure_summary": DISCLOSURE_SUMMARY,
        "context_disclosure_body": DISCLOSURE_BODY,
        # A granted tool that is not registered on this install is a
        # NOTE, not a refusal (spec section 8.3 step 3's tolerant
        # half) -- `dropped_tool_notes` (`agents/runtime/preflight.py`)
        # is the SAME function `agents.chat.service.start_turn` calls
        # for a 202's JSON body, over the SAME `check.dropped_tools`, so
        # a POST's note and this GET's note can never disagree.
        "notes": dropped_tool_notes(check),
        # RENDER-VS-GATE, on the surface this task introduces it. `chat-turn`
        # answers 403 to a `view`-share recipient (Task 7); a page that still
        # rendered them the compose form would be the exact defect Task 17
        # exists to remove, shipped fresh.
        "may_see_shares": may_see_shares,
        # CONSOLIDATION WAVE (audit B, S18): `conversation_shares`, not
        # the bare `shares` this used to be -- `agents.chat.views.
        # workstreams`'s own `share_list_for(...)` result (a DIFFERENT
        # row shape, a stream's shares) used to sit under the SAME bare
        # key.
        "conversation_shares": (shares_for(Share.Target.CONVERSATION, conversation.pk)
                                if may_see_shares else ()),
        "share_subjects": share_subjects(principal) if may_see_shares else {},
        "may_post": may_post_to(principal, conversation),
        # ROUND 9's attach door (`chat/_attach_files.html`, rendered by
        # `chat/_composer.html` now -- round 18's own ONE shared
        # composer, all three surfaces alike): gating computed once,
        # above. `attach_workstream`,
        # `None` for a loose conversation, is read only by that
        # fragment's own scope section, to decide the three-option
        # (in a stream) vs two-option (loose) chooser. `attach_
        # placement_state` is GONE as of round 12 (owner ruling): the
        # chat door never auto-applies a remembered
        # `default_upload_placement` any more -- it always renders its
        # own chooser, with "This chat only" pre-selected, regardless of
        # what the stream's own default says (the workstream Documents
        # panel, a different template, keeps its own auto-apply
        # behaviour and computes its own placement state separately).
        **attach_context,
        # ROUND 11 (owner feedback: an attached PDF ingested fine but
        # never showed up anywhere on this page); ROUND 13 retired the
        # conversation-level STRIP this key used to feed in favour of
        # per-turn chips (`"cards"`, above, via `attachments_by_turn`).
        # Two later removals took the rest of this spot's own history
        # with them: ROUND-13 REVIEW FIX, MINOR 3 dropped the flat,
        # unfiltered `"attachments"` context key (no template ever read
        # it), and the CONSOLIDATION WAVE's own S10 ruling later removed
        # I-5's `"legacy_attachments"` key along with the renderer
        # (`chat/_legacy_attachments.html`) it fed, once no migration on
        # `origin/main` could produce the `NULL turn_id` row it existed
        # to render (see `agents/attachments.py::attachments_for`'s own
        # docstring for the full ruling). Nothing that spot's own local
        # variable used to be survives either: `attachments_by_turn`,
        # above, is the one grouped value this dict carries, read once
        # by `thread_cards` to build `"cards"`.
        # UI-3b: the conversation sidebar, the SAME builder the index
        # uses (`agents/chat/sidebar.py`) -- one shape, two pages, so
        # the list cannot render one way here and another way there.
        #
        # AN ARCHIVED THREAD GETS THE ARCHIVE'S SIDEBAR. Reading one is
        # allowed and its URL still works; showing it the ACTIVE list
        # would mean the page you are on is the one entry the sidebar
        # cannot mark. `current=conversation` is what marks it.
        # The gate middleware's own already-stashed row -- see
        # `sidebar_context`'s note on why the per-row manage check must
        # not re-read the identity singleton.
        **sidebar_context(principal, current=conversation,
                          archived=conversation.archived_at is not None,
                          settings_row=settings_row),
    }


class ConversationView(TemplateView):
    """GET /chat/c/<uuid>/ -- the thread.

    Looked up through `agents.visibility.visible_conversations`, never
    `Conversation.objects` directly (ruling 4c) -- `foundation/ops/
    tests/test_column_boundaries.py::
    test_no_chat_module_queries_the_three_owned_models_directly` fails
    the build otherwise. In open mode that function returns every
    conversation, so an unknown id is the only way to a 404 today.
    """

    template_name = "chat/conversation.html"

    def get_context_data(self, **kwargs) -> dict:
        principal = principal_for_request(self.request)
        conversation = visible_conversation_or_404(principal, kwargs["conversation_id"])
        context = super().get_context_data(**kwargs)
        context.update(thread_context(self.request, conversation))
        return context
