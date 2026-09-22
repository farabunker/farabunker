"""Every URL name on this box, and the coarse tier it answers to.

THREE TIERS, NOT SIX. The design's six route classes -- P public, A
authenticated, O owned content, L library content, R operational rows, S
superuser -- collapse to three at the MIDDLEWARE layer, because a
middleware runs before a view has resolved anything: it can answer "may
this principal be here at all", and it cannot answer "may they see THIS
row". So P is PUBLIC; A, O, L and R are all AUTHENTICATED at this layer;
S is ADMIN. `tier_for` derives that collapse from THIS SAME TABLE, so
the tier and the finer class can never disagree with each other -- there
used to be two tables (this one typing the tier as a comment, the route
matrix retyping the class as test data), reconciled by a pair of tests
whose only job was to stop the two from drifting. One table, and
`_TIER`'s three-entry map below, makes that reconciliation unnecessary
rather than merely tested.

O, L and R keep their real rule in the VIEW, through the visibility
functions; the class recorded here is what lets the route matrix
(`identity/tests/test_route_matrix.py`) assert that finer answer.

  A -- signed in, no row rule
  O -- owned CONTENT: 404 unless owned/shared, or `sees_all_content`
  L -- library CONTENT: 404 unless readable
  R -- operational ROWS: `is_admin` sees every row; content withheld
  S -- superuser

A NAME ABSENT FROM THIS TABLE IS TREATED AS ADMIN -- the strictest tier
-- and logged. Forgetting to classify a new route fails closed and
loudly rather than shipping it open, and `test_route_matrix.py` fails
immediately, naming the route, which is what keeps this table a live
artefact rather than a document that rots.
"""
from __future__ import annotations

PUBLIC = "public"
AUTHENTICATED = "authenticated"
ADMIN = "admin"
TIERS = (PUBLIC, AUTHENTICATED, ADMIN)

# THE ONLY TABLE. Every classified name's finer class -- "P"/"A"/"O"/
# "L"/"R"/"S" -- not a tier with the class demoted to a comment: the
# route matrix asserts ON this value, and a comment is not assertable.
ROUTE_RULES: dict[str, str] = {
    # --- / (foundation/landing/urls.py) --------------------------------
    # A, NOT P, and the difference is deliberate. Install guides
    # (`setup-index`) is public because it is the page a person needs
    # BEFORE they can log in to a box whose engines are not up, and it
    # names no row and no model choice. The landing page names WHICH
    # SURFACES THIS BOX HAS -- an inventory of
    # capability, which is operator information -- and it is not a page
    # anybody needs in order to sign in. So it behaves like every other
    # page: on an accounts-on box an anonymous visitor is redirected to
    # sign in; on an open box the gate returns before it ever looks here.
    "landing": "A",

    # --- /chat/ (agents/chat/urls.py) ----------------------------------
    "chat-index": "A",
    "chat-start": "A",
    "chat-default-install": "A",
    # ROUND 14, Part 2: the conversations browser -- this principal's
    # own list, no row addressed in the URL (the optional `?selected=`
    # is resolved through `visible_conversations` a second time inside
    # the view, never trusted bare), the identical shape `chat-index`/
    # `chat-workstreams` already carry.
    "chat-all": "A",
    "chat-conversation": "O",
    "chat-turn": "O",
    # CHAT CLUSTER, FEATURE C (edit a past prompt): row-addressed by
    # conversation + turn, POST-only, 404 unless
    # `agents.visibility.may_edit_turn` -- which is
    # `may_manage_conversation` plus two row facts, NOT the wider
    # `may_post_to` (spec review M2: a branch is a COPY, and a share
    # recipient minting a conversation they own that survives revocation
    # of the share is a different decision from letting them post). The
    # same "O" class `chat-conversation-duplicate` carries for the same
    # gate.
    "chat-turn-edit": "O",
    # ROUND 13 (message-bound attachments): row-addressed (conversation
    # + doc id), POST-only, gated in the view by `may_post_to` THEN
    # `agents.attachments.detach_attachment`'s own uploader-only check
    # -- the identical "O" class `chat-turn`/`chat-conversation-delete`
    # already carry for the same shape of route.
    "chat-attachment-detach": "O",
    "chat-conversation-delete": "O",
    # UI-3b, the sidebar's per-conversation menu. Row-addressed
    # mutations of owned content, gated in the view by the SAME
    # `agents.visibility.may_manage_conversation` predicate the delete
    # above uses -- a thread SHARED to somebody is readable by them and
    # not theirs to rename, copy, archive, pin, or delete, so every one
    # of them refuses a recipient with 404 alike.
    "chat-conversation-rename": "O",
    "chat-conversation-duplicate": "O",
    "chat-conversation-archive": "O",
    "chat-conversation-unarchive": "O",
    # ROUND 20: the SAME class, the SAME gate, archive/unarchive's own
    # two-routes-not-one-with-a-direction shape.
    "chat-conversation-pin": "O",
    "chat-conversation-unpin": "O",
    "chat-turn-status": "O",
    # Owner or `sees_all_content` only -- a recipient may not re-share.
    # The same route revokes, keyed on a `share_id` in the body, so
    # unsharing is not a second URL.
    "chat-conversation-share": "O",
    # ROUND 21: the chat column's settings-area page. S for the reason
    # `rag-settings` records for itself -- a page whose whole body is an
    # administrator-only form has nothing to render for anybody else, so
    # it refuses at the gate rather than serving an empty shell. GET and
    # POST share the route; both are administrator-only.
    "chat-settings": "S",
    # Labelling a tool is library-wide operator policy, and it lives at
    # `/chat/` because `/chat/` is the agents column's only mount.
    "chat-tool-entitlements": "S",
    # Labels agents and flows with entitlements (spec sections 6.11,
    # 9.6). ONE page for both -- see `agents/chat/views/access.py`.
    "chat-agent-entitlements": "S",

    # --- /chat/agents/ (chat cluster, feature B) -----------------------
    # A: a signed-in person's own list; the rows are narrowed in the
    # view by `editable_agents`, so there is no row to be addressed by.
    "chat-agents": "A",
    # A: creation needs no row.
    "chat-agent-new": "A",
    # O, NOT S, and the difference is the whole feature: an S route
    # would refuse a non-admin at the middleware, which is exactly the
    # person this page exists for. Row-addressed, 404 in the view unless
    # `agents.visibility.may_manage_agent` -- the same shape
    # `chat-conversation-rename` and its siblings carry.
    #
    # THE ONE O ROUTE WHOSE ADMIN ANSWER DOES NOT MOVE WITH THE CONTENT
    # TOGGLE. `may_manage_agent` short-circuits on `is_admin` alone --
    # an agent is box INVENTORY, the same call `labellable_agents`
    # records for `/chat/access/`, not a conversation or a document --
    # so an administrator is admitted here whether `admin_sees_content`
    # is on or off. `identity/tests/test_route_matrix.py`'s own
    # `_ADMIN_ALWAYS_ADMITTED_O` names it for that reason.
    "chat-agent-edit": "O",

    # --- /chat/w/ (new in Workstreams WS-1) ----------------------------
    # A, not O: the list is this principal's own streams plus the ones
    # shared to them, so there is no row to be addressed by.
    "chat-workstreams": "A",
    # A, the identical reason -- creating a stream needs an authenticated
    # principal and nothing else; there is no row until the POST succeeds
    # (owner feedback round 7: setup moved off the list page's own inline
    # create form onto this its own screen).
    "chat-workstream-new": "A",
    # O, and it is the ONE route on this platform that may answer 403 on
    # a row-addressed URL -- for a holder of a live `Share` row whose
    # grants no longer cover the stream's tags, and for nobody else
    # (WS-2, spec §12.3, fenced there in three ways). A stranger still
    # gets the house rule's 404.
    "chat-workstream": "O",
    # Owner-only settings split (chat layout fix): the PLAIN house 404
    # rule, not `chat-workstream`'s own 403 exception just above -- every
    # section this page hosts (Manage/Instructions/Scope/Upload default/
    # Tags/Shared with) is already owner-only or owner-relevant, so a
    # recipient (a live share holder included, not only a dormant one)
    # gets exactly the 404 a stranger gets.
    "chat-workstream-settings": "O",
    "chat-workstream-edit": "O",
    "chat-workstream-scope": "O",
    # Owner or `sees_all_content` only -- a recipient may not re-share.
    # The same route revokes, keyed on a `share_id` in the body, exactly
    # as `chat-conversation-share` does.
    "chat-workstream-share": "O",
    # Consolidation (WS-2, Task 18): OWNER-ONLY, including for a
    # recipient's own thread (ruling F, spec §23.F) -- a recipient always
    # gets 404, never the 403 `chat-workstream` itself may answer.
    "chat-workstream-consolidate": "O",

    # --- /rag/ (tools/rag/urls.py) -------------------------------------
    "rag-ask-page": "A",
    "rag-ask": "A",
    "rag-ask-status": "R",
    "rag-search": "A",
    "rag-documents": "R",
    "rag-document-upload": "A",
    "rag-document-file": "L",
    "rag-document-transcript": "L",
    "rag-document-delete": "R",
    "rag-document-reingest": "R",
    # ONE ACTION, NOT N (spec section 22.34): admin, or an owner of every
    # entitlement being added or removed, applied per document -- no row
    # in its own URL, so it stays on the base R mapping rather than
    # `_ROW_ADDRESSED_R`/`_LIBRARY_MUTATIONS` in the route matrix.
    "rag-document-labels-bulk": "R",
    # O, not L, and the row it is addressed by is the STREAM, not the
    # document (spec §14). Two resolutions, both 404-shaped.
    "rag-workstream-pin": "O",
    "rag-category-rename": "S",               # -- taxonomy is library-wide
    "rag-category-delete": "S",
    "rag-history": "A",                       # (rows are content: own only)
    # "Library" (UI-1 as "Library settings"; renamed in UI-2): the PAGE
    # that renders the library's seven settings forms, and the one
    # endpoint they post to. Both S -- a page whose entire body is
    # administrator-only forms has nothing to show anybody else, so it
    # refuses at the gate rather than rendering an empty shell.
    #
    # S2 (Coherence Wave C): ONE row, where there used to be seven --
    # `rag-history-settings` and its six siblings are retired along with
    # their paths. The class did not change with the count: every one of
    # them was S for the same reason this one is, including the hybrid
    # toggle that rebuilds the chunk table.
    "rag-settings": "S",
    "rag-settings-update": "S",               # -- operator policy

    # --- /vision/ (tools/vision/urls.py; mounted only with the flag) ---
    "vision-create": "A",
    "vision-create-operation": "A",           # -- the rendering alias
    "vision-gallery": "A",                    # (lists own; content)
    "vision-jobs-strip": "A",                 # (lists own; content)
    "vision-operations": "A",                 # -- schema, reveals no rows
    "vision-generate": "A",
    "vision-job-status": "O",
    "vision-job-delete": "O",
    # O, not R: a generated image is CONTENT (`tools/vision/visibility.py`'s
    # own module docstring) -- the row rule is `sees_all_content`, never
    # `is_admin`, so an administrator with the content setting OFF must
    # not bulk-delete somebody else's generations either, which is
    # exactly what `visible_jobs(principal)` already refuses. No row in
    # the URL, so no per-row refusal to assert; the row rule stays
    # `visible_jobs(principal)`, enforced by dropping -- the gallery's
    # select-mode bulk delete never 404s for anybody: an id outside it
    # (foreign, malformed, or simply gone) is silently DROPPED from the
    # batch rather than refusing the whole submit, the same
    # "PER-DOCUMENT PERMISSION, not all-or-nothing" SHAPE
    # `rag-document-labels-bulk` carries -- that route stays "R" because
    # its page is an admin surface (`rag-documents`); this one is owned
    # content. `identity/tests/test_route_matrix.py`'s
    # `TestVisionJobsDeleteSelectedFilters` pins the four cells this
    # implies directly against the database.
    "vision-jobs-delete-selected": "O",
    "vision-output-file": "O",                # -- through output.job
    "vision-input-file": "O",                 # -- through input.job
    "vision-queue-status": "R",

    # Engine files (2026-09-02): box inventory (the engine's own
    # output/input folders), not anybody's content -- S, like the other
    # admin-only surfaces below, not O/L. `is_admin` gets an
    # administrator to all three; `sees_all_content` is a SECOND,
    # narrower gate the thumbnail route applies itself, on top, only for
    # pixels (`tools/vision/maintenance.py`'s own module docstring) --
    # recorded here as a comment, not a class, because this table has no
    # finer class than S to express it in.
    "vision-engine-files": "S",
    "vision-engine-file-thumbnail": "S",
    "vision-engine-files-delete": "S",

    # --- /inference/ (models/registry/urls.py) -------------------------
    # The console READ is S as well as the mutations: it displays
    # endpoints, model identifiers and connection configuration -- an
    # inventory of the box, which is operator information. This closes
    # ADR 0010's standing unauthenticated-mutation gap in full rather
    # than half.
    "inference-console": "S",
    "inference-connection-add": "S",
    "inference-connection-remove": "S",
    "inference-machine-add": "S",
    "inference-role-assign": "S",
    "inference-role-reencode": "S",
    "inference-server-scan": "S",
    # A model connection is box inventory, not somebody's library row, so
    # an entitlement owner has no standing over it -- S, not R.
    "inference-connection-sets": "S",
    "inference-model-sets": "S",
    "inference-model-set-edit": "S",

    # --- /queue/ (models/queue/urls.py) --------------------------------
    "jobs-queue": "R",
    # F1 (Coherence Wave C): "Job execution", the registered settings
    # page the four `JobSettings` forms moved to. S, like the endpoint
    # they post to and like every other settings page -- a page whose
    # entire body is administrator-only forms has nothing to show
    # anybody else, so it refuses at the gate rather than rendering an
    # empty shell (the same call `rag-settings` records above).
    "jobs-settings": "S",
    "jobs-settings-update": "S",                # -- operator policy
    "jobs-queue-cancel": "R",

    # --- /setup/ (foundation/setup/urls.py) ----------------------------
    # PUBLIC, deliberately: the install guidance itself -- which engines
    # exist, how to install one, the register-then-bind flow -- names no
    # row, no model choice and no document, and it is the page a person
    # needs BEFORE they can log in to a box whose engines are not up. The
    # BOX'S INVENTORY the same view can show -- each engine's configured
    # endpoint, whether it answers, and the "What each feature needs"
    # bindings table -- is gated inside the view on `is_admin` instead
    # (`foundation/setup/views.py::_engine_views`/`_role_views`), so the
    # route stays PUBLIC without the page disclosing operator information
    # to an anonymous caller.
    "setup-index": "P",

    # --- /settings/ (foundation/settings_area.py) ----------------------
    # P, and for the same reason `setup-index` is. The route renders no
    # page at all: it reads the posture row the gate middleware already
    # fetched, picks the first settings section this viewer may open, and
    # redirects there. Every target keeps its OWN class, so an anonymous
    # visitor who follows it lands on `setup-index` (P) and a member who
    # tries a deeper one is still refused by the gate. Classifying it A
    # instead would bounce an anonymous visitor to sign in before a
    # redirect that was only ever going to send them to the public
    # install guidance -- the one page a person needs BEFORE they can
    # sign in to a box whose engines are not up.
    "settings-index": "P",

    # --- /settings/assistant/ (settings assistant) ----------------------
    # S, for `chat-settings`'s own recorded reason: a surface whose whole
    # body is an administrator-only panel has nothing to show anybody
    # else, so it refuses at the gate rather than serving an empty shell.
    # `is_admin` is True for everybody on an open box, which is what makes
    # a household box's panel work for whoever is at the keyboard.
    "settings-assistant-ask": "S",
    "settings-assistant-reset": "S",
    "settings-assistant-panel": "S",

    # --- /settings/agents/ (the agent library, chat cluster feature B) --
    # S, the same call `chat-settings` and `chat-tool-entitlements`
    # already record: a page whose WHOLE body is an administrator-only
    # listing has nothing to show anybody else, so it refuses at the gate
    # rather than serving an empty shell. The view carries its own
    # `is_admin` check as well, for the reason `settings-assistant-*`
    # above does: the gate is not the only way a view function can be
    # reached.
    #
    # NOT O, unlike `chat-agent-edit` above, and the two are not in
    # tension: the EDIT route is class O precisely so a non-admin owner
    # can open their own row, and this LISTING is the box-wide view of
    # every row, which only an administrator has any standing over.
    "settings-agents": "S",

    # --- /identity/ (new in IA-1) --------------------------------------
    "identity-login": "P",
    "identity-logout": "A",
    "identity-password-change": "A",
    "identity-password-change-done": "A",
    "identity-users": "S",
    "identity-user-create": "S",
    "identity-user-edit": "S",
    "identity-settings": "S",

    # --- /identity/ (new in IA-2) --------------------------------------
    "identity-groups": "S",
    "identity-group-edit": "S",
    "identity-entitlements": "S",
    # R, NOT S, and the difference is load-bearing. `tier_for` maps S to
    # the ADMIN tier, and the middleware refuses a non-admin there before
    # the view resolves anything -- so an ENTITLEMENT OWNER, who spec
    # section 11.3 says may reach this entitlement's grant form, could
    # never get in. R is AUTHENTICATED at the middleware and keeps its
    # real rule in the view (`identity/views.py::entitlement_edit`), which
    # is the definition of the R class: admitted, then answered 404 for a
    # row this principal has no standing over. Rename and delete re-check
    # `is_admin` inside `identity.services`.
    "identity-entitlement-edit": "R",
}

# The only two classes that answer to something other than AUTHENTICATED
# at the middleware layer -- A/O/L/R all fall through to `tier_for`'s
# default, because the middleware cannot tell them apart and does not
# need to.
_TIER: dict[str, str] = {"P": PUBLIC, "S": ADMIN}


def tier_for(url_name: str, app_names: list[str]) -> str:
    """The tier `url_name` answers to.

    `/admin/` is classified WHOLESALE by its namespace rather than name
    by name: Django registers its own tree, this platform does not own
    those names, and a per-name table would rot the first time Django
    added one. It is also how `/admin/` becomes SUPERUSER-only rather
    than Django's default staff-only, with no `AdminSite` subclass.

    An unknown name is ADMIN. Fail closed, and the caller logs it.
    """
    if app_names and "admin" in app_names:
        return ADMIN
    klass = ROUTE_RULES.get(url_name)
    if klass is None:
        return ADMIN
    return _TIER.get(klass, AUTHENTICATED)
