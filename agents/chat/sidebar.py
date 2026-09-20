"""The chat rail's context, built once for every chat page (UI-3b,
round 14's own app-rail rewrite).

WHY ITS OWN MODULE. `chat/index.html` and `chat/conversation.html` both
render the same rail, and their two context builders live in two view
modules whose import direction is one-way (`views/__init__.py`:
`turns.py` -> `thread.py` -> nothing; `conversations.py` -> `service.py`).
Putting the builder in either one would mean the other importing back up
that chain. So it lives beside `service.py` -- a module both view modules
may import and which imports neither -- for exactly the reason the
poller's three constants do.

IT READS ROWS THROUGH `agents/visibility.py`, like every other module
under `agents/chat` (ruling 4c): `visible_conversations` is THE ONE list
gate, and the archived filter below is a `.filter()` on top of it, never
a second query against `Conversation.objects`.

NOTHING THIS CONTEXT FEEDS NEEDS JAVASCRIPT. The per-conversation menus
are native `<details>` elements and the actions inside them are plain
POST forms -- the same idiom the delete confirm and the setup page's
platform disclosures already use -- so every row this builder produces
works with no script running. `chat/_sidebar.html` does include one
progressive-enhancement script of its own (`chat/_menu_exclusive.html`,
round 21, one menu open at a time); it reads nothing from this context
and is documented in that fragment, not here.

ROUND 14 RETIRES THE STREAM-SCOPED VARIANT (owner: "when I cycle through
the chats and i click on a project, it takes me to that workstreams
chat... I believe we should remove the workstream chats from the side
bar"). Through round 13 this function took an OPTIONAL `workstream=`
keyword that narrowed the CHATS section to that one stream's own
conversations, called by the workstream page and its settings page
(`agents/chat/views/workstreams.py`). That keyword is GONE: every chat
page now renders the SAME global CHATS list (stream conversations
included, each carrying its own small workstream chip -- Part 1's own
`row.conversation.workstream`, free off the `select_related` this
function already pays for the stream-owner branch below), and the
stream's own conversation list lives on the stream page itself, as it
already did. This is what the brief calls "the sidebar stops context-
switching": clicking a conversation, workstream-scoped or not, no
longer changes what the rail itself shows.
"""
from __future__ import annotations

from agents.visibility import (
    chat_surface_conversations, dormant_recipient_workstream_ids, may_manage_conversation,
    visible_workstreams,
)

# How many threads the rail's CHATS section lists. A cap, not a
# paginator, for the reason the index's own cap was one: this is a
# single-operator box and the rail is a nav, not an archive browser.
# What is new is that the cap is HONEST ON SCREEN -- when it bites, the
# rail says how many older conversations it is not showing rather than
# silently ending. Round 14's own "All chats" link (Part 1) is the real
# see-more now -- `/chat/all/`, a searchable table, not merely a longer
# capped list -- so this constant stays a NAV cap, never grown into a
# paginator itself.
SIDEBAR_LIMIT = 30

# How many workstreams the rail's WORKSTREAMS section lists. A cap, not
# a paginator, for the reason `SIDEBAR_LIMIT` is one -- and honest on
# screen when it bites, with the same "…N more" line the conversation
# cap already renders.
#
# 5, NOT 10 (owner feedback, chat layout fix round 2: "show the 5 most
# recent ones and the ability to see more or collapse"). Lowered
# alongside the sidebar going from two side-by-side columns to one
# single stacked column with the workstreams section now a collapsible
# `<details>` above the conversation list (`chat/_sidebar.html`) --
# fewer rows visible by default is the other half of "this takes up too
# much space", not just the accordion. `sidebar_workstreams_older_count`
# below is unchanged: it already renders whatever the gap between this
# constant and the real total is, honestly, however that number moves.
WORKSTREAM_SIDEBAR_LIMIT = 5


def sidebar_context(principal, *, current=None, archived: bool = False,
                    settings_row=None) -> dict:
    """Everything `chat/_sidebar.html` (the rail's own CHATS/WORKSTREAMS
    sections) needs.

    `current` is the conversation being read, or `None` on the index --
    it is the row the rail marks `.current`, the same treatment the
    settings sidebar gives the section you are on.

    `archived` picks WHICH list this is: the active one (the default) or
    the archive. ONE PAGE serves both -- `/chat/?archived=1` -- so there
    is no second template, no second route to classify, and no second
    copy of this shape to keep in step. ROUND 14: `/chat/all/` (Part 2)
    absorbs the ARCHIVE LISTING itself (`/chat/?archived=1` now
    redirects there) -- but `sidebar_context`'s own `archived` keyword
    stays, unchanged, for the rail: a rail rendered while the operator
    is looking at the archive still needs to say so and still needs its
    own "back to active" foot link, and the redirect only ever touches
    the LIST page, never this function's own callers.

    THE MANAGE FLAG IS COMPUTED PER ROW, NOT ASSUMED. A conversation
    somebody SHARED with this principal is in the list (they may read
    it) and is not theirs to rename, duplicate, archive or delete
    (`may_manage_conversation`) -- so its row renders no menu at all,
    rather than a menu whose every entry 404s. That is the render-vs-gate
    rule this surface already applies to the compose form.

    `settings_row` IS WHAT KEEPS THAT PER-ROW CALL FREE, and an earlier
    version of this docstring was wrong about it. `may_read_owned_row`
    really is a pure comparison of two already-loaded columns, and
    `sees_all_content` really does short-circuit on an open box -- but
    on a box WITH accounts (the postures that have sharing and more than
    one person, i.e. the ones where the per-row question has an answer
    worth asking) it reads `IdentitySettings` first, and
    `identity.access._user_row` memoises on that row's INSTANCE, so a
    fresh read per row defeats the `identity_user` cache as well: two
    extra queries per listed conversation, on the two most-trafficked
    pages in the app. Both callers now hand over
    `identity.request.settings_row_for(request)` -- the row the gate
    middleware already stashed for this request -- and the rail is
    flat in the number of rows. `test_sidebar.py` pins that with a
    query count at 1 row and at 25.

    THE SAME `settings_row` IS ALSO THREADED INTO EVERY BUILDER CALL
    THIS FUNCTION MAKES -- both `visible_conversations` calls (the
    active/archived list and the archived count) and `visible_
    workstreams` -- not just the per-row `may_manage_conversation` call
    above. Each of those functions reads `IdentitySettings` once via
    `sees_all_content`; asking three of them in one render without
    threading would mean three fresh singleton reads (six queries: the
    settings row plus the memoised `_user_row` lookup, each on its own
    unshared instance) where one suffices. This is the identical N+1
    lesson this docstring already tells for the per-row case, applied to
    this function's OWN calls into `agents/visibility.py` rather than to
    its callers' per-row calls. What remains un-threaded, deliberately,
    is `owned_rows_q`'s internal `is_admin(principal)` call inside both
    `visible_conversations` and `visible_workstreams` -- that function is
    shared with `tools/vision/visibility.py` and `tools/rag/access.py`,
    so widening it is a cross-column change and its own question, the
    same bounded edge `may_manage_conversation`'s own docstring already
    names for the identical function.

    `select_related("workstream")` IS NOT AN OPTIMISATION. It is the
    condition of TWO things being affordable at once: `may_manage_
    conversation` gains a stream-owner branch that reads `conversation.
    workstream.owner_kind`/`owner_key`, and round 14's own per-row
    workstream CHIP (`row.conversation.workstream.name`, `chat/
    _sidebar.html`) reads the same joined row a second time for a
    second reason -- this function calls both once per listed row.
    Without the join either one would be a second per-row read, on the
    two most-trafficked pages in the app; the review that measured the
    manage-flag cost (+2 queries per conversation, 47 -> 95 at 25 rows)
    is the same regression a per-row workstream fetch would have
    reintroduced here.
    """
    # CONSOLIDATION WAVE (audit A, F1): `visible_conversations` does its
    # identity reads (`owned_rows_q`'s own `is_admin()`, `shared_keys`
    # twice) AT CALL TIME, not at queryset evaluation -- calling it a
    # second time below for the archived count repeated all four. Built
    # ONCE here and `.filter()`d several times instead: a queryset's
    # `.filter()` clones it without re-running the principal-identity
    # work the base call already paid for, so every other use below is
    # free.
    base = chat_surface_conversations(principal, settings_row=settings_row)
    rows = base.select_related("workstream").filter(archived_at__isnull=not archived)
    # ROUND 20 (owner: "pin chats so it's always shown in its own
    # se[c]tion... it should only appear if we have somethign that is
    # pinned"). PINNED CONVERSATIONS ARE EXCLUDED FROM THE ACTIVE CHATS
    # LIST -- they render in their own section instead (below), never
    # both, so the SAME conversation is never listed twice on one
    # render. Scoped to the ACTIVE view only (`not archived`): the
    # ARCHIVE listing is untouched by pin state entirely -- a
    # conversation appears there purely on `archived_at`, exactly as
    # before this round, and ARCHIVING WINS over pinning (a pinned
    # conversation that gets archived disappears from the PINNED
    # section too, below, the same way it disappears from Chats --
    # `pinned_at` is left standing, unread while archived, so unarchiving
    # restores the pin exactly where archiving found it).
    if not archived:
        rows = rows.filter(pinned_at__isnull=True)
    # `.count()` BEFORE the slice, so the "N older" line can be an
    # honest number rather than "there might be more". Two small queries
    # on an indexed column, once per page render.
    total = rows.count()
    listed = list(rows[:SIDEBAR_LIMIT])
    # THE PINNED SECTION'S OWN ROWS (round 20). One more `.filter()` on
    # the SAME free-to-clone `base` -- ONE bounded extra query for the
    # whole render (never per row), the same accounting the archived
    # count below already uses. `archived_at__isnull=True` is what makes
    # archiving win: an archived-and-still-pinned row never appears
    # here. NO CAP/"…N more" LINE: unlike Chats and Workstreams, a pin
    # is the operator's own deliberate, small curated set -- there is no
    # "recent" ordering pressure a cap would be protecting against, so
    # this stays uncapped until an operator actually asks for one.
    # Ordered by `pinned_at` DESCENDING (most recently pinned first) --
    # simple, and the one ordering the brief asks for.
    pinned = list(
        base.select_related("workstream")
        .filter(archived_at__isnull=True, pinned_at__isnull=False)
        .order_by("-pinned_at")
    )
    streams = visible_workstreams(principal, settings_row=settings_row) \
        .filter(archived_at__isnull=True)
    stream_total = streams.count()
    listed_streams = list(streams[:WORKSTREAM_SIDEBAR_LIMIT])
    return {
        "sidebar_archived": archived,
        "sidebar_workstreams": listed_streams,
        "sidebar_workstreams_older_count": max(0, stream_total - WORKSTREAM_SIDEBAR_LIMIT),
        # DORMANT MARKERS, BATCHED (spec §15.1, Task 17 review round 1):
        # a recipient's row gets a marker when `stream_access` would
        # fail right now. `dormant_recipient_workstream_ids` costs THREE
        # queries total for this whole capped slice -- never one per
        # row -- and answers `frozenset()` with none of them for an
        # owner-only list, at no query cost at all (its own `not
        # recipient_ids` early return).
        "sidebar_dormant_workstream_ids": dormant_recipient_workstream_ids(
            listed_streams, principal, settings_row=settings_row),
        # ROUND 20: rendered strictly BETWEEN Workstreams and Chats
        # (`chat/_sidebar.html`'s own template order), and ONLY when
        # non-empty -- the template's own `{% if sidebar_pinned_rows %}`
        # is what makes the section vanish outright rather than render
        # as an empty shell. Same row shape as `sidebar_rows`, below,
        # reused by the SAME row include.
        "sidebar_pinned_rows": [
            {"conversation": row,
             "may_manage": may_manage_conversation(principal, row,
                                                   settings_row=settings_row),
             "current": current is not None and row.pk == current.pk}
            for row in pinned
        ],
        "sidebar_rows": [
            {"conversation": row,
             "may_manage": may_manage_conversation(principal, row,
                                                   settings_row=settings_row),
             "current": current is not None and row.pk == current.pk}
            for row in listed
        ],
        "sidebar_older_count": max(0, total - SIDEBAR_LIMIT),
        # Rendered as the "Archived (N)" link at the foot of the ACTIVE
        # rail, and only when N > 0: a link to an empty page is a link
        # to a disappointment. Not computed at all on the archive
        # itself, which links back to the active list instead.
        "sidebar_archived_count": (
            0 if archived
            else base.filter(archived_at__isnull=False).count()
        ),
    }
