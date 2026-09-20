"""The cross-column seam for workstreams.

MAY BE IMPORTED BY `tools/` AND `models/`. It is the THIRD `agents`
name outside `agents/contracts/` that may be, beside `agents.
entitlements` (already a named seam -- `tools/vision/views.py:824` says
so in those words, and `tools/rag/views.py` imports it too). The closed
three-name set is pinned by `foundation/ops/tests/test_import_law.py::
test_tools_reaches_agents_through_contracts_entitlements_and_workstreams
_and_nothing_else`.

IT IMPORTS NOTHING OF `tools/`. That is the whole point, and it has its
own narrow guard (`::test_agents_workstreams_imports_no_tools_package`)
on top of the broad sweep, because this is the module whose accidental
widening would be least visible in review. When the stream page needs to
show another column's rows, the answer is a `WorkstreamPanel`
registration, never an import.

IT IMPORTS `agents.visibility`, NEVER THE REVERSE. The direction is
one-way, so the two agents-side stream modules cannot cycle when spec
§12 puts `stream_access` here and `share_workstream` /
`workstream_taint_ids` there.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.module_loading import import_string

from agents.contracts.workstreams import WorkstreamScope, all_workstream_panels
from agents.models import WorkstreamScopeEntitlement
from agents.visibility import (
    may_manage_workstream, set_workstream_upload_default, visible_workstreams,
)
from identity import audit
from identity.access import accounts_on, held_entitlement_ids
from identity.contracts import actions
from identity.contracts.principals import SERVICE_PRINCIPAL

logger = logging.getLogger(__name__)


def wall_ids(workstream_id: int) -> frozenset[int]:
    """The stream's wall, or the empty set on an open box.

    PUBLIC, not `_wall_ids`: it is the wall's one canonical reader, called
    by both `workstream_scope` below and, through `agents.entitlements.
    wall_for`, the planner's entitlement intersection in
    `agents/entitlements.py::tool_access_for`/`model_access_for` (the
    `wall=` keyword both take). A name that only this file may call is
    private; a name built to be called from outside it is not.

    RULING A (spec §23.A): when `accounts_on()` is False this returns
    `frozenset()` WITHOUT READING `WorkstreamScopeEntitlement` at all.
    That is what preserves "an open box never runs a permission query"
    and what stops a wall written under `enterprise` from bricking the
    stream after a posture switch -- `ToolEntitlement`,
    `DocumentEntitlement` and the model-set rows all survive a switch to
    `open` with no posture branch in their readers, so "on an open box
    nothing is labelled" is false and a non-empty `required` against an
    empty `held` would drop every labelled tool and refuse every labelled
    model set with no page able to clear it.

    The rows SURVIVE, dormant, and bind again the moment the posture
    returns.
    """
    if not accounts_on():
        return frozenset()
    return frozenset(
        WorkstreamScopeEntitlement.objects.filter(workstream_id=workstream_id)
        .values_list("entitlement_id", flat=True))


def _scope_from_row(principal, row) -> WorkstreamScope:
    """The `WorkstreamScope` for an ALREADY-RESOLVED `Workstream` row --
    the construction `workstream_scope` and `pin_target` below both need,
    factored out so adding the second caller did not mean a second copy
    of this same five-line build. `pinned_file_ids` is left EMPTY here
    for the identical reason `workstream_scope`'s own docstring gives.
    """
    return WorkstreamScope(
        workstream_id=row.pk,
        wall=wall_ids(row.pk),
        default_upload_placement=row.default_upload_placement,
        may_upload=may_manage_workstream(principal, row),
        # ROUND 17: carried straight off the row -- see `WorkstreamScope.
        # include_universal`'s own docstring for the corpus-formula
        # consequence.
        include_universal=row.include_universal,
    )


def _visible_row(principal, workstream_id):
    """The one `Workstream` row `principal` may see with this id, or
    `None` (C-30).

    `visible_workstreams(principal).filter(pk=workstream_id)` was written
    out at four call sites in this file, three of them `.first()` and one
    `.exists()`. `pin_target`'s own docstring records that this query has
    already been folded once before, when `workstream_name` retired into
    it; this is the rest of that fold.

    `None` and "not visible" are the SAME answer on purpose -- the seam's
    404 house rule -- and every caller already treats them that way.
    """
    return visible_workstreams(principal).filter(pk=workstream_id).first()


def workstream_scope(principal, workstream_id) -> WorkstreamScope | None:
    """The pure scope value for `workstream_id`, or None when this
    principal may not be in that stream (or it does not exist -- the
    caller cannot tell the two apart, and answers 404 to both).

    `pinned_file_ids` is left EMPTY here and filled by
    `tools/rag/workstreams.py::scope_with_pins`, which is the only
    function that fills it, because the pin table lives in `tools/rag`
    (author decision 6, spec §6.2).
    """
    row = _visible_row(principal, workstream_id)
    if row is None:
        return None
    return _scope_from_row(principal, row)


def scope_for_conversation(principal, conversation) -> WorkstreamScope | None:
    """The scope of the stream a CONVERSATION belongs to -- `None` for a
    loose one, and `None` too when this principal may not be in that
    stream (spec §17.3's own anomaly: a shared conversation whose stream
    the recipient was never admitted to).

    "IS THERE A STREAM AT ALL" IS THE CALLER'S QUESTION EVERY TIME, and
    four callers were each answering it themselves before calling
    `workstream_scope` -- `agents.chat.views.thread.thread_context`,
    `agents.chat.views.turns`, `agents.chat.views.all_conversations` and
    `agents.runtime.loop` all typed the identical
    `workstream_scope(principal, conversation.workstream_id) if
    conversation.workstream_id else None` (audit 2, S21). Written once
    here, beside the function it guards, because the guard exists for
    `workstream_scope`'s own sake: passing it a `None` id would resolve
    no row and return `None` anyway, at the cost of a query.

    NOT A SECOND GATE. This adds no visibility rule of its own;
    `workstream_scope` is still the one that decides, through
    `visible_workstreams`.
    """
    if not conversation.workstream_id:
        return None
    return workstream_scope(principal, conversation.workstream_id)


def pin_target(principal, workstream_id) -> tuple[WorkstreamScope, str] | None:
    """The scope AND the display NAME for a `?pin_into=<id>` banner
    (`tools/rag/views.py::DocumentsView`, spec §14/§15.4: "a banner
    naming the stream"), in ONE row read -- `None` when this principal
    may not be in that stream or it does not exist.

    REPLACES A SEPARATE `workstream_name` THIS FUNCTION USED TO CALL
    BESIDE `workstream_scope` (final-polish round, quality-fix batch):
    the one production caller resolved `visible_workstreams(principal)
    .filter(pk=workstream_id).first()` TWICE for the SAME row on the
    SAME render -- once inside `workstream_scope`, once inside
    `workstream_name` -- because `WorkstreamScope`'s frozen value
    deliberately carries no display field (it travels on every retrieval
    turn and every content route, and widening it with a string every
    one of those callers would then carry, for the one caller that ever
    reads it, is exactly the kind of cost `scope_with_pins`'s own
    docstring reasons past for `pinned_file_ids`). This function keeps
    that same frozen contract -- the tuple's first half is built by the
    SAME `_scope_from_row` helper `workstream_scope` itself uses, off
    this row -- while resolving the row itself only once. With
    `workstream_name` gone (it had no other caller), this is now the
    SEAM this specific banner uses.
    """
    row = _visible_row(principal, workstream_id)
    if row is None:
        return None
    return (_scope_from_row(principal, row), row.name)


def workstream_visible(principal, workstream_id) -> bool:
    """Whether this principal may see this stream at all -- the cheap
    admission check a caller that only ever tests `is None`/`is not None`
    needs, without building the `WorkstreamScope` (a wall read plus
    several more queries) that `workstream_scope` returns and that such a
    caller then throws away.

    `tools/rag/workstreams.py::panel` is exactly that caller: its own
    scope-or-refuse gate used to call `workstream_scope` for this alone.
    `agents.workstreams` is `tools/rag`'s only legal door into `agents/`
    (the seam import law pins), so this is the narrow, purpose-built
    call that door offers -- not a reach into `agents.visibility`
    itself, which `tools/rag` may not import.

    Same admission as `workstream_scope`/`pin_target`
    (`visible_workstreams`), so all three can never disagree about which
    streams exist for this principal.
    """
    return _visible_row(principal, workstream_id) is not None


def set_upload_placement_default(principal, workstream_id, placement: str) -> bool:
    """Write `Workstream.default_upload_placement` from another column.

    A SEAM CALL, because the column is on an `agents` row and its caller
    (`tools.rag.views.document_upload`, spec §9.1) is `tools/rag` code.
    `""` clears it back to "ask every time". The predicate and the audit
    row live with the writer in `agents/visibility.py`; this is the door,
    not a second copy of the rule.
    """
    row = _visible_row(principal, workstream_id)
    if row is None:
        return False
    return set_workstream_upload_default(principal, row, placement)


def validated_next_url(request) -> str | None:
    """The `next` POST field, honoured only when it is a same-origin,
    same-scheme URL -- or `None`. THE DOOR (CONSOLIDATION WAVE, audit A
    B5's own "highlight": an orchestrator-verified open-redirect class),
    for the identical reason `set_upload_placement_default` above is
    one: `tools.rag.views.workstream_pin`'s own `next` field is `tools/
    rag` code redirecting after a WORKSTREAM-addressed action, and
    `tools/rag` may not import `agents.chat.service` (where this SAME
    guard already lives, `agents/chat/service.py::validated_next_url`,
    for `turns.py`/`conversations.py`'s own identical need) -- `agents.
    workstreams` is one of the three names `tools/` may cross into
    (import-law rule 2's closed set), so the guard is repeated here,
    self-contained, rather than reached for across a seam that does not
    exist.

    BEFORE THIS FIX: `workstream_pin` read `request.POST.get("next")`
    UNGUARDED and redirected straight to it -- the open-redirect class
    `agents/chat/service.py::validated_next_url`'s own docstring and
    `tools/vision/views.py::_validated_next_url` both already guard
    their identical field against, unguarded here alone.

    `url_has_allowed_host_and_scheme` is the SAME Django guard both
    siblings use.
    """
    next_url = request.POST.get("next", "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return None


def workstream_entitlement_cascade(entitlement_id: int, *, commit: bool) -> int:
    """This column's THIRD answer to "an entitlement is being deleted" --
    the stream's wall rows AND its taint tags, at both levels.

    DELETING AN ENTITLEMENT THEREFORE UN-TAINTS, and it says so in the
    trail (author decision 14). That is the one path by which a tag is
    removed in v1, and it is not an exception to "additive only" so much
    as the MEANING of deleting the entitlement: the label no longer
    exists, so no document carries it, so no share can be refused for
    lacking it. `tools.rag.labels.unlabel_all_for_entitlement` already
    makes the documents unlabelled in the same transaction, and the two
    cascades run under one `transaction.atomic()` in
    `identity.services.delete_entitlement`, so a stream cannot end up
    tagged with an entitlement its documents have lost.

    THE UNTAINT AUDIT ROWS ARE WRITTEN IN `commit=True` MODE (spec
    §16.1). Without them the `WORKSTREAM_TAINTED` rows would survive
    their own tags and `for_target("workstream", pk)` would show what
    came in and not that it left -- a trail that lies by omission about
    the only removal the product performs.

    NO CASCADE FOR `WorkstreamPin`, from THIS cascade: an entitlement
    delete removes neither the stream nor the document a pin names, so
    it removes no pin either. A pin still dies with its stream or its
    document, both by `CASCADE` -- and, since re-homing a document
    neither deletes nor is a `CASCADE`, `tools.rag.workstreams.
    set_document_workstream` ALSO deletes that document's pins itself,
    in the same transaction as the move: a pin naming the stream the
    document just left must not survive the move to name it falsely.
    """
    from agents.models import ConversationTaint, WorkstreamTaint

    walls = WorkstreamScopeEntitlement.objects.filter(entitlement_id=entitlement_id)
    conversation_tags = ConversationTaint.objects.filter(entitlement_id=entitlement_id)
    stream_tags = WorkstreamTaint.objects.filter(entitlement_id=entitlement_id)
    total = walls.count() + conversation_tags.count() + stream_tags.count()
    if not commit:
        return total
    actor = SERVICE_PRINCIPAL
    for row in conversation_tags.select_related("conversation"):
        audit.record(actor, actions.CONVERSATION_UNTAINTED, target_type="conversation",
                     target_key=row.conversation_id,
                     target_label=row.conversation.title, entitlement=entitlement_id,
                     cause="entitlement_deleted")
    for row in stream_tags.select_related("workstream"):
        audit.record(actor, actions.WORKSTREAM_UNTAINTED, target_type="workstream",
                     target_key=row.workstream_id, target_label=row.workstream.name,
                     entitlement=entitlement_id, cause="entitlement_deleted")
    walls.delete()
    conversation_tags.delete()
    stream_tags.delete()
    return total


def panels_for(principal, workstream_id) -> list[dict]:
    """Every registered panel's data for this stream, in registration
    order.

    NEVER RAISES (author decision 11). `import_string` failing is what
    the same-commit registration rule already prevents; a provider that
    RAISES at render time -- a store that is down, a migration half
    applied -- is a different failure, and the stream page must survive
    it. A failed panel renders as its own heading with one honest
    sentence, which is the posture `agents.chat.pickers.
    chat_picker_options` already takes for the model picker.
    """
    out = []
    for spec in all_workstream_panels():
        try:
            data = import_string(spec.provider)(principal, workstream_id)
            out.append({"key": spec.key, "label": spec.label,
                        "template": spec.template, "ok": True, "data": data})
        except Exception:  # noqa: BLE001 -- one broken panel, not a broken page
            logger.exception("workstreams: panel %r could not be rendered", spec.key)
            out.append({"key": spec.key, "label": spec.label,
                        "template": spec.template, "ok": False, "data": {}})
    return out


@dataclass(frozen=True)
class StreamAccess:
    """Why this principal may or may not read this stream, RIGHT NOW."""

    ok: bool
    missing: frozenset          # empty when ok
    missing_names: tuple        # for the reader's own error page
    unnamed_count: int
    # WHY, for the page and for the count. `via_share` was a sixth field
    # in an earlier draft and is dropped: nothing read it. `is_owner` IS
    # read (E7): `agents.chat.views.workstreams.workstream_page` threads
    # it straight into `_working_context` (the settings-page split's own
    # working-page builder; `_page_context` was its pre-split name), which
    # is what lets that builder skip re-deriving `may_manage_workstream`
    # for a principal this same request already settled the question
    # for -- so it stays.
    is_owner: bool


def stream_access(principal, workstream, *, settings_row=None) -> StreamAccess:
    """GATE TWO. Every non-owner read of a shared stream re-checks: tags
    grow and grants are revoked, so a share that passed gate one is not
    therefore passing now.

    - Owner, or `sees_all_content` -> `ok=True`, NO TAG CHECK. THE OWNER
      IS NEVER LOCKED OUT OF THEIR OWN STREAM BY ITS TAGS -- the tags
      were caused by turns IN THIS STREAM, which is the owner's space,
      and a stream whose owner could be shut out of it by a recipient's
      retrieval would be a space nobody could administer. Note what this
      deliberately no longer says: not "they caused them". Under ruling B
      a recipient's turn taints the owner's stream, so the owner may hold
      no grant for a tag on their own stream (spec §24 concern 6).
    - A share row exists -> `missing = taint_ids - held(principal)`;
      `ok = not missing`.
    - No share row -> the caller never reached this function;
      `visible_workstreams` already excluded the row and the view already
      answered 404.

    TWO EXTRA QUERIES per stream page view for a recipient (D4 correction
    to a claim of one) -- `workstream_taint_ids(workstream)` and
    `held_entitlement_ids(principal, ...)`, both against indexed columns,
    on a page that is already doing several -- and THREE on a refusal:
    `name_for_viewer`'s own `disclose_all=True` branch adds one more,
    `entitlement_names(missing_ids)`, to name the dormant-share 403
    page's own list.
    """
    from agents.visibility import name_for_viewer, workstream_taint_ids

    if may_manage_workstream(principal, workstream, settings_row=settings_row):
        return StreamAccess(True, frozenset(), (), 0, True)
    missing = workstream_taint_ids(workstream) - held_entitlement_ids(
        principal, settings_row=settings_row)
    if not missing:
        return StreamAccess(True, frozenset(), (), 0, False)
    # `disclose_all=True` -- THE ONE CALLER OF THAT MODE (ruling E). At
    # gate two the viewer IS the recipient and the missing set is
    # disjoint from what they hold by definition, so the default mode
    # would name nothing and owner decision 8's own sentence would be
    # unwritable.
    named, rest = name_for_viewer(missing, principal, disclose_all=True)
    return StreamAccess(False, missing, named, rest, False)


def transcript_for(principal, conversation_id, *, limit: int):
    """The most recent `limit` replayable root-depth turns of a stream
    conversation, as pure dicts -- `({"role": str, "text": str}, ...)`,
    oldest first -- for the consolidation job, or `None` when the
    principal may not read it.

    NOT `agents.runtime.prompt.history_messages`, AND `limit` IS WHY.
    That helper caps at `agents.limits.HISTORY_TURNS = 20`, which is a
    PROMPT budget for a live turn; consolidating only the last twenty
    turns would make spec §10.4's "the conversation" false without saying
    so. This is its own query, with its own cap, NAMED BY ITS OWN CALLER
    -- `limit` is required and keyword-only, and a non-positive one is
    refused rather than defaulted (author decision 16), so the honesty
    sentence the job writes always has a number behind it.

    EVERY ROOT-DEPTH COMPLETED TURN, TOOL TURNS INCLUDED -- not only
    what a person typed. A TOOL turn's own `text` can therefore carry
    retrieved document content (C-3, round-3 hardening), which is why
    `tools.rag.distil.distil_conversation` reaches this transcript ONLY
    through its own fence (`_fenced_transcript_block`, that module's own
    docstring) rather than embedding it directly: this function's job is
    the SELECTION, not the trust decision, and it hands every caller the
    same unfenced dicts regardless of what produced each turn's text.

    PURE DICTS, not rows: the caller is `tools/rag` code running in the
    worker process, and handing it `Turn` instances would make a
    `tools/rag` module hold `agents` ORM objects.
    """
    if limit <= 0:
        raise ValueError("transcript_for needs a positive limit")
    from agents.models import Turn
    from agents.visibility import visible_conversations

    conversation = visible_conversations(principal).filter(pk=conversation_id).first()
    if conversation is None:
        return None
    rows = (Turn.objects.filter(conversation=conversation, state=Turn.State.DONE, depth=0)
            .order_by("-index")[:limit])
    return tuple({"role": t.role, "text": t.text} for t in reversed(list(rows)))


def record_consolidation(conversation_id, *, through_index: int, at) -> None:
    """Stamp the conversation's consolidation bookkeeping (spec §10.5).

    ONE conditional `UPDATE`, never a read-then-save: the job runs in the
    worker process and the page may have written the row since.
    """
    from agents.models import Conversation

    Conversation.objects.filter(pk=conversation_id).update(
        consolidated_through_index=through_index, consolidated_at=at)


def taint_ids_for_conversation(conversation_id) -> frozenset[int]:
    """The conversation's taint tags, for the note document to inherit
    (spec §10.4 step 5). Owner decision 6: no laundering."""
    from agents.models import ConversationTaint

    return frozenset(
        ConversationTaint.objects.filter(conversation_id=conversation_id)
        .values_list("entitlement_id", flat=True))


def owner_fields_for_conversation(conversation_id) -> tuple[str, str] | None:
    """The conversation's own `(owner_kind, owner_key)`, as PURE STRINGS
    -- for the consolidation job's note document to carry as its own
    attribution (round-3 hardening H32/C-3, round 2). `None` when the
    conversation no longer exists (every real caller already confirmed
    it does, via `transcript_for`, before reaching this).

    TWO PLAIN STRINGS, NOT A `Principal`: this module returns pure data
    the same way `transcript_for` and `taint_ids_for_conversation` above
    do, and `identity.contracts.principals.Principal` may only be
    CONSTRUCTED inside `identity/contracts/principals.py`,
    `identity/request.py` and `identity/testing.py` -- an AST-enforced
    law (`foundation/ops/tests/test_import_law.py`'s own
    `_PRINCIPAL_CONSTRUCTORS`) this module is not on the list for. A
    caller that needs a `Principal` back builds one through that
    module's own constructor function, not by minting one here.

    ATTRIBUTION ONLY, NOT AUTHORITY: this is who the CONVERSATION is
    attributed to, read directly off its own two columns -- it says
    nothing about who may re-stage the resulting note (see
    `tools.rag.ingest._acts_for_the_box`'s own docstring for why the
    note stays in the `actor=None` shape regardless of what this
    function returns).
    """
    from agents.models import Conversation

    row = Conversation.objects.filter(pk=conversation_id).values(
        "owner_kind", "owner_key").first()
    return (row["owner_kind"], row["owner_key"]) if row else None


def tags_for_conversations(conversations, principal, *, settings_row=None) -> dict:
    """`{conversation pk: {"named": ((id, name), ...), "unnamed_count":
    int}}` for /chat/all/'s own table (round 14, Part 2: "entitlements
    tagged for that conversation").

    THE SAME DISCLOSURE RULE `agents.visibility.name_for_viewer` already
    enforces everywhere else on this surface (spec §12.3): a tag this
    VIEWER holds is named; everything else is counted, never named --
    applied here to a conversation's OWN full taint set rather than to a
    dormancy REFUSAL's "missing" set, which is the only difference in
    the QUESTION being asked -- the partition rule itself (named-if-
    held, counted-if-not) is identical, and `test_workstreams.py::
    TestTagsForConversations::test_matches_name_for_viewer_row_for_row`
    pins that two functions answering two different questions still
    agree bit for bit on any one row.

    NOT a per-row call to `name_for_viewer` itself, deliberately: that
    function re-reads `held_entitlement_ids` AND re-queries `entitlement_
    names` on EVERY call, which is exactly right for its own one-message
    callers and exactly wrong for a page listing up to `PAGE_SIZE`
    conversations at once -- this function pays each of those costs
    ONCE for the whole page instead: one `ConversationTaint` join over
    every listed conversation, one `held_entitlement_ids` read, and one
    `entitlement_names` call over the UNION of held ids actually present
    across every row (never per row), matching this column's own
    "bounded queries, equality pins" discipline (`sidebar_context`'s own
    docstring has the identical N+1 lesson, applied to a different
    join).
    """
    from collections import defaultdict

    from agents.models import ConversationTaint
    from identity.access import entitlement_names, held_entitlement_ids

    keys = [c.pk for c in conversations]
    by_conv: dict = defaultdict(set)
    for row in ConversationTaint.objects.filter(conversation_id__in=keys).values(
            "conversation_id", "entitlement_id"):
        by_conv[row["conversation_id"]].add(row["entitlement_id"])
    held = held_entitlement_ids(principal, settings_row=settings_row)
    nameable = set()
    for ids in by_conv.values():
        nameable |= (ids & held)
    names = dict(entitlement_names(nameable))
    out = {}
    for c in conversations:
        ids = by_conv.get(c.pk, set())
        held_ids = ids & held
        out[c.pk] = {
            "named": tuple(sorted(((i, names[i]) for i in held_ids), key=lambda pair: pair[1])),
            "unnamed_count": len(ids - held),
        }
    return out


def staleness_for(conversations) -> dict:
    """`{conversation pk: sentence}` -- the per-conversation staleness
    hint (spec §10.5).

    OWNER-ONLY, and the CALLER checks that (ruling F): a staleness hint
    is a prompt to press Consolidate, and rendering it to a recipient who
    would get a 404 would be the render-vs-gate pair broken on the
    surface it is most visible on. One predicate, checked ONCE for the
    page rather than once per row.

    INDEX DIFFERENCE, NOT TURN COUNT (author decision 20). `_finish`
    leaves deliberate gaps in `Turn.index`, so
    `latest - consolidated_through` can exceed the number of turns
    actually added. It is a HINT -- the owner's own word -- and an
    over-estimate of "how much has happened here" is the right direction
    for a hint to err in. The alternative is a `COUNT(*)` per
    conversation per page render.

    ONE AGGREGATE QUERY for the latest index per conversation, and one
    more for the tag note, over rows the page already loaded.
    """
    from django.db.models import Max

    from agents.models import ConversationTaint, Turn

    keys = [c.pk for c in conversations]
    latest = dict(Turn.objects.filter(conversation_id__in=keys)
                  .values_list("conversation_id").annotate(Max("index")))
    # E6: a dict keyed by conversation id, built ONCE, rather than the
    # nested comprehension's `for c in conversations` inside the loop
    # over taint rows -- that was O(rows × conversations); this is
    # O(rows) with an O(1) lookup per row, over the exact same condition.
    consolidated_at_by_pk = {c.pk: c.consolidated_at for c in conversations}
    tagged_since = {
        row["conversation_id"]
        for row in ConversationTaint.objects.filter(conversation_id__in=keys)
        .values("conversation_id", "at")
        if consolidated_at_by_pk.get(row["conversation_id"])
        and row["at"] > consolidated_at_by_pk[row["conversation_id"]]
    }
    out = {}
    for c in conversations:
        if c.consolidated_through_index is None:
            out[c.pk] = "Not consolidated"
            continue
        gap = (latest.get(c.pk) or 0) - c.consolidated_through_index
        sentence = "Up to date" if gap <= 0 else (
            f"{gap} turn{'' if gap == 1 else 's'} since last consolidated")
        if c.pk in tagged_since:
            # THE ONE CASE WHERE RE-CONSOLIDATING CHANGES *ACCESS* RATHER
            # THAN CONTENT (spec §10.5). A note's labels are recomputed
            # only on re-consolidation, so a conversation that acquired a
            # tag since its last one has a note that is UNDER-LABELLED
            # relative to its stream. That is safe -- the newer material
            # is not in the note -- but nothing on a turn-counting hint
            # would tell an owner it had happened.
            sentence += "; a tag has been added since"
        out[c.pk] = sentence
    return out
