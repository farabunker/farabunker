"""Rows -> render-ready dicts. Every judgement the thread makes about
what a turn MEANS lives here, and the templates iterate.

No request, no `HttpResponse`, no template: this module is pure enough
to test over rows alone, and `agents.chat.views.turns.turn_status`
reuses it byte-identically to render one finished card into its poll
body. A template that decided any of this itself would be a second
copy that drifts the first time one of them is edited.

IT IMPORTS NOTHING FROM `tools.*`. Import-law rule 3 forbids it outright
(`foundation/ops/tests/test_import_law.py::test_no_agents_module_
imports_a_tools_package` walks every import node in every file under
`agents/`), and nothing here needs to: an artifact is a REFERENCE
STRING, `agents.contracts.artifacts.artifact_url_name` maps its kind to
a URL NAME, and Django reverses the name. No layer below the view ever
learns a filesystem path -- the same rule `tools/vision/services.py`'s
`job_json` already enforces for the vision page.

FOUR TRUTHS ABOUT AN AUDIT ROW, three of them from P2's ledger:

1. `finished_at IS NULL` means RUNNING, whatever `outcome` says --
   `invoke_tool` creates the row with `outcome=ERROR` as a placeholder.
   Read through `agents.runtime.audit.invocation_state`, never directly.
2. A failure's words are in `error`, not `text` (`invoke.py::_finish`
   writes `text` only when a `ToolResult` came back).
3. `reverse()` CAN RAISE HERE. `vision-output-file` exists only when
   the vision feature is on (`config/urls.py:24-25`), and a
   conversation outlives a feature flag. One `try/except
   NoReverseMatch`, in `_url_for`, and the artifact then renders as
   plain text. UI-3c adds the SECOND site on the same rule --
   `_generation_url`'s `vision-gallery` -- and it is where the image
   card's "open this submission" button gets its feature guard: an
   unguarded `{% url %}` in the card template would 500 a whole
   historical thread on a box whose flag was later turned off, and
   catching it here (rather than passing a boolean down from the view)
   is what makes the guard hold for `turn_status`'s poll body too,
   which renders the same card with no page context at all.
4. Citations live under `data["citations"]` (`rag.ask`) OR
   `data["results"]` (`rag.search`) -- deviation P3-D6. Both lists
   carry `title`, `locator_text`, `score`, `document_id`.
"""
from __future__ import annotations

import json
import logging
import re
import uuid

from django.urls import NoReverseMatch, reverse
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

from agents.contracts.artifacts import artifact_title, artifact_url_name, parse_artifact
from agents.contracts.tools import VISION_GENERATE_KEY
from agents.limits import TURN_TIMEOUT_ERROR
from agents.models import Turn
from agents.runtime.audit import invocation_message, invocation_state
# THE PER-TURN EDITABILITY RULE, ONE DEFINITION (feature C review, I1).
# `is_editable_turn_row` needs no principal and runs no query -- it is
# three facts off a row this module already holds -- so the card asks the
# SAME function `agents.visibility.may_edit_turn` asks rather than
# spelling the condition a second time in its own words. Importing
# `agents.visibility` from here is the sanctioned direction:
# `agents/chat/views/turns.py` and `views/thread.py` already do it, the
# import law's rule is about the reverse, and `agents/visibility.py`
# imports nothing from `agents.chat` at all (its own `ast`-walking test
# pins that).
from agents.visibility import is_editable_turn_row

logger = logging.getLogger(__name__)

# How much of one tool argument the card shows. A prompt or a query can
# be paragraphs long, and a card that rendered all of it would bury the
# answer it sits above. The full value is always in the row.
ARG_VALUE_MAX = 200

# U1 (chat-polish P3.1): a tool's raw text body -- the whole
# concatenated library-search result text, say -- collapses behind a
# `<details>` past this length. Citations, images and files (the
# structured, USEFUL half of a tool card) stay visible above it either
# way; only the raw dump collapses.
TOOL_OUTPUT_COLLAPSE_THRESHOLD = 400

_IMAGE_KINDS = ("output", "input")

# The image surface's own generate-tool key, matched BY VALUE. ALIASED
# FROM `agents.contracts.tools` (packet finding B, 2026-09-17), the
# same one home `agents.runtime.prompt`/`agents.runtime.loop` alias
# from -- this used to be a THIRD literal spelling of `"vision.generate"`
# inside the agents column. This module still may not import `tools.*`
# at all (import-law rule 3, stated in this file's own docstring and
# enforced by `foundation/ops/tests/test_import_law.py`), so the
# constant that declares it (`tools.vision.tools.VISION_GENERATE_TOOL_
# KEY`) is unreachable from here -- exactly the reason `artifact_url_
# name` maps an artifact KIND to a URL NAME by string rather than by
# import -- but `agents.contracts.tools` is a same-column, rule-1 pure
# leaf, not `tools/`, so aliasing it costs nothing and removes a
# duplicate. A key this string does not match simply gets no link,
# which is the honest failure: the card renders as it always did.
_VISION_GENERATE_KEY = VISION_GENERATE_KEY

# Values `_split_args` treats as "nothing was really supplied" -- U1's
# "20 argument rows, most null" complaint. `False`/`0` are real,
# meaningful values and stay inline; only these four are the union
# spec's own "not used by this operation" filler.
_EMPTY_ARG_VALUES = (None, "", [], {})


def thread_cards(conversation, *, queue_job_id: int | None = None,
                 attachments_by_turn: dict | None = None,
                 may_edit: bool = False, may_attach_files: bool = False,
                 attach_workstream=None) -> list[dict]:
    """Every turn of `conversation`, in index order, as cards --
    `depth > 0` turns nested under the turn they belong to.

    THE NESTING RULE COMES FROM THE WRITE ORDER, not from a guess.
    `agents.runtime.loop.run_loop` calls `invoke_tool` FIRST (line
    298) -- which, for an `agent.<slug>` spec, runs the delegate's
    entire loop and writes its depth-1 turns -- and only THEN creates
    the parent's own TOOL row (`Turn.objects.create(...)` at line 322).
    Deep turns therefore always PRECEDE the turn they belong to, so
    this buffers them and attaches the buffer to the next depth-0 turn
    rather than looking ahead.

    A buffer left over at the end (a delegate that crashed before its
    parent's row existed) is rendered at top level rather than dropped:
    hiding work that really happened would be the worse failure.

    `queue_job_id` NARROWS IT TO ONE JOB'S OUTPUT, which is what the
    poll view needs: a finished turn is not one card, it is the TOOL
    cards the loop wrote plus the assistant answer, and a poller that
    swapped in only the answer would leave the tool calls invisible
    until a manual refresh. Both `run_loop` and `_run_turn` stamp
    `queue_job_id` on every row they write (`agents/runtime/loop.py`),
    and the USER turn -- written by the view, before any job exists --
    carries none in the DATABASE, so this SQL filter selects exactly
    the job's own rows and never the message that provoked it. Nesting
    still works: the delegate's depth-1 turns carry the same job id.

    THE UNFILTERED CALL (`queue_job_id=None`, `thread_context`'s own
    whole-page render) STILL STAMPS A USER CARD'S `poll_block` KEY
    (chat-polish P3.1 fix round 1, R1) -- a rendering-only decision,
    never a database write. `turn_group_cards` (below) builds the
    POLLED fragment for one job and always groups the provoking USER
    card into it under that job's `poll_block` marker
    (`_turn_card.html`'s `data-poll-block`); before this fix, the FULL
    page render left that same USER card with no marker at all, so a
    poller's swap -- which removes every DOM node sharing the marker
    before inserting a fresh copy -- could never find the
    server-rendered USER card and left a second, orphaned copy of it
    behind once the fresh group landed (a real, reported duplicate: a
    mid-turn reload, or the no-JS `?pending=` redirect, followed by a
    poll tick). Fixed by construction here rather than patched on the
    client: as this loop walks turns in index order, the most recent
    unclaimed USER card is remembered, and the FIRST depth-0 row after
    it that carries a `queue_job_id` (a TOOL or the ASSISTANT turn
    itself) claims it -- stamping that USER card's `poll_block` to the
    SAME value, exactly what `turn_group_cards` already does for its
    own copy. The full render and the polled fragment now agree on
    every marker for an in-flight (or just-finished) exchange, which is
    what makes the poller's existing dedup-by-marker swap correct
    again: `test_thread.py::TestThePoller::
    test_the_full_render_and_the_polled_fragment_share_one_poll_block_
    marker` pins the parity directly.

    `may_edit` / `may_attach_files` / `attach_workstream`, OPTIONAL
    (chat cluster, feature C): the three keys the per-turn edit
    disclosure needs, passed straight to every card this builds. Each is
    a CONVERSATION-level answer the caller computed ONCE -- see
    `turn_card`'s own note on why -- so they are threaded rather than
    derived here: this module holds no principal and cannot ask.
    """
    turns = conversation.turns.select_related("invocation").all()
    if queue_job_id is not None:
        turns = turns.filter(queue_job_id=queue_job_id)
    by_turn = attachments_by_turn or {}
    edit_keys = {"may_edit": may_edit, "may_attach_files": may_attach_files,
                 "attach_workstream": attach_workstream}
    buffered: list[dict] = []
    cards: list[dict] = []
    pending_user_card: dict | None = None
    for turn in turns:
        if turn.depth:
            buffered.append(turn_card(turn, attachments=by_turn.get(turn.pk),
                                      **edit_keys))
            continue
        card = turn_card(turn, nested=buffered, attachments=by_turn.get(turn.pk),
                         **edit_keys)
        buffered = []
        if turn.role == Turn.Role.USER:
            pending_user_card = card
        elif turn.queue_job_id is not None and pending_user_card is not None:
            pending_user_card["poll_block"] = turn.queue_job_id
            pending_user_card = None
        cards.append(card)
    cards.extend(buffered)
    return cards


def turn_card(turn, nested: list[dict] | None = None, attachments: list[dict] | None = None,
              attachment_detach_next: str | None = None,
              may_edit: bool = False, may_attach_files: bool = False,
              attach_workstream=None) -> dict:
    """One turn as a fixed-key dict.

    FIXED KEYS, always present. A card that omitted a key on some paths
    would make a template `{% if %}` silently never fire, which is the
    class of bug a renderer is least likely to notice.

    `attachments`, OPTIONAL (round 13, message-bound attachments; owner
    feedback: "After submitting the document wasn't attached to that
    message (at least not visually)"): the chip dicts (`tools.rag.
    access.attached_documents`'s own shape, threaded through unchanged)
    whose OWN `"turn_id"` key equals THIS turn's pk -- resolved by the
    CALLER (`thread_cards`/`turn_group_cards`, below, both of which
    accept an `attachments_by_turn` mapping built once per render) and
    passed straight through, never queried here: this module imports
    NOTHING from `tools.*` (its own docstring's rule 3), so it cannot
    resolve `agents.attachments.attached_documents` itself. `[]` --
    never `None` -- when the caller has nothing for this turn (a TOOL/
    ASSISTANT card, or a USER turn that carried no files), the same
    "fixed key, always a list" contract `nested`/`images`/`files`
    already keep.

    `attachment_detach_next`, OPTIONAL (ROUND-13 REVIEW FIX, MINOR 2):
    the `next` value `chat/_attachment_chip.html`'s own detach form
    carries, when set -- `None` (every caller before this fix, and the
    ordinary thread page's own call) renders no hidden field at all, so
    `agents.chat.views.turns.attachment_detach` falls back to its
    existing default (`conversation_url(conversation)`, "stay on the
    conversation"). `agents.chat.views.all_conversations._preview_cards`
    is the one caller that sets it, to its own current URL (search/
    filter/page/selected state included), so detaching from `/chat/
    all/`'s own preview pane returns the operator to that browse state
    instead of bouncing them into the thread.

    `may_edit` / `may_attach_files` / `attach_workstream`, OPTIONAL
    (CHAT CLUSTER, FEATURE C -- editing a past prompt): the three
    conversation-level answers the per-turn edit disclosure needs. Their
    own note sits beside the keys they set, below.
    """
    images, files = artifact_links(turn.artifacts or ())
    return {
        "turn": turn,
        "index": turn.index,
        "role": turn.role,
        "state": turn.state,
        "depth": turn.depth,
        "text": turn.text,
        # U2: a safe-subset markdown rendering of `turn.text`, ASSISTANT
        # turns only -- a model's own words are the one text this page
        # shows that was never typed by the operator, so it is the one
        # place `**bold**`/backtick/list markers are worth rendering
        # rather than showing literally. `None` for every other role;
        # `_turn_card.html` falls back to `text|linebreaks` when this is
        # falsy, which a USER turn's plain typed message always is.
        "text_html": render_answer(turn.text) if turn.role == Turn.Role.ASSISTANT else None,
        "error": turn.error,
        # THE CONTENT-WITHHELD PATTERN, applied to the on-timeout admin
        # hint (one-timeout task, 2026-09-17): computed HERE, once, in
        # Python, against the ONE shared constant `agents.limits`
        # declares (`TURN_TIMEOUT_ERROR`, imported into `agents.runtime.
        # loop` too, so both sides of the column read the same object)
        # -- never a template string literal comparison, which would be
        # a second, driftable copy of the same sentence. `_turn_card.
        # html`'s FAILED branch shows the
        # admin-only "raise this in Settings" hint only when this is
        # `True` AND the viewer is an admin; a non-admin, or a FAILED
        # turn with any OTHER error, sees no hint. `False` for every
        # non-FAILED turn too (`turn.error` is `""` for those), same as
        # every other fixed key on this card.
        "error_is_timeout": turn.error == TURN_TIMEOUT_ERROR,
        # A turn the page should keep watching. Only an ASSISTANT turn
        # is ever non-`done` (`agents/models.py::Turn`'s own docstring),
        # so this is exactly the placeholder the poller is waiting on.
        "pending": turn.state in (Turn.State.QUEUED, Turn.State.RUNNING),
        "tool": tool_card(turn) if turn.role == Turn.Role.TOOL else None,
        "nested": nested or [],
        "images": images,
        "files": files,
        "attachments": attachments or [],
        "attachment_detach_next": attachment_detach_next,
        # D1 (chat-polish P3.1): the marker `_turn_card.html` renders as
        # `data-poll-block`, read from THIS key rather than
        # `card.turn.queue_job_id` directly -- `turn_group_cards` (below)
        # overrides it on a USER card it is grouping alongside a job's
        # own cards, even though that row's own `queue_job_id` column is
        # always `NULL` (the USER turn is written before any job
        # exists). Every other caller of `turn_card` leaves this equal
        # to the row's own `queue_job_id`, unchanged from before.
        "poll_block": turn.queue_job_id,
        # FEATURE C. `may_edit` is a CONVERSATION-level answer the
        # caller computed once (`agents.visibility.may_edit_any_turn` --
        # `may_edit_turn`'s own predicate minus the per-row half, which
        # the card already carries in `role`/`depth`/`state`), so a
        # thread of two hundred messages costs ONE predicate rather than
        # two hundred. `may_attach_files` and `attach_workstream` are
        # `agents.chat.service.composer_attach_context`'s two answers,
        # threaded so the edit form's file input needs no second
        # derivation -- and BOTH are needed, because `chat/_attach_
        # files.html` reads `attach_workstream` to decide whether to
        # offer the three-value placement chooser or the two-value one.
        # Passing only the boolean would render a form on the POLL path
        # that differs from the reload's, which is the very thing this
        # threading exists to prevent.
        #
        # THEY DEFAULT FALSE/None ONLY FOR CALLERS THAT HAVE NO
        # PRINCIPAL. `agents.chat.views.turns._done_body` DOES compute
        # them and pass them through `turn_group_cards`, so a polled
        # swap shows exactly what a reload shows -- the invariant
        # `_done_body`, `_group_html`, `turn_group_cards` and
        # `_attachments_by_turn` each state in their own words.
        #
        # THE ROW HALF IS `is_editable_turn_row`, NOT A SECOND SPELLING
        # OF IT (feature C review, I1): this line used to restate
        # `may_edit_turn`'s own `role`/`depth`/`state` condition in the
        # positive, with nothing pinning that the two agreed. A card
        # whose copy drifted WIDER would render a disclosure whose POST
        # answers 404; one that drifted NARROWER would silently hide an
        # available control. One definition, asked from both sides.
        "may_edit": bool(may_edit and is_editable_turn_row(turn)),
        "may_attach_files": bool(may_attach_files),
        "attach_workstream": attach_workstream,
        # THE PER-TURN DOM ID the edit form's file input and its label
        # button share. Built HERE rather than composed in the template,
        # so there is one spelling of it and a test can assert on the
        # same string the markup uses. `chat/_attach_files.html`
        # hardcodes `id="attach-files"` and the COMPOSER already renders
        # one; every `<label for=...>` resolves to the FIRST match in
        # document order, so without this each edit form's "+ Add files"
        # would open the COMPOSER's picker and stage the file onto a new
        # turn instead of onto the branch.
        "attach_id": f"attach-files-{turn.pk}",
        # THE EDIT TEXTAREA'S OWN DOM ID, on the same per-turn rule and
        # for the same reason (feature C review, M1): the disclosure
        # renders once per eligible user turn, so a long thread is a run
        # of edit boxes, and a fixed id would give every `<label for=>`
        # on the page the same target -- the first one in document
        # order. A REAL `<label>`, not an `aria-label`: `chat/
        # _composer.html`'s own recorded decision reaches for the
        # attribute only because the shared fragment had dropped a
        # visible label that used to exist, and nothing here forces that
        # compromise.
        "edit_text_id": f"edit-text-{turn.pk}",
    }


def turn_group_cards(turn, attachments_by_turn: dict | None = None, *,
                     may_edit: bool = False, may_attach_files: bool = False,
                     attach_workstream=None) -> list[dict]:
    """The whole visible exchange one ASSISTANT turn belongs to: the
    USER message that provoked it, plus every card its own job wrote --
    exactly what a full page reload already shows for this turn (D1,
    chat-polish P3.1).

    `attachments_by_turn`, OPTIONAL (round 13): the SAME mapping
    `thread_cards` below accepts, `{turn_pk: [chip dict, ...]}` --
    threaded through to BOTH the user card this function builds
    directly and the `thread_cards(...)` call at the bottom, so the
    poller's own swapped fragment renders chips identically to a full
    page reload. `None` (`agents.chat.views.turns._group_html`'s
    caller has nothing to hand in -- see that module's own docstring
    for when that happens) means every card renders `[]`, the same as
    before this round.

    THE BUG THIS FIXES: `thread_cards(conversation, queue_job_id=...)`
    (used directly by `_done_body` before this) deliberately excludes
    the USER turn -- see that function's own docstring -- because a
    provoking message must never be duplicated into ANOTHER assistant
    turn's own job block on a full-thread render. That guarantee is
    still exactly what `thread_cards` provides and still what nesting
    depends on. But the POLLER (`turn_status`, and now `turn_create`'s
    202 body) never rendered the user's own bubble AT ALL when it
    inserted or swapped this turn's card -- the running/queued/done
    block it swaps in has always been ONLY the job's own output, and a
    JS-enabled submission never triggers the full-thread render that
    would have shown the user's message any other way. This function is
    the ONE place that adds the user turn back in, for exactly the one
    caller (the poller) that needs the whole exchange rather than just
    the job's own rows.

    A turn with NO `queue_job_id` renders itself alone, matching
    `thread_cards`'s own N4 guard (`turn_status.py`'s former
    `_done_body`): grouping it with a nearby USER turn found by index
    alone would not be grounded in anything the row itself records.

    `may_edit` / `may_attach_files` / `attach_workstream`, OPTIONAL
    (chat cluster, feature C): the same three keys `thread_cards` takes,
    threaded so a POLLED swap renders the edit disclosure identically to
    a full page reload. `agents.chat.views.turns._done_body` is the one
    caller that supplies them -- the queued and running bodies leave
    them at their defaults, and are RIGHT to: `may_edit_any_turn` is
    provably False while a turn of this conversation is in flight, which
    on those two paths is the turn being polled. This is the same "never
    render the group differently from a reload" rule this function's own
    first paragraph states.
    """
    by_turn = attachments_by_turn or {}
    edit_keys = {"may_edit": may_edit, "may_attach_files": may_attach_files,
                 "attach_workstream": attach_workstream}
    if turn.queue_job_id is None:
        return [turn_card(turn, attachments=by_turn.get(turn.pk), **edit_keys)]

    conversation = turn.conversation
    cards: list[dict] = []
    user_turn = (
        conversation.turns.filter(role=Turn.Role.USER, index__lt=turn.index)
        .order_by("-index").first()
    )
    if user_turn is not None:
        user_card = turn_card(user_turn, attachments=by_turn.get(user_turn.pk),
                              **edit_keys)
        # Grouped under the SAME marker as this job's own cards (not the
        # user row's real `queue_job_id`, which is always `NULL`) so the
        # poller's idempotent swap removes the STALE copy of the user's
        # own bubble along with the stale tool/assistant cards before
        # inserting the fresh group -- never leaving two copies of it
        # behind after a second poll tick re-swaps the same block.
        user_card["poll_block"] = turn.queue_job_id
        cards.append(user_card)
    cards.extend(thread_cards(conversation, queue_job_id=turn.queue_job_id,
                              attachments_by_turn=by_turn, **edit_keys))
    return cards


def tool_card(turn) -> dict:
    """One TOOL turn as a card: what ran, with what, how it ended, and
    what it produced."""
    call = turn.tool_call or {}
    key = call.get("tool") or ""
    images, files = artifact_links(turn.artifacts or ())
    message = invocation_message(turn.invocation, turn.text)
    shown_args, hidden_args = _split_args(call.get("args") or {})
    return {
        "key": key,
        "label": _tool_label(key),
        "agent": call.get("agent") or "",
        # NEVER `turn.invocation.outcome` directly -- see truth 1.
        "state": invocation_state(turn.invocation),
        "message": message,
        # U1: a card that rendered the whole concatenated result text --
        # thousands of characters, `<EOS> <pad>` tokens and all -- buried
        # the citations and thumbnails that actually answer "what did
        # this call find". The template puts `message` behind a `<details>`
        # ("Show output") exactly when this is true, never deciding the
        # threshold itself.
        "message_collapsed": len(message) > TOOL_OUTPUT_COLLAPSE_THRESHOLD,
        # Non-null/non-empty rows, shown inline -- what the caller
        # actually supplied. `hidden_args` (below) is everything else.
        "args": shown_args,
        # U1: a union `ToolSpec`'s call can carry twenty parameters,
        # most `None` (the operation actually picked does not use
        # them) -- these rows go behind the card's own "All arguments
        # (N)" disclosure instead of dominating the visible dl.
        "hidden_args": hidden_args,
        # Recorded, never silent: a model reasoning about three calls it
        # thinks it made is reasoning about a turn that did not happen
        # (`loop._tool_message_text` tells the model the same thing).
        "discarded": [d.get("tool") or "" for d in (call.get("discarded") or [])],
        "citations": citations_of(turn),
        "images": images,
        "files": files,
        # UI-3c: "take me to that submission", not just to the picture.
        # `""` for every tool that is not the image generator, for a
        # result that carries no job id, and on a box where the image
        # surface is not mounted -- the template renders no button for
        # any of the three, and the card is exactly what it was before.
        "generation_url": _generation_url(key, turn.data),
    }


def _split_args(args: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """`(shown, hidden)` -- every `(key, _short(value))` row, split on
    whether the RAW value is one of `_EMPTY_ARG_VALUES`. Splitting here,
    over the raw value, rather than in the template over `_short`'s
    string output: `_short(None)` is the string `"null"`, and a template
    check for that literal string would also hide a caller-supplied
    string argument that happened to BE the word "null"."""
    shown: list[tuple[str, str]] = []
    hidden: list[tuple[str, str]] = []
    for key, value in sorted(args.items()):
        entry = (key, _short(value))
        (hidden if value in _EMPTY_ARG_VALUES else shown).append(entry)
    return shown, hidden


def citations_of(turn) -> list[dict]:
    """The library sources a tool turn recorded.

    `data["citations"]` (`tools/rag/tools.py::run_ask`) OR
    `data["results"]` (`::run_search`) -- deviation P3-D6. Both carry
    `title`, `locator_text`, `score`, `document_id`
    (`tools/rag/retrieval.py::_vector_citations` and
    `::_search_result_for`), which are exactly the four fields spec
    section 8.4 names.
    """
    data = turn.data if isinstance(turn.data, dict) else {}
    entries = data.get("citations") or data.get("results") or []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        out.append({
            "title": entry.get("title") or "(untitled)",
            "locator_text": entry.get("locator_text") or "",
            "score_display": _score_display(entry),
            "url": _document_url(entry.get("document_id")),
        })
    return out


def artifact_links(references) -> tuple[list[dict], list[dict]]:
    """`(images, files)` for a turn's artifact reference strings.

    An unparseable reference is DROPPED. `parse_artifact` raises for
    anything that is not `<kind>:<digits>`; this value reached the row
    from a tool runner, and a link that cannot resolve is worse than no
    link at all.
    """
    images: list[dict] = []
    files: list[dict] = []
    for reference in references:
        try:
            kind, pk = parse_artifact(reference)
        except ValueError:
            logger.info("chat: dropping unparseable artifact reference %r", reference)
            continue
        entry = {
            "reference": reference, "kind": kind,
            "url": _url_for(artifact_url_name(kind), pk),
            # R5 (chat-polish P3.1, fix round 1): the document's own
            # TITLE, read straight off `reference` -- `mint_artifact`
            # (`tools/rag/tools.py::_document_artifacts`) embeds it at
            # the moment the reference is created, which is what lets
            # this module show a name instead of a bare id with NO
            # second, cross-column lookup (this file's own docstring
            # forbids importing anything from `tools.*` at all).
            # `artifact_title` returns `""` for a non-`document` kind
            # and for any reference minted before this addition, so the
            # fallback numbers the id -- never worse than the bare
            # `document:<id>` reference the link used to show verbatim.
            "title": (
                (artifact_title(reference) or f"Document {pk}")
                if kind == "document" else ""
            ),
        }
        (images if kind in _IMAGE_KINDS else files).append(entry)
    return images, files


def _url_for(url_name: str, pk: int) -> str:
    """The reversed URL, or `""` when that route is not mounted.

    THE ONE PLACE `NoReverseMatch` IS CAUGHT. `vision-output-file` and
    `vision-input-file` exist only when `"vision"` is in
    `FARABUNKER_FEATURES` (`config/urls.py:24-25`), and a conversation
    outlives a feature flag. `""` makes the template render the
    artifact as plain text -- honest about what it is and honest that
    there is nothing here to open.
    """
    try:
        return reverse(url_name, args=[pk])
    except NoReverseMatch:
        logger.info(
            "chat: %r is not mounted on this install; rendering the artifact "
            "as plain text.", url_name,
        )
        return ""


def _document_url(document_id) -> str:
    """`rag-document-file` for a numeric id, else `""`.

    Guarded with `isdecimal()` the same way `tools/rag/retrieval.py::
    _document_url_for` guards its own: a stray non-numeric `file_id` in
    stored chunk metadata must not mint a URL nothing can reverse.
    """
    if document_id is None or not str(document_id).isdecimal():
        return ""
    return _url_for("rag-document-file", int(document_id))


def _generation_url(key: str, data) -> str:
    """The image gallery, filtered and anchored to THIS generation --
    or `""` when there is nothing to link to.

    THE ID COMES FROM THE STRUCTURED RESULT, NEVER FROM THE PROSE.
    `tools/vision/tools.py`'s runner returns `ToolResult(text=...,
    data=services.job_json(job))`, and that payload's `"id"` is the
    generation's own uuid -- the same value its closing sentence prints.
    Reading it off `Turn.data` is reading the thing the sentence was
    formatted FROM; parsing the sentence back apart would make a copy
    edit over in another column silently break this link.

    NOT `data["queue_job_id"]`, which is in the same payload and is a
    different row in a different table. The gallery is addressed by
    generation uuid.

    VALIDATED AS A UUID before it is spliced into a URL, the same guard
    and the same reason `_document_url` puts `isdecimal()` in front of
    its own id: `Turn.data` is a JSON column written by a tool runner,
    and a stray value there must not mint a link nothing can resolve.
    A validated uuid also needs no percent-encoding, which is why there
    is none here.

    `NoReverseMatch` IS THE FEATURE-FLAG GUARD, and it is caught for the
    same reason truth 3 in this module's docstring gives for
    `_url_for`: `vision-gallery` is mounted only while `"vision"` is in
    `FARABUNKER_FEATURES` (`config/urls.py`), and a conversation
    outlives a feature flag -- an unguarded `{% url %}` in the card
    template would 500 the whole thread page on a box where the flag
    was later turned off. Guarding it HERE rather than with a boolean in
    the view is what makes it hold on both render paths: `turn_status`
    renders this same card standalone into its poll body, with no page
    context to have carried a flag through.

    It is deliberately NOT `surface_available.images`, which answers a
    different question -- whether an image MODEL is bound -- and would
    wrongly hide the link to a real, finished generation on a flag-on
    box whose binding was since removed.
    """
    if key != _VISION_GENERATE_KEY:
        return ""
    raw = data.get("id") if isinstance(data, dict) else None
    if raw is None:
        return ""
    try:
        generation = str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError, TypeError):
        # DEBUG, not INFO: this fires on every render of the offending
        # thread -- page load AND every poll tick while a turn runs --
        # so an INFO here turns one bad row into a repeating line in the
        # operator's log for as long as anyone leaves that tab open.
        logger.debug("chat: %r is not a generation uuid; rendering no link.", raw)
        return ""
    try:
        gallery = reverse("vision-gallery")
    except NoReverseMatch:
        logger.info(
            "chat: 'vision-gallery' is not mounted on this install; rendering "
            "the generation card with no link.",
        )
        return ""
    # The shape agreed with the image surface: the gallery's own `?job=`
    # filter, plus the per-figure anchor it renders. The filter is what
    # makes the link land on the submission even when it is far down the
    # list; the anchor is what scrolls to it once it is on the page.
    return f"{gallery}?job={generation}#job-{generation}"


def _score_display(entry: dict) -> str:
    """`run_search` already formats one (`score_display`); `run_ask`'s
    citations carry the raw `score` only. Formatted the same way either
    way so two cards in one thread never disagree about precision."""
    if entry.get("score_display"):
        return str(entry["score_display"])
    score = entry.get("score")
    return f"{score:.3f}" if isinstance(score, (int, float)) else ""


def _tool_label(key: str) -> str:
    """The registered spec's operator-facing label, or the key.

    `_TOOLS.get`, not `get_tool`, deliberately: `get_tool` RAISES for an
    absent key, and a conversation can outlive the tool it called (a
    feature turned off, a tool retired). The key is always readable and
    is never a worse label than a crash.
    """
    from agents.contracts.tools import _TOOLS

    spec = _TOOLS.get(key)
    return spec.label if spec is not None else key


def _short(value) -> str:
    """One argument value, truncated, JSON for anything not a string."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= ARG_VALUE_MAX else text[:ARG_VALUE_MAX].rstrip() + "…"


# --- U2: a safe, dependency-free markdown SUBSET for assistant text ---
#
# NOT a markdown library (the brief's own instruction) -- a fixed, small
# grammar: `**bold**`, `*em*`, backtick code spans, `> ` blockquotes,
# `- `/`* ` and `1. ` lists, and fenced ``` blocks. Every raw line is
# escaped BEFORE any of this runs (`_inline`'s first line), so a model
# answer containing literal `<script>` or any other tag is inert text on
# the page, never markup -- and no code path here ever un-escapes
# anything or emits a tag `escape()` didn't put there itself. NO LINKS:
# a bare URL or `[text](url)` is never turned into `<a>` -- the brief's
# own rule, and one this grammar simply never implements.
_CODE_SPAN_RE = re.compile(r"`([^`\n]+?)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_EM_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_UL_ITEM_RE = re.compile(r"^[-*] (.*)$")
_OL_ITEM_RE = re.compile(r"^\d+\. (.*)$")
_QUOTE_ITEM_RE = re.compile(r"^> ?(.*)$")
_FENCE_RE = re.compile(r"^```")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _inline(line: str) -> str:
    """One line's worth of inline formatting, over ALREADY-ESCAPED text.

    Escaping FIRST, formatting SECOND is what makes this safe with no
    allow-list of tags to maintain: `escape()` turns every `<`/`>`/`&`
    into an entity, and none of the three markdown patterns below use
    those characters, so nothing this function adds can ever be
    un-escaped HTML from the model's own words -- only the `<code>`/
    `<strong>`/`<em>` tags this function itself writes.
    """
    text = escape(line)
    text = _CODE_SPAN_RE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _EM_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    return text


def render_answer(text: str) -> SafeString:
    """`text` (an assistant turn's own words), as safe, minimal HTML.

    Escapes first, so no raw HTML from the model ever reaches the page
    (an XSS-shaped `<script>` stays inert, escaped text -- pinned by
    `test_rendering.py`'s own test). Recognises exactly: `**bold**`,
    `*em*`, backtick code spans, `> ` blockquotes, `- `/`* ` and `1. `
    lists, and fenced ``` code blocks. THREE OR MORE consecutive
    newlines collapse to one paragraph break, so a model's habit of
    padding an answer with blank lines does not turn into a wall of
    empty `<p>`s. Never creates a link from bare text -- no `<a>` is
    ever produced here, from a URL or otherwise.
    """
    if not text:
        return mark_safe("")

    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    normalised = _BLANK_RUN_RE.sub("\n\n", normalised)
    lines = normalised.split("\n")

    html: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            html.append("<p>" + "<br>".join(_inline(l) for l in paragraph) + "</p>")
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not _FENCE_RE.match(lines[i].strip()):
                code_lines.append(lines[i])
                i += 1
            i += 1  # the closing fence, or EOF -- either way, move past it
            html.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
            continue

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        if _UL_ITEM_RE.match(line):
            flush_paragraph()
            items = []
            while i < len(lines) and _UL_ITEM_RE.match(lines[i]):
                items.append(_UL_ITEM_RE.match(lines[i]).group(1))
                i += 1
            html.append("<ul>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ul>")
            continue

        if _OL_ITEM_RE.match(line):
            flush_paragraph()
            items = []
            while i < len(lines) and _OL_ITEM_RE.match(lines[i]):
                items.append(_OL_ITEM_RE.match(lines[i]).group(1))
                i += 1
            html.append("<ol>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ol>")
            continue

        if _QUOTE_ITEM_RE.match(line):
            flush_paragraph()
            quoted = []
            while i < len(lines) and _QUOTE_ITEM_RE.match(lines[i]):
                quoted.append(_QUOTE_ITEM_RE.match(lines[i]).group(1))
                i += 1
            html.append(
                "<blockquote>" + "<br>".join(_inline(l) for l in quoted) + "</blockquote>"
            )
            continue

        paragraph.append(line)
        i += 1

    flush_paragraph()
    return mark_safe("".join(html))
