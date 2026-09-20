"""The access questions identity can answer alone.

A NAMED SEAM (import-law rule 2, as amended by spec section 4.2): every
column may import this module. `identity.models`, `identity.views`,
`identity.services`, `identity.forms` and `identity.middleware` are
off-limits to every other column with no exception -- the same shape
`models.registry.models`/`.views` already have, and enforced by the same
gate.

IDENTITY CANNOT ANSWER "WHICH DOCUMENTS". It may not import `tools/` or
`agents/` (rule 4), so it answers questions about PRINCIPALS and lets
each column turn those answers into a queryset of its own rows. That is
what makes this column a base rather than a hub.
"""
from __future__ import annotations

from django.db.models import Q

from identity.contracts.postures import LIBRARY_OPEN, POSTURE_OPEN
from identity.models import Entitlement, EntitlementGrant, IdentitySettings, User


def posture() -> str:
    """This box's posture, read from the one row that answers it.

    One primary-key read per request, on a connection Django already
    holds open (`CONN_MAX_AGE=600`). No cache: a cache would be a second
    truth with a staleness window, and the staleness window of a
    security posture is exactly the interval in which the box is wrong.
    """
    return IdentitySettings.get_solo().posture


def settings_row():
    """The `IdentitySettings` singleton, fetched ONCE, for a caller
    outside `identity/` that needs more than one answer off it in a
    single call and has no HTTP request to reuse
    (`identity.request.settings_row_for(request)` is the request-scoped
    version of this).

    `agents.entitlements.tool_access_for` is the first caller: without
    this, threading `settings_row=` into both `sees_all_content` and
    `held_entitlement_ids` would still mean fetching the row here, and
    `agents/` may not import `identity.models.IdentitySettings` itself
    (import-law rule 4) -- so the fetch has to happen on THIS side of
    the seam and travel down as plain data, exactly as every other
    `settings_row=` keyword argument in this module already does.
    """
    return IdentitySettings.get_solo()


def accounts_on(*, settings_row=None) -> bool:
    """Whether this box requires a signed-in caller.

    EVERY function below and every visibility function in every column
    tests this FIRST and returns its open branch before touching another
    table. That is how "an open box never runs a permission query" is
    made structural rather than promised.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only, for a caller that reads the singleton once and
    needs more than one answer off it (`IdentityGateMiddleware`, which
    also needs `is_admin` and `settings_row.session_idle_minutes` from
    the same read). Every other caller leaves it out, and this fetches
    the singleton itself.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    return row.posture != POSTURE_OPEN


def _user_row(settings_row, principal):
    """The active `User` row `principal` names, or None.

    The `isdecimal()` check is not defensive noise: `Principal.key` for a
    user is the primary key AS A STRING, and a key that is not a decimal
    string -- from a hand-written payload, or a row written against an
    older schema -- would reach `filter(pk=...)` and raise `ValueError`
    inside a request. A never-500 surface cannot afford that, and
    "answer no" is the correct reading of an unparseable identity.

    MEMOISED ON `settings_row`, keyed by `principal`: a caller that
    threads the SAME already-fetched `IdentitySettings` instance through
    several `is_admin(..., settings_row=row)`/`sees_all_content(...,
    settings_row=row)` calls in one request (`models.queue.views.
    QueueView` asks once per snapshot list and once per row via
    `visible_rows`/`may_read_job_content`) gets the `auth_user` read
    ONCE per request, not once per call -- the same per-request reuse
    the settings row itself already gets from `IdentityGateMiddleware`.
    A settings_row fetched fresh for a single call (`is_admin()`/
    `sees_all_content()`'s own no-argument forms, and every caller that
    has not opted into row-threading) pays for an unused one-entry cache
    dict and nothing else -- there is no second call on that instance to
    benefit from it.
    """
    if getattr(principal, "kind", None) != "user":
        return None
    key = principal.key
    if not isinstance(key, str) or not key.isdecimal():
        return None
    cache = getattr(settings_row, "_user_row_cache", None)
    if cache is None:
        cache = {}
        settings_row._user_row_cache = cache
    if principal not in cache:
        cache[principal] = User.objects.filter(pk=int(key), is_active=True).first()
    return cache[principal]


def is_admin(principal, *, settings_row=None) -> bool:
    """May this principal reach an ADMIN SURFACE -- the model console,
    the queue settings, the users page, the posture page, `/admin/`.

    True for `OPEN_PRINCIPAL` and for an ACTIVE SUPERUSER. False for
    `ANONYMOUS` and for every service principal.

    `OPEN_PRINCIPAL` is an admin because on a box with no accounts there
    is nobody for anything to be hidden from, and answering False would
    make every admin surface unreachable in the posture that is the
    default.

    A SERVICE principal is never an admin, whatever it was issued: a
    machine caller that could reach the model console or the posture
    page would make the shell a privilege-escalation path with no login
    behind it.

    THIS IS NOT `sees_all_content`. Administering is what this answers;
    reading is what that one answers. An administrator cancels any job,
    labels any document and deletes any document with THIS predicate
    alone.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only -- see `accounts_on` for why.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    if not accounts_on(settings_row=row):
        return True
    user = _user_row(row, principal)
    return bool(user and user.is_superuser)


def sees_all_content(principal, *, settings_row=None) -> bool:
    """Whether this principal may read OTHER PEOPLE'S CONTENT -- their
    conversations, Ask history, generated images, agent and flow bodies,
    document bytes, and the payload/answer text of jobs they did not
    start.

    OPEN -> True. There is nobody for anything to be hidden from.
    OTHERWISE -> `is_admin(principal) and admin_sees_content`.

    NO POSTURE BRANCH, deliberately. `personal` and `enterprise` answer
    this identically; they differ only in which pages exist. A predicate
    that read the posture would mean the same administrator saw
    different content on two boxes that had made the same choice -- and
    the choice is the SETTING, not the posture.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only -- see `accounts_on` for why. Threading it through
    to `accounts_on`/`is_admin` below is what keeps this ONE
    `IdentitySettings.get_solo()` READ rather than three: a caller
    evaluating this once PER ROW of a list (`models.queue.views.
    QueueView`, which asks `models.queue.visibility.may_read_job_content`
    once per queue row) must not re-read the singleton once per row --
    it reads ONCE per request instead, through `IdentityGateMiddleware`'s
    own `request.identity_settings_row`, and threads it through here.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    if not accounts_on(settings_row=row):
        return True
    if not is_admin(principal, settings_row=row):
        return False
    return row.admin_sees_content


def owned_rows_q(principal, *, settings_row=None) -> Q:
    """The `Q` an owned-rows queryset filters on -- the mirror of
    `owner_fields`: identity defines how ownership is STAMPED
    (`owner_fields`) and how it is READ BACK (this), so no column
    outside this module restates the shape of the two owner columns.
    The caller still names the model and calls `.filter()`; this names
    neither, so identity still answers nothing about WHICH rows.

    OWN ROWS, plus rows a SHELL PATH created (`owner_kind="service"`)
    when `principal` is an admin -- full statement and rationale in
    `identity/README.md` §5b. `is_admin`, not `sees_all_content`: this is
    the one place the administer/read split does not apply (nobody's
    privacy is at stake in a row a command produced), so an operator
    sees what a command made whatever the content setting says.

    AN EMPTY `Q()` IS SAFE IN AN `OR`, worth stating because the naive
    reading is the dangerous one: for a non-admin this is exactly the
    own-rows clause, never wider. Django's `Q._combine` returns a copy of
    the non-empty operand when the other is empty.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only, threaded straight into `is_admin` -- the identical
    fallback every other `settings_row=` keyword in this module already
    has. Most callers still leave it out. The settings assistant panel is
    what threads it, from both `agents.visibility.visible_agents` (Task 8
    review, M1) and `agents.visibility.visible_conversations` (Task 8
    review, R2) -- on an administrator's open panel these were two of the
    legs re-reading the singleton (and, via `_user_row`'s memoisation on
    the row instance, re-reading `auth_user` too) that identity's
    middleware pin `identity/tests/test_middleware.py::
    TestTheSingleRowRead` already holds every other view to.
    """
    own = Q(owner_kind=principal.kind, owner_key=principal.key)
    service = Q(owner_kind="service") if is_admin(principal, settings_row=settings_row) else Q()
    return own | service


def may_read_owned_row(principal, row) -> bool:
    """The ROW-PREDICATE mirror of `owned_rows_q`, for a caller checking
    ONE ALREADY-LOADED `row` (any object with `.owner_kind`/`.owner_key`)
    instead of filtering a queryset -- `agents.visibility.may_manage_conversation`
    and `tools.vision.visibility.may_read_job` both used to write this
    same three-branch check by hand over a different model.

    Answers the SAME TWO BRANCHES `owned_rows_q` does -- own row, or a
    service-made row for an admin -- and deliberately NOT the third:
    `sees_all_content` is the caller's own early return, exactly as it
    is the caller's own `.filter()` around `owned_rows_q`. Full
    statement and rationale for the service branch in
    `identity/README.md` §5b.
    """
    if row.owner_kind == "service":
        return is_admin(principal)
    return row.owner_kind == principal.kind and row.owner_key == principal.key


def owner_fields(principal) -> dict:
    """The two columns to stamp on a row this principal is creating.

    MOVED here from `agents/visibility.py` so all five owned tables in
    three columns share ONE definition rather than three that agree by
    convention. A dict rather than two arguments, so a call site cannot
    pass one and forget the other.

    No database read at all: ownership is stamped from the principal, and
    the principal is already resolved.
    """
    return {"owner_kind": principal.kind, "owner_key": principal.key}


def _user_pk(principal) -> int | None:
    """`principal`'s primary key as an int, or None.

    THE SAME `isdecimal()` GUARD `_user_row` above applies, for the same
    reason and against a different query: `Principal.key` for a user is
    the primary key AS A STRING, and a key that is not a decimal string
    would reach `filter(user_id=...)` and raise `ValueError` inside a
    request. "Answer nothing" is the correct reading of an unparseable
    identity on a never-500 surface.
    """
    if getattr(principal, "kind", None) != "user":
        return None
    key = principal.key
    if not isinstance(key, str) or not key.isdecimal():
        return None
    return int(key)


def _grant_ids(principal, *, role: str | None = None) -> frozenset[int]:
    """ONE QUERY: every entitlement id `principal` holds, directly or
    through one of their groups, optionally narrowed to a role.

    `group__user` is the reverse of `PermissionsMixin.groups`, whose
    `related_query_name` is `user` -- so this reaches a group grant
    without a second query and without the platform ever materialising a
    membership list.
    """
    pk = _user_pk(principal)
    if pk is None:
        return frozenset()
    rows = EntitlementGrant.objects.filter(Q(user_id=pk) | Q(group__user__id=pk))
    if role is not None:
        rows = rows.filter(role=role)
    return frozenset(rows.values_list("entitlement_id", flat=True))


def entitlement_ids_for_subject(*, user=None, group=None) -> frozenset[int]:
    """Every entitlement id a SUBJECT holds -- a `User` row or a `Group`
    row, not a `Principal`.

    `held_entitlement_ids` takes a `Principal` and answers for the
    CALLER; the workstream share gate's subject is the RECIPIENT, who is
    a row the sharer picked off a `<select>` (spec §12.1). Same join
    `_grant_ids` already runs, keyed differently, on a seam every column
    may already import -- a new module would be a fifth sanctioned
    identity import for one function.

    A GROUP IS CHECKED AGAINST ITS OWN GRANTS, not its members'
    (author decision 23): a group is the subject of a grant in this
    codebase (`grant_user_xor_group`), so "does this group hold E" is a
    row rather than a computation over membership -- and a member who
    personally lacks E is caught by the read-time gate, which checks each
    actual reader.

    EMPTY when accounts are off, exactly as `held_entitlement_ids` is:
    there is no second account to share to on an open box, and both
    gates are present in code and unreachable in practice there.
    """
    if not accounts_on():
        return frozenset()
    if bool(user) == bool(group):
        return frozenset()
    rows = (EntitlementGrant.objects.filter(user=user) if user
            else EntitlementGrant.objects.filter(group=group))
    return frozenset(rows.values_list("entitlement_id", flat=True))


def held_entitlement_ids(principal, *, settings_row=None) -> frozenset[int]:
    """Every entitlement id this principal holds, directly or through one
    of their groups.

    EMPTY for the open, service and anonymous principals -- grants attach
    to a user or a group by the XOR constraint, so there is nothing for a
    non-user principal to join against. The open branch never reaches
    here in practice: every caller tests `accounts_on()` first.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only -- see `accounts_on` for why. Used here only to skip
    the query in `open` posture without a second singleton read.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    if not accounts_on(settings_row=row):
        return frozenset()
    return _grant_ids(principal)


def owned_entitlement_ids(principal, *, settings_row=None) -> frozenset[int]:
    """The subset of the above whose grant carries `role=owner`.

    An owner of E may grant it, label with it, and SEE everything in E.
    The third of those needs no code anywhere: an owner grant is also a
    grant, so it already appears in `held_entitlement_ids`.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    if not accounts_on(settings_row=row):
        return frozenset()
    return _grant_ids(principal, role=EntitlementGrant.Role.OWNER)


def may_see_unlabelled(principal, *, settings_row=None) -> bool:
    """Whether this principal may READ documents carrying no label.

    OPEN posture -> True; there is nobody for anything to be hidden from.
    `library_posture=open` (the default) -> True for anybody who is not
    anonymous, INCLUDING the service principal: a document the watcher
    created arrives unlabelled because it has no way to know who a
    dropped file is for, and inventing a default would be inventing a
    policy. `manage.py ask` therefore answers from the unlabelled part of
    the library rather than from nothing.
    `library_posture=locked` -> `sees_all_content` only. An administrator
    with the content setting OFF does NOT read an unlabelled document's
    bytes on a locked library -- they still see its ROW, and can label or
    delete it, which is what administering it requires.

    NO POSTURE BRANCH. `library_posture` is only EDITABLE on the
    enterprise page, and `identity.services.set_posture` resets it to
    `open` when a box moves to `personal`. That branch lives in the write
    path, on purpose; this read path stays posture-free.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    if not accounts_on(settings_row=row):
        return True
    if row.library_posture == LIBRARY_OPEN:
        return getattr(principal, "kind", "") != "anonymous"
    return sees_all_content(principal, settings_row=row)


def labelling_entitlements(principal, *, settings_row=None) -> tuple[tuple[int, str], ...]:
    """`((id, name), ...)` -- the entitlements this principal may LABEL
    with, in name order, for a form's `<select>`.

    Everything for an administrator; the owned ones for anybody else.
    THE SAME PREDICATE THE WRITE PATHS ENFORCE (`identity.services.grant`,
    `tools.rag.views.document_labels_bulk`, `agents.chat.views.tools`),
    so a form can never offer an option the POST will refuse.

    It lives here rather than in `tools/rag` or `agents` because those
    columns may not import `identity.models` -- they get ids and names as
    plain data, exactly as they get `held_entitlement_ids`.

    `settings_row`: an already-fetched `IdentitySettings` row, for a
    caller (`agents.chat.views.tools`) that reads the singleton once,
    via `identity.request.settings_row_for`, and needs more than this one
    answer off it. Optional and keyword-only, exactly like every other
    `settings_row`-accepting function here; a caller that leaves it out
    gets ONE `IdentitySettings.get_solo()` READ of its own, threaded
    through to all three calls below -- the same per-request-reuse norm
    `sees_all_content`'s own docstring states, not three singleton reads
    for one answer.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    if not accounts_on(settings_row=row):
        return ()
    rows = Entitlement.objects.all()
    if not is_admin(principal, settings_row=row):
        rows = rows.filter(pk__in=owned_entitlement_ids(principal, settings_row=row))
    return tuple(rows.order_by("name").values_list("pk", "name"))


def entitlement_names(ids) -> tuple[tuple[int, str], ...]:
    """`((id, name), ...)` for `ids`, in name order.

    So a page can render entitlement ids as names without importing
    `identity.models` -- the same reason `labelling_entitlements` lives
    here, and the same shape. NOT a substitute for it: that function
    answers "which may this principal LABEL with" (owned), and this one
    answers "what are these called", with the caller deciding which set
    to ask about. The workstream scope editor asks about
    `held_entitlement_ids(principal)`, deliberately (spec §6.1's M10).

    An id with no row is DROPPED rather than rendered as a blank: a name
    that is not there is not a name, and the caller's own count is what
    reports the difference.
    """
    rows = Entitlement.objects.filter(pk__in={int(i) for i in ids})
    return tuple(rows.order_by("name").values_list("pk", "name"))


def effective_entitlements(user) -> tuple[dict, ...]:
    """Every entitlement `user` effectively holds, and HOW.

    One row per entitlement: `{"id", "name", "role", "via"}`, where `via`
    is `""` for a direct grant and the group's name otherwise. Sorted by
    name, so two accounts' panels read the same way.

    A DIRECT GRANT WINS over a group one for the same entitlement, and
    the row appears ONCE: the direct grant is the stronger fact -- it
    survives the person leaving the group -- and showing the same
    entitlement twice would make an administrator count it twice.

    READ-ONLY, and used by exactly one surface (the users page). It takes
    a `User` row rather than a principal because the caller is an
    administrator asking about SOMEBODY ELSE, which is a different
    question from every other function in this module.
    """
    if not accounts_on():
        return ()
    rows: dict[int, dict] = {}
    grants = (EntitlementGrant.objects
              .filter(Q(user=user) | Q(group__user=user))
              .select_related("entitlement", "group"))
    for row in grants:
        via = "" if row.user_id else row.group.name
        existing = rows.get(row.entitlement_id)
        if existing is None or (existing["via"] and not via):
            rows[row.entitlement_id] = {"id": row.entitlement_id,
                                        "name": row.entitlement.name,
                                        "role": row.role, "via": via}
    return tuple(sorted(rows.values(), key=lambda row: row["name"]))


def share_subjects(principal) -> dict[str, tuple[tuple[int, str], ...]]:
    """The accounts and groups a share form may offer, as plain data:
    `{"users": ((pk, username), ...), "groups": ((pk, name), ...)}`.

    ACTIVE ACCOUNTS ONLY, and never the caller themselves -- sharing a
    row with yourself is a no-op a form should not offer.

    Here, not in `agents/chat`, for the same reason `labelling_
    entitlements` is here: a column that needed usernames would otherwise
    reach `identity.models` (forbidden) or `django.contrib.auth.
    get_user_model()` (legal, but a second path to one table that no
    production module outside this column takes today).
    """
    if not accounts_on():
        return {"users": (), "groups": ()}
    from django.contrib.auth.models import Group
    users = User.objects.filter(is_active=True).exclude(pk=_user_pk(principal) or 0)
    return {
        "users": tuple(users.order_by("username").values_list("pk", "username")),
        "groups": tuple(Group.objects.order_by("name").values_list("pk", "name")),
    }


def grant_subjects(principal) -> dict[str, tuple[tuple[int, str], ...]]:
    """The accounts and groups an ENTITLEMENT GRANT form may offer --
    the same shape as `share_subjects`, and deliberately NOT the same
    function.

    IT INCLUDES THE CALLER. Sharing a row with yourself is a no-op, which
    is why `share_subjects` excludes you; granting yourself an entitlement
    is not. An administrator with `admin_sees_content` off reads NOTHING
    labelled (`tools/rag/access.py::readable_documents` answers to
    `sees_all_content`), so holding the entitlement is the only way for
    them to read a document under it -- and spec section 18.2's first
    done-when criterion begins by granting entitlements to accounts. A
    grant form built from `share_subjects` would leave an administrator
    with no UI path to grant themselves one.

    Two readers rather than one with a flag, because the two questions
    are different questions: "who else could read this row" and "who may
    hold this permission". The bodies differ by one `.exclude()`, and
    that clause is the whole of what each answer means.
    """
    if not accounts_on():
        return {"users": (), "groups": ()}
    from django.contrib.auth.models import Group
    return {
        "users": tuple(User.objects.filter(is_active=True)
                       .order_by("username").values_list("pk", "username")),
        "groups": tuple(Group.objects.order_by("name").values_list("pk", "name")),
    }
