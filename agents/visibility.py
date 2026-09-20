"""What one principal may see, per kind, in one place.

FIVE FUNCTIONS AND ONE RULE: every list, detail, POST, and delete under
`agents/chat` reaches its rows through one of these (`visible_
conversations`, `visible_agents`, `installed_agent_slugs`,
`visible_flows`, `create_conversation`). UI-3b adds four more writers on
exactly the same terms -- `rename_conversation`,
`set_conversation_archived`, `duplicate_conversation` and the shared
`may_manage_conversation` predicate all live here for the reason
`delete_conversation` and `create_conversation` already do. In open mode the four
visibility functions return everything, so this module changes NO
behaviour today -- which is precisely why `foundation/ops/tests/
test_column_boundaries.py` grows a guard that no other module under
`agents/chat` touches `Conversation`, `Agent`, or `Flow` `.objects`
directly. A rule with no current consequence is a rule nobody notices
breaking, and the whole value of writing it now is that Identity & Auth
edits these functions instead of auditing a package.

`owner_fields` USED TO LIVE HERE. Identity & Auth moved
it to `identity.access.owner_fields` -- all five owner-carrying tables
in three columns (`identity/services.py::all_owned_rows`) share ONE
definition rather than three that agree by convention. `create_conversation`
below imports it from there, exactly like every other stamping call
site.

It lives at the COLUMN root, not inside `agents/chat`, for the same
reason `agents/models.py` does: a future MCP edge and a future
management command need the same answer, and neither of them is a chat
view.

The 2026-08-27 addendum's consequence 2 is the shape being copied here:
retrieval keeps exactly ONE filter point, and a visibility scope is one
more filter argument THERE rather than a second copy in each caller.
This is that discipline applied to the agent column's own three tables.

IA-1 fills in every body: ownership plus the `resident`/
`service` carve-outs are now the real rule, not a promise. Every
function tests the OPEN BRANCH FIRST -- `sees_all_content`/`is_admin`
test `accounts_on()` before touching another table -- so an open box
still runs zero ownership queries and this module still changes no
behaviour on a box with no accounts.
"""
from __future__ import annotations

from typing import NamedTuple

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from agents.models import (
    Agent, Conversation, ConversationTaint, Flow, Share, Turn, Workstream,
    WorkstreamScopeEntitlement, WorkstreamTaint,
)
from agents.shares import share_level, shared_keys, shares_for
from identity import audit
from identity.access import (
    entitlement_ids_for_subject, entitlement_names, held_entitlement_ids, may_read_owned_row,
    owned_rows_q, owner_fields, sees_all_content,
)
from identity.contracts import actions


def visible_conversations(principal, *, settings_row=None):
    """Every conversation this principal may read.

    THE OPEN BRANCH IS FIRST, in this and in every function below:
    `sees_all_content` tests `accounts_on()` before it reads a second
    table, so an open box executes zero ownership queries and the
    queryset is the same `.all()` it was before this phase.

    `owned_rows_q(principal)` -- own rows, plus rows a shell path made
    when `principal` is an admin; full rule in `identity/README.md`
    §5b. A `Share` extends this to somebody the owner named -- directly
    or through a group -- at either level; `may_post_to` below is what
    then tells a `view` share apart from a `use` one.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only, threaded straight into `sees_all_content` --
    `may_manage_conversation`'s own docstring reasoning, applied to this
    function's own `IdentitySettings.get_solo()` read. `agents/chat/
    sidebar.py::sidebar_context` is the caller that asks THIS function
    more than once in a single render (the active list and the archived
    count) and threads the request's already-fetched row through both,
    so a sidebar render costs one `IdentitySettings` read here, not two.
    It is ALSO threaded into `owned_rows_q` -> `is_admin` below (Task 8
    review, R2): the settings assistant panel, open, reaches this
    function with the row it already has, and without threading it here
    too `owned_rows_q`'s internal `is_admin(principal)` call re-read the
    singleton a second time in the same request -- the same re-read
    `visible_agents`'s docstring closed on its own two legs. A caller
    with no row is unaffected: `owned_rows_q` re-fetches the singleton
    itself, same as always.
    """
    qs = Conversation.objects.select_related("agent")
    if sees_all_content(principal, settings_row=settings_row):
        return qs.all()
    # COMPUTED ONCE, reused for BOTH the top-level clause and the
    # nested workstream subquery below: `owned_rows_q` calls
    # `is_admin(principal, settings_row=settings_row)` internally, and
    # with no row threaded that reads `IdentitySettings` plus the user
    # row afresh on every call with no cross-call cache. Calling it
    # twice per render would cost this function two more queries than
    # the feature needs -- the resulting `Q` is a plain value, so
    # reusing it changes no result.
    owned = owned_rows_q(principal, settings_row=settings_row)
    return qs.filter(
        owned
        | Q(pk__in=shared_keys(Share.Target.CONVERSATION, principal))
        # A SHARE ON THE CONTAINER IMPLIES A SHARE ON THE CONTENTS
        # (spec §12.4): a stream share reaches every conversation in the
        # stream.
        | Q(workstream_id__in=shared_keys(Share.Target.WORKSTREAM, principal))
        # AN OWNER IS NOT OPAQUE TO THEIR OWN SPACE (ruling C): the
        # stream's owner reads every conversation in it, including ones a
        # recipient started there. Without this,
        # `create_conversation`'s `**owner_fields(principal)` stamp would
        # make a recipient's new thread invisible to the stream's owner
        # on every surface, while §7.3 still unioned its taint upward and
        # refused the owner's future shares for it.
        | Q(workstream__in=Workstream.objects.filter(owned))
    ).distinct()


def label_permitted_q(principal, *, settings_row=None) -> Q:
    """Rows this principal may reach past their entitlement labels.

    UNLABELLED ROWS PASS, which is what keeps a box that never labels an
    agent behaving exactly as it does today. Both halves are spelled out
    because the short spelling is wrong: `~Q(entitlement_labels__isnull=
    False)` alone would exclude a labelled row from EVERYBODY.

    An OR within the labels, never an AND: holding any one of a row's
    entitlements is enough, the same match documents and tools use.

    ONE FUNCTION, THREE BODIES (`visible_agents`,
    `installed_agent_slugs`, `visible_flows`), so the three cannot come to
    disagree about what a label means. It works for both models because
    both name the reverse accessor `entitlement_labels`.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only, threaded straight into `held_entitlement_ids` --
    which already accepts it (`identity/access.py:299`). Same bounded
    edge as `owned_rows_q`'s new parameter: most callers still leave it
    out; `visible_agents` threads it so the settings panel's own request
    does not re-read the singleton a second time here.
    """
    return Q(entitlement_labels__isnull=True) | Q(
        entitlement_labels__entitlement_id__in=held_entitlement_ids(
            principal, settings_row=settings_row))


def visible_agents(principal, *, settings_row=None):
    """Every ENABLED agent this principal may run.

    `resident=True` rows are the shipped defaults and are visible to
    everybody -- they are the platform's own offer, not somebody's
    private work.

    THE LABEL CLAUSE IS AND-ED ONTO THE OWNERSHIP OR, NOT OR-ED INTO IT
    (decision 35): a labelled row -- resident or owned -- is excluded
    from a principal who holds none of its entitlements, which is what
    makes labelling the shipped defaults or one's own agent actually
    restrict it rather than being bypassable by the person it is aimed
    at.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only, threaded into every leg that accepts it --
    `sees_all_content` on the cheap branch, and `owned_rows_q` /
    `label_permitted_q` on the non-cheap one (Task 8 review, M1): an
    administrator on an accounts-on box takes the non-cheap branch, and
    without threading here `owned_rows_q` -> `is_admin` and
    `label_permitted_q` -> `held_entitlement_ids` each re-fetch the
    singleton on their own, which is exactly the re-read
    `identity/tests/test_middleware.py::TestTheSingleRowRead` exists to
    forbid on every view, this one included. A caller with no row (every
    one before the settings assistant panel) is unaffected: each of the
    three re-fetches the singleton itself, same as always. This does NOT
    thread into `shared_keys` -- the share-table read it costs has no
    `settings_row=` parameter to take, and adding one is a wider change
    this function does not make here.
    """
    qs = Agent.objects.filter(enabled=True)
    if sees_all_content(principal, settings_row=settings_row):
        return qs
    return qs.filter(
        (owned_rows_q(principal, settings_row=settings_row) | Q(resident=True)
         | Q(pk__in=shared_keys(Share.Target.AGENT, principal)))
        & label_permitted_q(principal, settings_row=settings_row)
    ).distinct()


def installed_agent_slugs(principal):
    """Every slug this principal has installed, ENABLED or not -- the
    set the "Add the default X" offers are computed against. Same
    visibility rule, minus the `enabled` filter, for the reason this
    function's original docstring gives."""
    qs = Agent.objects.all()
    if not sees_all_content(principal):
        qs = qs.filter(
            (owned_rows_q(principal) | Q(resident=True)
             | Q(pk__in=shared_keys(Share.Target.AGENT, principal)))
            & label_permitted_q(principal)
        )
    return qs.values_list("slug", flat=True).distinct()


def visible_agent_slugs(principal) -> frozenset[str]:
    """Lower-cased slugs of the agents this principal may RUN -- for the
    two runtime call sites that filter `agent.<slug>` tool keys. ONE
    QUERY, once per turn, not once per key."""
    return frozenset(s.lower() for s in visible_agents(principal).values_list(
        "slug", flat=True))


def chat_surface_agents(principal):
    """`visible_agents`, minus the agents whose surface is not `/chat/`
    (owner ruling 2, spec §7).

    A THIN WRAPPER ON THE ONE GATE, never a narrowing of it. The gate
    itself must keep answering YES for these rows -- the settings panel
    reads exactly the same agent and the same conversation, in the same
    request cycle -- so what this expresses is a SURFACE preference, and a
    surface preference has no business inside a function whose question is
    "may this principal read this row".

    CASE-INSENSITIVE (Task 6 review, Finding 4): `Lower("slug")`, the
    same comparison `Agent`'s own `uniq_agent_slug_ci` constraint makes,
    rather than an exact `slug__in` -- slug identity is
    case-insensitive everywhere else this platform tests it
    (`install_default`'s `slug__iexact`, `missing_defaults`'s `.lower()`,
    `_startable_agent`'s `slug__iexact`), and an exact match here would
    have let a case-variant row (`Settings-Helper`) slip back onto
    `/chat/` even though `install_default` would report it as already
    installed.
    """
    from agents.defaults import SETTINGS_SURFACE_SLUGS

    surface_slugs = {slug.lower() for slug in SETTINGS_SURFACE_SLUGS}
    return visible_agents(principal).annotate(
        _settings_surface_slug_ci=Lower("slug")
    ).exclude(_settings_surface_slug_ci__in=surface_slugs)


def chat_surface_conversations(principal, *, settings_row=None):
    """`visible_conversations`, minus the conversations of agents whose
    surface is not `/chat/`. The conversation half of the wrapper above,
    and it threads `settings_row` unchanged so its callers keep their
    one-read-per-render property.

    NO `chat_surface_only=False` ESCAPE HATCH (spec decision 22): nothing
    needs one. The panel reads the un-narrowed gate directly, so a keyword
    defaulting to True with no caller passing False would be a knob with
    no reader -- and it would invite the first author who hits a 404 to
    flip it rather than ask why.

    CASE-INSENSITIVE, the same `Lower("slug")` comparison
    `chat_surface_agents` makes and for the same reason (Task 6 review,
    Finding 4).
    """
    from agents.defaults import SETTINGS_SURFACE_SLUGS

    surface_slugs = {slug.lower() for slug in SETTINGS_SURFACE_SLUGS}
    return visible_conversations(principal, settings_row=settings_row).annotate(
        _settings_surface_slug_ci=Lower("agent__slug")
    ).exclude(_settings_surface_slug_ci__in=surface_slugs)


def visible_flows(principal):
    """Every ENABLED flow this principal may run, ASKED WITH THE ACTING
    PRINCIPAL -- which after the acting rule (Identity & Auth, spec
    section 5.3) is the USER, not the agent.

    THIS REVERSES THE 2026-08-28 RULING recorded here before. That
    ruling said a delegate sees the flows IT may run rather than the
    flows its caller may; the acting rule says an agent is a tool a
    person wields, not a second person, so there is no hop at which the
    acting principal widens. Its three runtime callers
    (`narrowed_flow_spec`, `flow_row_roles`, `run_flow`) therefore
    narrow a delegate to the flows the ROOT USER may run -- which is the
    whole point: flipping the posture changes what an agent can run, not
    merely what a page lists. Recorded in an amendment to ADR 0015
    section 10; ADR 0016, written after IA-2, records the same ruling
    from the grants side.

    Same ownership rule as `visible_agents`: a `resident=True` flow is
    the platform's own offer, and a service-owned one is a shell path's
    output, visible to an administrator for the same reason. Same label
    clause too, AND-ed onto the ownership OR for the identical reason
    (decision 35).
    """
    qs = Flow.objects.filter(enabled=True)
    if sees_all_content(principal):
        return qs
    return qs.filter(
        (owned_rows_q(principal) | Q(resident=True)
         | Q(pk__in=shared_keys(Share.Target.FLOW, principal)))
        & label_permitted_q(principal)
    ).distinct()


def visible_turn(principal, turn_id):
    """The `Turn` `turn_id`, or None.

    RESOLVED THROUGH THE CONVERSATION, never by bare pk.
    `chat-turn-status` takes a sequential integer, which is the
    enumeration exposure the agents spec's gap 4 recorded, and this is
    where it closes. `None` for an unknown id as well as an invisible
    one -- the view answers 404 to both, and the caller cannot tell them
    apart, which is the point.
    """
    return Turn.objects.filter(
        pk=turn_id, conversation__in=visible_conversations(principal)
    ).select_related("conversation").first()


def latest_completed_turn_index(conversation) -> int | None:
    """The highest `Turn.index` this conversation has actually completed
    at root depth, or `None` when it has none.

    HERE, beside `visible_turn`, because this module is where `agents/chat`
    reaches `Turn` rows -- the same ruling `resident_agent_tool_keys`
    records for `Agent`: it is not a visibility question, but it is a
    `Turn` question, and the guard is flat.

    ROOT DEPTH AND `DONE` ONLY, matching `agents.runtime.prompt`'s own
    `_REPLAYABLE_STATES`/`_ROOT_DEPTH` pair, so the index this stamps is
    the index a transcript would actually have read.
    """
    return (Turn.objects.filter(conversation=conversation, state=Turn.State.DONE, depth=0)
            .order_by("-index").values_list("index", flat=True).first())


def may_manage_conversation(principal, conversation, *, settings_row=None) -> bool:
    """Whether `principal` may delete, rename, duplicate, archive,
    unarchive, pin, or unpin `conversation`.

    ROUND 20 ADDS PIN/UNPIN TO THIS SAME PREDICATE, not a new one --
    the owner's own words ("same gate — whoever may archive may pin")
    match the reasoning "ONE PREDICATE FOR ALL FIVE", below, already
    gives for why archive/unarchive share it rather than getting their
    own: pinning is the OWNER's bookmark (`Conversation.pinned_at`'s own
    docstring), so the identical "shared to somebody is not manageable
    by them" rule applies unchanged.

    `settings_row`: an already-fetched `IdentitySettings`, OPTIONAL and
    keyword-only, threaded straight into `sees_all_content`. THE SIDEBAR
    ASKS THIS ONCE PER LISTED ROW, which is exactly the caller
    `sees_all_content`'s own `settings_row=` docstring names as the one
    that must not re-read the singleton per row -- and it is worse than
    one extra read each, because `identity.access._user_row` memoises on
    the settings-row INSTANCE, so a fresh row per call defeats the
    `identity_user` cache too. Measured before this argument existed:
    +2 queries per conversation on `/chat/` and on every thread page
    (47 -> 95 at 25 rows). `models.queue.views.QueueView` already
    threads its own row through `may_read_job_content` for the identical
    reason; this is that pattern, on this column's list.

    A single-row caller (every mutating view below) passes nothing and
    gets the one read it always did.

    ONE PREDICATE FOR EVERY MENU ACTION (UI-3b, extended by round 20),
    and that is the ruling rather than an accident of refactoring. The
    sidebar's per-conversation menu offers every one of them side by
    side; a separate predicate per action that happened to agree today
    would be one chance per action for it to drift, and the drift would
    show up as a menu entry that 404s. It was `_may_delete` when delete
    was the only mutation; the body is unchanged and the name now says
    what it actually gates.

    A CONVERSATION SHARED TO SOMEBODY IS NOT MANAGEABLE BY THEM. A
    recipient reads the thread (`visible_conversations` admits it
    through `Share`) and may post into it at `use` level
    (`may_post_to`), and that is the whole of what a share grants:
    `may_read_owned_row` is false for them, so renaming, duplicating,
    archiving, unarchiving, pinning, unpinning or deleting somebody
    else's thread is refused -- a 404 from the view, since a 403 on a
    row-addressed URL confirms the row exists. That is the rule delete
    already had; every action beside delete inherits it rather than
    inventing one, which stays true when an eighth menu entry lands.

    THE READ SIDE AND THE DELETE SIDE MUST AGREE ABOUT SERVICE-OWNED
    ROWS. `owned_rows_q` shows an administrator every conversation a
    shell path made; without the middle branch here, that administrator
    would see the row on the list, click delete, and get a 404 --
    a surface that shows a row it will not let you act on, which is
    the worst of the three possible answers.

    RULING: an administrator MAY delete a service-owned conversation.
    It is the same reasoning that makes them visible in the first place
    -- nobody's privacy is at stake in a row a command produced, and
    pruning what an automated path left behind is exactly the operator
    work `is_admin` exists for. `is_admin`, not `sees_all_content`, for
    the same reason: this is administration, not reading.

    `may_read_owned_row` -- the row-predicate mirror of `owned_rows_q`
    -- answers the service-branch/own-branch half; `sees_all_content` is
    this function's own early return, same as it is `owned_rows_q`'s
    every caller's.
    """
    if sees_all_content(principal, settings_row=settings_row):
        return True
    # NOT THREADED FURTHER, and that is a known, bounded edge rather
    # than an oversight: `may_read_owned_row`'s SERVICE-OWNED branch
    # (`identity/access.py`) calls a bare `is_admin(principal)`, so a
    # sidebar whose rows were all made by a shell path would still cost
    # one read each. That signature is shared with `tools/vision`'s own
    # `may_read_job`, so widening it is a cross-column change and its
    # own question -- and the case it would pay for (a person whose
    # conversation list is mostly `manage.py agent_turn` output) is not
    # the one this fix was measured against. The common path -- own
    # rows, shared rows, an administrator reading -- is flat.
    if may_read_owned_row(principal, conversation):
        return True
    # RULING C: consolidating and deleting a conversation in a stream is
    # available to its creator AND to the stream's owner, and to nobody
    # else. `PROTECT`'s count is then always a count of rows the person
    # reading the refusal can act on (spec §8.1).
    #
    # `conversation.workstream` IS ALREADY LOADED on the two callers that
    # ask this per row -- `agents/chat/sidebar.py` and the stream page --
    # because both `select_related("workstream")`. That is not an
    # optimisation; it is the condition of this branch being affordable,
    # and `test_sidebar.py` pins it with a query count at 1 row and at
    # 25.
    stream = conversation.workstream
    return stream is not None and may_read_owned_row(principal, stream)


def delete_conversation(principal, conversation) -> bool:
    """Delete `conversation` if `principal` may. True if it went.

    THE DELETE LIVES HERE for the same reason the create does: a view
    that could reach the manager could reach it without the ownership
    check, and a guard with an exception is a guard somebody widens.
    The conversation's `Share` rows are deleted in the same transaction.

    `tools.rag`'s OWN `DocumentAttachment` rows go too (round 11
    re-review minor 4): `agents.attachments.delete_attachments_for`,
    the delete-time twin of `agents.attachments.attached_documents`
    (the SAME `agents.contracts.attachments` registry, resolved the
    identical way) -- `conversation_id` is a UUID BY VALUE there, never
    a real FK (`tools/rag` may not import `agents.models`), so nothing
    else would ever clean those rows up once this conversation is gone.
    NEVER RAISES, and its own count is not this function's business --
    a broken `tools.rag` cleanup provider must not block a delete the
    actor already confirmed.
    """
    if not may_manage_conversation(principal, conversation):
        return False
    with transaction.atomic():
        # IA-2: the thread's shares go with it, in the SAME transaction.
        # No `post_delete` receiver: this repository uses no Django
        # signals anywhere, and a stale `Share` row is inert -- every
        # reader resolves the target first. This is the one shipped
        # delete surface, so it is the one place that has to say so.
        Share.objects.filter(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk)).delete()
        from agents.attachments import delete_attachments_for

        delete_attachments_for(conversation.id)
        conversation.delete()
    return True


def rename_conversation(principal, conversation, title: str) -> bool:
    """Give `conversation` a new `title` if `principal` may. True if it
    was written.

    THE TITLE ARRIVES ALREADY TRUNCATED. The word-boundary rule lives in
    `agents.chat.service.truncate_title` -- one function, whose other
    caller is the first-message title -- and this module may not import
    it: `agents/visibility.py` sits at the COLUMN ROOT and
    `agents/chat/` is an app inside it, so a call in this direction
    would invert the dependency the whole file exists to keep one-way.
    The view truncates and hands the finished string over, exactly as it
    hands over an already-resolved `principal`.

    `update_fields`, so a rename touches the title and the `auto_now`
    stamp and nothing else. `updated_at` moving means a renamed thread
    jumps to the top of the sidebar -- accepted behaviour (UI-3b): the
    ordering column is "last touched", and renaming is touching it.
    """
    if not may_manage_conversation(principal, conversation):
        return False
    conversation.title = title
    conversation.save(update_fields=["title", "updated_at"])
    return True


def set_conversation_archived(principal, conversation, *, archived: bool) -> bool:
    """Archive or unarchive `conversation` if `principal` may. True if
    the stamp was written.

    ONE FUNCTION FOR BOTH DIRECTIONS, keyed on a flag rather than two
    near-identical bodies: the two routes differ only in which value
    goes into the column, and the permission question they ask is
    identical. `timezone.now()` on the way in, `None` on the way out --
    the column records WHEN, and unarchiving is not "when it came back"
    but "it is not archived", which is what `null` says.
    """
    if not may_manage_conversation(principal, conversation):
        return False
    conversation.archived_at = timezone.now() if archived else None
    conversation.save(update_fields=["archived_at", "updated_at"])
    return True


def set_conversation_pinned(principal, conversation, *, pinned: bool) -> bool:
    """Pin or unpin `conversation` if `principal` may. True if the
    stamp was written.

    ROUND 20's OWN COPY of `set_conversation_archived`'s shape, above,
    for the identical reason: one function keyed on a flag, not two
    near-identical bodies, and `timezone.now()`/`None` for the same
    "the column records WHEN, and unpinning is not a timestamp of its
    own" logic that function's own docstring already gives.
    """
    if not may_manage_conversation(principal, conversation):
        return False
    conversation.pinned_at = timezone.now() if pinned else None
    conversation.save(update_fields=["pinned_at", "updated_at"])
    return True


# The three `Turn.State` values a turn never leaves. A QUEUED or RUNNING
# turn is mid-flight in the ORIGINAL conversation's `agent.turn` job:
# that job will write its answer back to the row it was given, which is
# the original's, so a copy of the placeholder could only ever sit in
# the new thread as a turn that never finishes.
_TERMINAL_TURN_STATES = (Turn.State.DONE, Turn.State.FAILED, Turn.State.CANCELLED)


def duplicate_conversation(principal, conversation, *, title: str):
    """A new conversation with `conversation`'s finished history, owned
    by `principal` -- or `None` if they may not.

    THE COPY IS THE ACTING PRINCIPAL'S, not the original's owner's
    (`owner_fields(principal)`, exactly as `create_conversation`
    stamps): an administrator who duplicates somebody else's thread gets
    their OWN thread, not a second row filed under a person who did not
    ask for it.

    A CONSEQUENCE WORTH STATING, because it is not obvious from that
    sentence: an administrator duplicating under `admin_sees_content`
    makes a copy they own BY OWNERSHIP, and ownership is not what the
    content toggle governs. Switch the toggle back off and the original
    becomes unreadable to them while the copy stays -- a durable copy
    the setting no longer reaches. That is accepted rather than
    overlooked. Nobody's privacy is newly at risk: reading the thread is
    what the toggle permitted, and anyone who could read it could
    already paste it anywhere; the original is untouched and its audit
    rows are intact. The alternative -- a copy stamped with the
    ORIGINAL's owner -- would file a row under somebody who never asked
    for it, which is the worse of the two.

    THREE THINGS ARE DELIBERATELY NOT COPIED, and each is a decision:

      * ANY TURN NOT IN A TERMINAL STATE (`_TERMINAL_TURN_STATES`
        above) -- see that constant's own note.
      * `Turn.invocation`. A `ToolInvocation` is an AUDIT RECORD of a
        call that really happened, in the original thread, by a named
        principal (the 2026-08-27 addendum's consequence 3). Pointing a
        copied turn at it would make one audit row appear to belong to
        two conversations; writing a second row would invent a call
        that never ran. The copy keeps the tool card's own visible
        content (`tool_call`, `data`, `text`) and carries no audit
        link, which is the honest third answer.
      * `Turn.queue_job_id`. It names the job that produced the
        ORIGINAL row; a copy was produced by nothing.

    Indexes are renumbered from 0 over the surviving turns rather than
    carried across, so a copy of a thread with a dropped mid-flight turn
    has no gap in it -- `uniq_turn_index` would accept either, and a
    contiguous thread is the one a reader can reason about.

    THE COPY STAYS IN THE STREAM (ruling D, spec §23.D), and it is
    spelled out here because this is where duplication is defined.
    Unamended, this function built the copy with no `workstream` while
    copying `text`, `data` and `artifacts` -- the quoted document text
    and the `document:<id>` references -- so one menu click would have
    produced a LOOSE thread holding labelled material outside every gate
    the workstreams phase builds, shareable through
    `chat-conversation-share`. An administrator under
    `admin_sees_content` could have done it to anybody's stream
    conversation.

    Carrying the stream identity is CONSISTENT WITH IMMUTABILITY RATHER
    THAN AN EXCEPTION TO IT: nothing moves, and the new row's
    `workstream` is stamped once at creation like every other row's. v1
    offers NO LOOSE COPY of a stream conversation -- there is no control
    for it and no parameter that would produce one.
    """
    if not may_manage_conversation(principal, conversation):
        return None
    with transaction.atomic():
        copy = Conversation.objects.create(
            agent=conversation.agent, title=title,
            workstream=conversation.workstream,      # THE COPY STAYS IN THE STREAM
            **owner_fields(principal),
        )
        Turn.objects.bulk_create([
            Turn(
                conversation=copy, index=index, role=turn.role, text=turn.text,
                tool_call=turn.tool_call, data=turn.data, artifacts=turn.artifacts,
                depth=turn.depth, state=turn.state, error=turn.error,
            )
            for index, turn in enumerate(
                Turn.objects.filter(conversation=conversation,
                                    state__in=_TERMINAL_TURN_STATES).order_by("index")
            )
        ])
        # ALWAYS, not only for a stream conversation (ruling D): a loose
        # thread's tags are recorded too (author decision 9), and a copy
        # that dropped them would launder a loose conversation exactly as
        # it would a stream one.
        ConversationTaint.objects.bulk_create([
            ConversationTaint(conversation=copy, entitlement_id=t.entitlement_id,
                              first_turn=t.first_turn)
            for t in conversation.taint_tags.all()
        ])
    return copy


def may_post_to(principal, conversation) -> bool:
    """Whether `principal` may add a TURN to `conversation`.

    Owner, `sees_all_content`, or a `use`-level share. A `view` share
    reads the thread and does not post into it -- which is the whole
    reason `Share.Level` exists rather than the level being implied.

    THE SAME TWO CLAUSES `visible_conversations` GAINED (spec §12.4): "new
    turns in those conversations" is reached by the identical pair, at
    `use`, because a workstream share IS `use`-by-construction (author
    decision 16 -- there is no `view`-level stream share to tell apart
    from a `use` one, unlike the per-conversation share checked above).
    """
    if sees_all_content(principal):
        return True
    if may_read_owned_row(principal, conversation):
        return True
    if share_level(principal, Share.Target.CONVERSATION,
                   str(conversation.pk)) == Share.Level.USE:
        return True
    if conversation.workstream_id is not None and str(conversation.workstream_id) in (
            shared_keys(Share.Target.WORKSTREAM, principal)):
        return True
    # AN OWNER IS NOT OPAQUE TO THEIR OWN SPACE (ruling C): the stream's
    # owner may post into a conversation a recipient started there, same
    # as they may read and manage it.
    stream = conversation.workstream
    return stream is not None and may_read_owned_row(principal, stream)


def may_read_conversation_shares(principal, conversation) -> bool:
    """Whether `principal` may SEE this thread's share list.

    THE SAME PREDICATE `share_conversation` and `revoke_share` enforce
    (`_may_share` is now a one-line alias of this), so the page cannot
    render a control the POST refuses -- the render-vs-gate rule, applied
    where the rule first came from.
    """
    return sees_all_content(principal) or may_read_owned_row(principal, conversation)


def _may_share(principal, conversation) -> bool:
    return may_read_conversation_shares(principal, conversation)


def share_conversation(principal, conversation, *, user=None, group=None, level):
    """Add one `Share` row, or WIDEN/NARROW the existing one for the same
    subject to `level` -- or answer None if `principal` may not.

    Exactly one of `user`/`group`, or the database's XOR constraint
    refuses the row; the caller's form makes the other case
    unrepresentable.

    `update_or_create`, NOT `create` (code-review finding, 2026-08-31):
    the two partial unique constraints on `Share` (one subject, one
    target, one row) mean a SECOND share to a subject already sharing
    this conversation raises `IntegrityError` from a plain `create` --
    a 500 on a never-500 surface, reachable by a double form submit, a
    back-button re-POST, or the ordinary "change this share from view to
    use" gesture the sharing UI offers. Last-write-wins on `level` is
    not a compromise here; it IS that gesture.
    """
    if not _may_share(principal, conversation):
        return None
    if bool(user) == bool(group):
        return None
    if level not in Share.Level.values:
        return None
    row, _ = Share.objects.update_or_create(
        target_type=Share.Target.CONVERSATION, target_key=str(conversation.pk),
        user=user, group=group,
        defaults={
            "level": level,
            "shared_by_id": int(principal.key) if principal.kind == "user" else None,
        },
    )
    return row


class RevokedShare(NamedTuple):
    """What `revoke_share` hands back on success.

    THE ROW IS GONE by the time the caller sees this (review finding,
    2026-08-31: the audit row for a revoke used to name nothing but the
    conversation, so the log could never answer "whose access was
    removed"), and `agents/chat` may not query `Share` directly
    (column-boundary rule) to reconstruct that after the fact. So this
    carries exactly what the deleted row knew about itself, for the
    caller's audit write.
    """

    subject: str        # "user" or "group"
    subject_key: int
    level: str


def _revoke_share_row(target_type, target_key, share_id) -> "RevokedShare | None":
    """Delete one `Share` row scoped to `(target_type, target_key)` and
    hand back what it knew about itself, or `None` if `share_id` names
    no such row.

    TWO REFUSALS, and neither is optional. `share_id` is checked with
    `isdecimal()` FIRST -- it arrives from a POST body, so a non-numeric
    value would reach `Share.objects.get(pk=...)` and raise `ValueError`,
    a 500 on a never-500 surface. `isdecimal()`, NOT `isdigit()` (review
    finding, 2026-08-31): `isdigit()` admits characters like "²" that
    `int()` itself rejects, which is the exact same 500 by another
    route. And the row is then checked against `target_key` in the SAME
    lookup, or an owner of one conversation (or workstream) could revoke
    a share on another by guessing a sequential id -- an IDOR that reads
    as a legitimate action in the audit log.

    Both callers (`revoke_share`, `revoke_workstream_share`) already
    checked the caller's own permission before reaching here; this
    function does not repeat that check. The view answers 404 to either
    refusal, never 403, because a 403 would confirm that some other
    target's share carries that id.
    """
    if not str(share_id).isdecimal():
        return None
    row = Share.objects.filter(pk=int(share_id),
                               target_type=target_type,
                               target_key=str(target_key)).first()
    if row is None:
        return None
    revoked = RevokedShare(
        subject="user" if row.user_id else "group",
        subject_key=row.user_id or row.group_id,
        level=row.level,
    )
    row.delete()
    return revoked


def revoke_share(principal, conversation, share_id):
    """Remove one `Share` row FROM THIS CONVERSATION. The removed row's
    `RevokedShare` if it went, None if it may not or does not belong
    here. See `_revoke_share_row` for the two refusals this leans on.
    """
    if not _may_share(principal, conversation):
        return None
    return _revoke_share_row(Share.Target.CONVERSATION, conversation.pk, share_id)


def resident_agent_tool_keys() -> dict[str, list[str]]:
    """`{tool_key: [resident agent slugs that declare it]}` -- the
    tool-label page's shell-path warning.

    HERE, NOT IN `agents/chat`, because this module is the one place that
    package reaches `Agent.objects` at all. It is not a visibility
    question, but it is an `Agent` question, and the guard is flat.

    `manage.py agent_turn` runs an agent as the ONE service principal,
    and a service principal holds no entitlements -- grants attach to a
    user or a group by the XOR constraint, and service-account tokens are
    a later phase. So labelling a tool a shipped (`resident=True`) agent
    declares makes that shell path SILENTLY weaker: the agent is simply
    not told the tool exists, and the failure reads as a model that
    cannot find anything rather than as a refusal. The form names those
    agents so the operator is warned before they cause it.

    `.order_by("slug")` -- without it the comma-joined list the template
    renders (`row.shell_agents|join:", "`) is in whatever order the
    database happens to return rows, which is not guaranteed stable
    across two calls. A page an operator reloads should not shuffle the
    names in its own warning.
    """
    out: dict[str, list[str]] = {}
    for slug, keys in Agent.objects.filter(resident=True, enabled=True) \
                                   .order_by("slug") \
                                   .values_list("slug", "tool_keys"):
        for key in keys or ():
            out.setdefault(key, []).append(slug)
    return out


def labellable_agents():
    """Every `Agent` row, unfiltered, for `/chat/access/`.

    NOT `visible_agents`: that function is ASKED WITH A PRINCIPAL and
    answers "may this principal run it", which for an administrator with
    `admin_sees_content` off would hide a member's own agent from the
    very page that exists to label it. `chat-agent-entitlements` is
    CLASS S -- `IdentityGateMiddleware` refuses anybody but an
    administrator before this view ever runs -- so there is no narrower
    principal to ask here, the same reason `tools.rag.access.
    listable_documents`'s admin branch is `Document.objects.all()`
    unconditionally. Disabled and resident rows are included: an
    operator restricting a shipped or a switched-off agent still needs
    to find it on this page.
    """
    return list(Agent.objects.order_by("slug"))


def labellable_flows():
    """The same, for `Flow`."""
    return list(Flow.objects.order_by("slug"))


def labellable_agent(pk):
    """One `Agent` row by pk, for `/chat/access/`'s POST handler -- or
    `None`. A ONE-QUERY reader, unconditional for the identical reason
    `labellable_agents` is: CLASS S already means every caller here is
    an administrator. Replaces a `next((r for r in labellable_agents()
    if r.pk == pk), None)` full-table scan per POST, which is the same
    N+1 shape `tool_entitlement_ids`'s own docstring warns against, just
    on the write side rather than the render side.
    """
    return Agent.objects.filter(pk=pk).first()


def labellable_flow(pk):
    """The same, for `Flow`."""
    return Flow.objects.filter(pk=pk).first()


def create_conversation(principal, agent, *, workstream=None):
    """A new conversation owned by `principal`, optionally BORN IN a
    workstream.

    RULING (2026-08-28): the CREATE lives here too, not in the view.
    The earlier draft kept `Conversation.objects.create(...)` in
    `conversations.py` and carved a hole in the guard for it -- and a
    guard with an exception is a guard somebody will widen. Keeping it
    here makes the rule flat and checkable in one sentence: NO module
    under `agents/chat` touches these three managers, for any reason.

    It also puts the ownership stamp where ownership is decided. A
    view that could create a row could create one without
    `owner_fields`, and that row would be invisible to every filter
    Identity & Auth later adds -- the exact failure these columns
    exist to prevent.

    `workstream` IS STAMPED ONCE AND NEVER WRITTEN AGAIN (owner decision
    2). There is no move affordance in v1, no route writes this column
    after creation, and this module exposes no setter for it -- a thread
    that can change containers is a thread whose taint history is a lie.
    The caller has already resolved the row through `visible_workstreams`
    (and, in WS-2, through `stream_access`); this function stamps it.
    """
    return Conversation.objects.create(agent=agent, workstream=workstream,
                                       **owner_fields(principal))


def visible_workstreams(principal, *, settings_row=None):
    """Every workstream this principal may read.

    THE OPEN BRANCH IS FIRST, as in every function in this module.
    `owned_rows_q` is own-rows-plus-service-rows-for-an-admin; a `Share`
    extends it to somebody the owner named. The DORMANCY check is NOT
    here -- a dormant share still LISTS (spec §12.2), and hiding it
    would leave a recipient with a stream that vanished and no sentence
    explaining why.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only, threaded straight into `sees_all_content` -- the
    same reason `visible_conversations`'s own `settings_row=` docstring
    gives, and the caller (`agents/chat/sidebar.py::sidebar_context`) is
    the same one, asking this and `visible_conversations` in the same
    render. `owned_rows_q`'s internal `is_admin(principal)` call is NOT
    threaded, for the identical cross-column reason that function's own
    docstring names.
    """
    qs = Workstream.objects.all()
    if sees_all_content(principal, settings_row=settings_row):
        return qs
    return qs.filter(
        owned_rows_q(principal)
        | Q(pk__in=shared_keys(Share.Target.WORKSTREAM, principal))
    ).distinct()


def may_manage_workstream(principal, workstream, *, settings_row=None) -> bool:
    """Whether `principal` may rename, describe, instruct, scope,
    archive, set the upload default on, share, consolidate in, or delete
    `workstream`.

    ONE PREDICATE FOR ALL OF THEM (author decision 7), and that is the
    ruling rather than an accident of refactoring -- the same ruling
    `may_manage_conversation` already made for its own five actions and
    `identity-entitlement-edit` for its `_ENTITLEMENT_ACTIONS` tuple.
    Seven predicates that happened to agree today would be seven chances
    for one of them to drift, and the drift would show as a control that
    404s.

    A STREAM SHARED TO SOMEBODY IS NOT MANAGEABLE BY THEM (owner
    decision 7): a recipient reads and converses, and re-sharing,
    editing the wall, changing pins, uploading, consolidating and
    deleting stay owner-only in v1.

    `settings_row` is threaded straight into `sees_all_content`, for the
    reason `may_manage_conversation`'s own docstring gives at length: the
    sidebar asks a per-row predicate and a fresh singleton read per row
    also defeats `identity.access._user_row`'s memoisation.
    """
    if sees_all_content(principal, settings_row=settings_row):
        return True
    return may_read_owned_row(principal, workstream)


def create_workstream(principal, name: str, *, description: str = ""):
    """A new workstream owned by `principal`, or `None` when the name
    collides with one they already have.

    THE CREATE LIVES HERE, not in the view, for the reason
    `create_conversation`'s own docstring gives: a view that could create
    a row could create one without `owner_fields`, and that row would be
    invisible to every filter identity added.

    `None` RATHER THAN AN EXCEPTION on a collision (author decision 8).
    `uniq_workstream_name_ci_per_owner` is a real thing an operator
    causes by typing a name twice, on a never-500 surface, so the caller
    renders a named message rather than catching `IntegrityError`.
    """
    name = (name or "").strip()
    if not name:
        return None
    fields = owner_fields(principal)
    if Workstream.objects.filter(name__iexact=name, **fields).exists():
        return None
    row = Workstream.objects.create(name=name, description=description, **fields)
    audit.record(principal, actions.WORKSTREAM_CREATED, target_type="workstream",
                 target_key=row.pk, target_label=row.name)
    return row


def rename_workstream(principal, workstream, name: str) -> bool:
    """Give `workstream` a new `name` if `principal` may. True if it was
    written. A collision with another of this owner's streams is False,
    for `create_workstream`'s reason."""
    name = (name or "").strip()
    if not name or not may_manage_workstream(principal, workstream):
        return False
    clash = Workstream.objects.filter(
        name__iexact=name, owner_kind=workstream.owner_kind, owner_key=workstream.owner_key,
    ).exclude(pk=workstream.pk).exists()
    if clash:
        return False
    was = workstream.name
    workstream.name = name
    workstream.save(update_fields=["name", "updated_at"])
    audit.record(principal, actions.WORKSTREAM_RENAMED, target_type="workstream",
                 target_key=workstream.pk, target_label=name, was=was)
    return True


def set_workstream_description(principal, workstream, description: str) -> bool:
    """Not audited: a description is a label on the operator's own row,
    carrying no access consequence, and §16.3's rule is that the trail
    records what changed about ACCESS or about what the box holds."""
    if not may_manage_workstream(principal, workstream):
        return False
    workstream.description = description or ""
    workstream.save(update_fields=["description", "updated_at"])
    return True


def set_workstream_instructions(principal, workstream, instructions: str) -> bool:
    """AUDITED, unlike the description, because instructions reach the
    model: they are the operator's standing words for every turn in this
    stream (spec §11), and "what was this box told to do, and when" is
    exactly the question the trail exists to answer."""
    if not may_manage_workstream(principal, workstream):
        return False
    workstream.instructions = instructions or ""
    workstream.save(update_fields=["instructions", "updated_at"])
    audit.record(principal, actions.WORKSTREAM_INSTRUCTIONS_SET, target_type="workstream",
                 target_key=workstream.pk, target_label=workstream.name,
                 cleared=not workstream.instructions.strip())
    return True


def set_workstream_archived(principal, workstream, *, archived: bool) -> bool:
    """ONE FUNCTION FOR BOTH DIRECTIONS, keyed on a flag rather than two
    near-identical bodies -- `set_conversation_archived`'s own ruling,
    applied to the second archivable row. `timezone.now()` on the way in,
    `None` on the way out."""
    if not may_manage_workstream(principal, workstream):
        return False
    workstream.archived_at = timezone.now() if archived else None
    workstream.save(update_fields=["archived_at", "updated_at"])
    audit.record(principal,
                 actions.WORKSTREAM_ARCHIVED if archived else actions.WORKSTREAM_UNARCHIVED,
                 target_type="workstream", target_key=workstream.pk,
                 target_label=workstream.name)
    return True


def set_workstream_upload_default(principal, workstream, placement: str) -> bool:
    """Set (or clear, with `""`) the stream's upload default.

    `""` is "ask every time" (owner decision 5), and it is a legal value
    here rather than a separate clear function, for the same reason
    `set_conversation_archived` takes a flag.
    """
    if not may_manage_workstream(principal, workstream):
        return False
    if placement not in ("", *Workstream.UploadPlacement.values):
        return False
    workstream.default_upload_placement = placement
    workstream.save(update_fields=["default_upload_placement", "updated_at"])
    audit.record(principal, actions.WORKSTREAM_UPLOAD_DEFAULT_SET, target_type="workstream",
                 target_key=workstream.pk, target_label=workstream.name,
                 placement=placement or "ask")
    return True


def set_workstream_include_universal(principal, workstream, *, include_universal: bool) -> bool:
    """ROUND 17 (owner, verbatim: "a bool in settings to use all rag
    documents ... default it on"). AUDITED, like `set_workstream_
    upload_default`/`set_workstream_instructions` and unlike `set_
    workstream_description`: this column changes what the stream's own
    corpus admits at RETRIEVAL time (`tools.rag.workstreams.stream_
    documents`, `tools.rag.retrieval._visibility_filters`), an access
    consequence, not a label with none.
    """
    if not may_manage_workstream(principal, workstream):
        return False
    workstream.include_universal = include_universal
    workstream.save(update_fields=["include_universal", "updated_at"])
    audit.record(principal, actions.WORKSTREAM_INCLUDE_UNIVERSAL_SET,
                 target_type="workstream", target_key=workstream.pk,
                 target_label=workstream.name, include_universal=include_universal)
    return True


def set_workstream_scope(principal, workstream, entitlement_ids):
    """Make the stream's WALL exactly `entitlement_ids`. Returns
    `(added, removed)` as two frozensets, or `None` if `principal` may
    not, or if any submitted id is outside their own grants.

    A WALL MAY ONLY NAME ENTITLEMENTS THE SETTER HOLDS (spec §6.1) --
    not because holding one would grant anything (it would not; §6.1's
    outer intersection sees to that) but because a wall naming an
    entitlement its owner does not hold is a wall that narrows to the
    empty set and reads as a bug. `held_entitlement_ids`, NOT
    `owned_entitlement_ids`: the editor renders the held set (§15.3
    item 3), and the render half and the gate half must be the same set.

    WRITES THE DIFFERENCE, not the whole set, so the audit trail records
    what changed rather than what was resubmitted -- exactly as
    `tools.rag.labels.set_document_labels` does for its own table.
    """
    # KEPT DELIBERATELY, not dead defence: `agents/chat/views/
    # workstreams.py::workstream_scope` (the POST view) now gates
    # eagerly via `@stream_owner_required`, so this line is a genuine
    # SECOND read on that path -- but `workstream_new` calls this
    # function directly, with no decorator of its own, and this line is
    # its ONLY gate. A shared service function checks for its least-
    # guarded caller, not its best-guarded one.
    if not may_manage_workstream(principal, workstream):
        return None
    wanted = {int(i) for i in entitlement_ids}
    held = held_entitlement_ids(principal)
    if wanted - held:
        return None
    with transaction.atomic():
        current = set(workstream.scope_entitlements.values_list("entitlement_id", flat=True))
        for entitlement_id in sorted(wanted - current):
            WorkstreamScopeEntitlement.objects.create(
                workstream=workstream, entitlement_id=entitlement_id,
                set_by_id=int(principal.key) if principal.kind == "user" else None)
            audit.record(principal, actions.WORKSTREAM_SCOPE_ADDED, target_type="workstream",
                         target_key=workstream.pk, target_label=workstream.name,
                         entitlement=entitlement_id)
        for entitlement_id in sorted(current - wanted):
            workstream.scope_entitlements.filter(entitlement_id=entitlement_id).delete()
            audit.record(principal, actions.WORKSTREAM_SCOPE_REMOVED, target_type="workstream",
                         target_key=workstream.pk, target_label=workstream.name,
                         entitlement=entitlement_id)
    return frozenset(wanted - current), frozenset(current - wanted)


def _contained_document_count(workstream) -> int:
    """How many documents this stream contains.

    THROUGH `getattr`, DELIBERATELY, and this is the one place in this
    module that reaches for a relation another column declares. The
    reverse accessor is created by `tools.rag.models.Document.workstream`
    (a string FK to `"agents.Workstream"`), so it exists on a box with
    the rag app installed and is absent on one without -- and `agents/`
    may not import `tools/` to ask. `0` when it is absent is the honest
    answer: no rag app, no contained documents.
    """
    related = getattr(workstream, "documents", None)
    return related.count() if related is not None else 0


def delete_workstream(principal, workstream) -> str | None:
    """Delete `workstream`, or return a SENTENCE naming why not.

    COUNTS FIRST AND REFUSES BY NAME, which is the same count-then-name
    shape `identity.services.delete_entitlement` already uses for its
    cascade. Both containment foreign keys are `PROTECT` (author decision
    15): a stream delete that silently took seven documents with it would
    be the one destructive gesture on this surface, hidden behind the
    least alarming button.

    "DELETE THEM FIRST", NOT "delete or re-home them first", because
    re-homing is not an action this product has: owner decision 2 forbids
    moving a conversation, §21.2 forbids a document in two streams, and
    §14 offers no re-home route. A refusal that names an action the
    person cannot take is worse than one that names a chore.

    And every row the count names is a row the person reading it can act
    on -- ruling C makes the stream's owner able to read and manage every
    conversation in their stream, including ones a recipient started.

    The pins go with the stream (`CASCADE`) because a pin is pure
    association and its loss destroys nothing.

    `None` MEANS SUCCESS AND ONLY SUCCESS. A permission refusal returns a
    SENTENCE too, rather than the `None` an earlier draft used for both:
    one return value with two opposite meanings is exactly the ambiguity
    this column's own "a predicate stated in the view alone is a predicate
    the second caller does not get" argues against, and a CLI-facing
    caller has no 404 to fall back on. The view still runs its own
    `may_manage_workstream` pre-check and answers 404 before ever
    reaching here, so this sentence is what a shell caller sees.
    """
    if not may_manage_workstream(principal, workstream):
        return "You may not delete this workstream."
    conversations = workstream.conversations.count()
    documents = _contained_document_count(workstream)
    if conversations or documents:
        return (f"This workstream holds {conversations} conversation"
                f"{'' if conversations == 1 else 's'} and {documents} document"
                f"{'' if documents == 1 else 's'}. Delete them first.")
    name, pk = workstream.name, workstream.pk
    with transaction.atomic():
        # The stream's shares go with it, in the SAME transaction --
        # `delete_conversation`'s own ruling, and for the same reason: no
        # `post_delete` receiver, because this repository uses no Django
        # signals anywhere.
        Share.objects.filter(target_type=Share.Target.WORKSTREAM,
                             target_key=str(pk)).delete()
        workstream.delete()
    audit.record(principal, actions.WORKSTREAM_DELETED, target_type="workstream",
                 target_key=pk, target_label=name)
    return None


# The cap on how many entitlement names one message may disclose (spec
# §12.3, fence 3) -- a named constant for the reason
# `MAX_PINS_PER_STREAM`, `CONSOLIDATION_MAX_TURNS`,
# `WORKSTREAM_SIDEBAR_LIMIT` and `SIDEBAR_LIMIT` are: a bound a test
# asserts is a bound the code has to name.
NAME_CAP = 5


def workstream_taint_ids(workstream) -> frozenset[int]:
    """The stream's tag set.

    IT LIVES HERE, beside `share_workstream`, NOT in
    `agents/workstreams.py` -- and the direction has to be stated because
    the two agents-side stream modules meet in this task.
    `agents/workstreams.py` imports `agents/visibility.py`, NEVER THE
    REVERSE (spec §4.2). Putting this reader in `workstreams.py` would
    make `share_workstream` import it and close the cycle; `stream_access`
    reads it FROM here and the arrow stays one-way.
    """
    return frozenset(workstream.taint_tags.values_list("entitlement_id", flat=True))


def name_for_viewer(missing_ids, viewer_principal, *, disclose_all: bool = False):
    """Render `missing_ids` for whoever is READING this message.
    Returns `(named, unnamed_count)`.

    DEFAULT (`disclose_all=False`) -- NAMED: the ids this viewer holds;
    they can act on those and they already know the name. COUNTED:
    everything else, because an entitlement's NAME is not disclosed to
    somebody who neither owns nor holds it anywhere else on this platform
    (spec §12.3), and a refusal message is not the place to start.

    `disclose_all=True` -- every id NAMED, capped at `NAME_CAP` with the
    remainder counted. EXACTLY ONE CALLER: the dormant-share 403 page of
    spec §12.3, where owner decision 8 asks for the names in so many
    words and where the reader holds a live `Share` row on this stream.

    RULING E is why the switch exists. Applying the default at gate two
    would name NOTHING, ALWAYS: the viewer there IS the recipient, and
    `missing = taint_ids - held(recipient)` by construction, so
    `missing_ids & held(viewer)` is empty by definition and the page
    would read "and N entitlements you don't hold" -- the precise
    opposite of owner decision 8's own words.
    """
    if disclose_all:
        named = entitlement_names(missing_ids)
        return named[:NAME_CAP], max(0, len(named) - NAME_CAP)
    held = held_entitlement_ids(viewer_principal)
    named = entitlement_names(frozenset(missing_ids) & held)
    return named, len(frozenset(missing_ids) - held)


class ShareRefused(NamedTuple):
    """Gate one's refusal, as data the caller renders.

    UNLIKE `share_conversation`, WHICH RETURNS `None` FOR EVERY REFUSAL.
    That function's silence is right for its three refusals, which are
    all "you may not" or "that is not a valid request". This one has a
    fourth refusal that is neither: the recipient is missing
    entitlements, and naming the ones the SHARER THEMSELVES HOLDS is
    actionable rather than a leak.
    """

    missing_ids: frozenset
    missing_names: tuple
    unnamed_count: int
    message: str

    def __str__(self) -> str:
        return self.message


def share_workstream(principal, workstream, *, user=None, group=None, level):
    """Share `workstream`. A `Share` row on success, a `ShareRefused`
    naming the missing entitlements, or `None` when `principal` may not
    share at all (which the view answers 404 to).

    `level` MUST BE `use`, or the share is refused by name (author
    decision 16): owner decision 7 says a recipient reads AND converses,
    and storing a `view` level no reader honours would be a column value
    with two meanings.

    GATE ONE is two set operations. The refusal NAMES ONLY WHAT ITS
    VIEWER HOLDS and counts the rest -- ruling B, because the sharer does
    not necessarily hold every tag: a previous recipient's turn may have
    brought one in.
    """
    if not may_manage_workstream(principal, workstream):
        return None
    if bool(user) == bool(group):
        return None
    if level != Share.Level.USE:
        return ShareRefused(
            frozenset(), (), 0,
            "A workstream share lets somebody read and converse, so it is always at the "
            "'use' level. A view-only workstream share is not something this platform has.")
    missing = workstream_taint_ids(workstream) - entitlement_ids_for_subject(
        user=user, group=group)
    if missing:
        named, rest = name_for_viewer(missing, principal)
        return ShareRefused(missing, named, rest,
                            _refusal_sentence(named, rest))
    row, _ = Share.objects.update_or_create(
        target_type=Share.Target.WORKSTREAM, target_key=str(workstream.pk),
        user=user, group=group,
        defaults={"level": Share.Level.USE,
                  "shared_by_id": int(principal.key) if principal.kind == "user" else None},
    )
    audit.record(principal, actions.SHARE_ADDED, target_type="workstream",
                 target_key=workstream.pk, target_label=workstream.name,
                 subject="user" if user else "group",
                 subject_key=(user or group).pk, level=Share.Level.USE)
    return row


def _refusal_sentence(named, unnamed_count) -> str:
    """Gate one's exact copy, in the two shapes spec §12.1 gives.

    The last clause is REAL ADVICE: a single conversation inside the
    stream may carry fewer tags than the stream does, and
    `chat-conversation-share` already exists.
    """
    names = [n for _, n in named]
    if names and not unnamed_count:
        joined = " and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1
                              else names)
        return (f"Not shared. This workstream contains material from {joined}, and that "
                f"account holds "
                f"{'neither' if len(names) == 2 else 'none of them'}. Grant them, or "
                f"share a conversation instead.")
    if names:
        joined = ", ".join(names)
        return (f"Not shared. This workstream contains material from {joined}, and "
                f"{unnamed_count} more entitlement"
                f"{'' if unnamed_count == 1 else 's'} you don't hold, and that account "
                f"holds none of them. Grant what you can, or share a conversation instead.")
    return (f"Not shared. This workstream contains material from {unnamed_count} "
            f"entitlement{'' if unnamed_count == 1 else 's'} you don't hold, and that "
            f"account holds none of them. Ask an administrator, or share a conversation "
            f"instead.")


def revoke_workstream_share(principal, workstream, share_id):
    """Remove one `Share` row FROM THIS WORKSTREAM. A `RevokedShare`, or
    `None`. Same two refusals as `revoke_share`, via `_revoke_share_row`;
    a successful revoke here additionally writes the audit row `revoke_
    share` leaves to its own view, since a workstream revoke has no
    other caller that would write it."""
    if not may_manage_workstream(principal, workstream):
        return None
    revoked = _revoke_share_row(Share.Target.WORKSTREAM, workstream.pk, share_id)
    if revoked is None:
        return None
    audit.record(principal, actions.SHARE_REVOKED, target_type="workstream",
                 target_key=workstream.pk, target_label=workstream.name,
                 subject=revoked.subject, subject_key=revoked.subject_key,
                 level=revoked.level)
    return revoked


def share_list_for(workstream, viewer_principal):
    """The owner's share list, each row marked live or DORMANT with its
    reason.

    ONE SHARES QUERY (`shares_for`), plus one grants query per LISTED
    ROW (`entitlement_ids_for_subject`) rather than per grant -- this is
    a bound on the recipient count of one stream's own share list, not
    the per-row cost `sidebar_context` batches away (that one runs once
    per row of a 30-row conversation list on every chat page render;
    this one runs once per row of an owner's own share list, which is
    the number of people this ONE stream has been shared with -- a
    handful in practice, and a page only its owner visits). A DORMANT
    ROW PAYS MORE THAN THE ONE GRANTS QUERY: `name_for_viewer` runs only
    when `missing` is non-empty, and it is UN-THREADED -- its own
    `held_entitlement_ids(viewer_principal)` and the `entitlement_names`
    call after it are both fresh reads, paid once per dormant row and
    not at all for a live one. Dormancy is COMPUTED, never stored (spec
    §5.8): a stored flag is stale the moment a grant moves, and grants
    moving is the entire reason the read-time gate exists.

    THE MARKER TAKES THE DEFAULT NAMING MODE (§12.1's table): it names
    only what the OWNER holds and counts the rest, because under ruling B
    the owner may hold no grant for a tag on their own stream.
    """
    tags = workstream_taint_ids(workstream)
    out = []
    for row in shares_for(Share.Target.WORKSTREAM, str(workstream.pk)):
        subject_ids = entitlement_ids_for_subject(user=row.user, group=row.group)
        missing = tags - subject_ids
        named, rest = name_for_viewer(missing, viewer_principal) if missing else ((), 0)
        out.append({"share": row, "dormant": bool(missing),
                    "named": named, "unnamed_count": rest})
    return out


def dormant_recipient_workstream_ids(workstreams, principal, *, settings_row=None) -> frozenset[int]:
    """Which of `workstreams` -- an already-fetched iterable, typically
    the sidebar's own capped slice -- are DORMANT for `principal` right
    now: the sidebar's own batched twin of `stream_access`'s tag check
    (spec §15.1: "each row shows... a dormant marker when `stream_
    access` fails... computed in ONE BATCHED QUERY, not per row, the way
    `may_manage_conversation` was made flat by `settings_row` threading").

    OWNER OR `sees_all_content` ROWS ARE NEVER DORMANT -- `stream_
    access`'s own owner branch runs no tag check at all, and this
    function agrees rather than re-deriving the rule: `may_manage_
    workstream(principal, w, settings_row=settings_row)` decides it PER
    ROW AT ZERO QUERY COST once `settings_row` is threaded
    (`sees_all_content`'s own reuse) and `may_read_owned_row` is a pure
    comparison of two already-loaded columns -- no different from every
    other per-row predicate this module already threads that way.

    THREE QUERIES TOTAL, whatever N is, never N+1: one `identity_user`/
    `IdentitySettings` read the first per-row `may_manage_workstream`
    call pays and every later one reuses (`sees_all_content`'s own
    memoised-per-instance reuse, once `settings_row` is threaded), one
    `held_entitlement_ids` read, and one `WorkstreamTaint` read filtered
    `IN` every RECIPIENT row's id (owner rows are excluded from that `IN`
    before it is built, not filtered out after), grouped in Python. Its
    own pin (`agents/chat/tests/test_sidebar.py`) asserts exactly three.
    `workstream_taint_ids` -- this module's own PER-ROW reader -- is deliberately
    NOT called here; calling it once per row is exactly the N+1 this
    function exists to avoid, and its own docstring's single-workstream
    contract is why a second, batched reader exists rather than a loop
    around it.
    """
    rows = list(workstreams)
    recipient_ids = [w.pk for w in rows
                     if not may_manage_workstream(principal, w, settings_row=settings_row)]
    if not recipient_ids:
        return frozenset()
    held = held_entitlement_ids(principal, settings_row=settings_row)
    tags: dict[int, set[int]] = {}
    for workstream_id, entitlement_id in WorkstreamTaint.objects.filter(
            workstream_id__in=recipient_ids).values_list("workstream_id", "entitlement_id"):
        tags.setdefault(workstream_id, set()).add(entitlement_id)
    return frozenset(wid for wid, ents in tags.items() if ents - held)
