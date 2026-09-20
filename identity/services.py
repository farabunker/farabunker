"""The writes that can lock an operator out of their own box.

PRIVATE TO THIS COLUMN. Nothing outside `identity/` imports this module
-- pinned by `test_no_column_imports_identitys_private_modules`. Its
callers are the identity pages, `identity/admin.py`, and this column's
management commands, and that is deliberate: every one of these writes
has a guard, and a second door to any of them is a guard that does not
exist.
"""
from __future__ import annotations

from django.conf import settings
from django.db import IntegrityError, transaction

from identity import audit
from identity.access import is_admin, owned_entitlement_ids
from identity.axes import apply_axis, axis_for
from identity.cascades import cascade_counts, run_cascades
from identity.contracts import actions
from identity.contracts.actions import SOURCE_WEB
from identity.contracts.postures import (
    LIBRARY_LOCKED, LIBRARY_OPEN, POSTURE_OPEN, POSTURE_PERSONAL, POSTURES,
)
from identity.models import Entitlement, EntitlementGrant, IdentitySettings, User
from identity.ownership import owned_models

# The two library postures. Not exported from `identity.contracts.postures`
# as a bare tuple (only `LIBRARY_CHOICES`, paired with labels, is), so it is
# named here once rather than spelling `(LIBRARY_OPEN, LIBRARY_LOCKED)` at
# each call site that must validate one.
_LIBRARY_POSTURES = (LIBRARY_OPEN, LIBRARY_LOCKED)


class ServiceRefused(ValueError):
    """A write this platform will not perform, with an operator-readable
    reason.

    A distinct type, not a bare `ValueError`, so a view can render the
    message and a command can print it without either of them catching
    a genuine programming error by accident.
    """


def _active_superusers(*, lock: bool = False):
    qs = User.objects.filter(is_active=True, is_superuser=True)
    if lock:
        # ORDERED before `select_for_update()`: Postgres (and the SQL
        # standard) don't guarantee row-lock acquisition order on an
        # unordered `SELECT ... FOR UPDATE`, and two callers locking the
        # same set of rows in different orders is exactly how a
        # `select_for_update()` meant to SERIALISE concurrent callers
        # instead deadlocks them.
        return qs.order_by("pk").select_for_update()
    return qs


def _refuse_if_last_admin(user, *, because: str) -> None:
    """Refuse to leave the box with zero active superusers.

    MUST BE CALLED INSIDE `transaction.atomic()` WITH THE LOCK TAKEN.
    "At least one row satisfying a predicate" is not expressible as a
    `CheckConstraint`, so two simultaneous demotions could each see two
    admins and each proceed. `select_for_update()` serialises them and
    makes the second see one admin and refuse.
    """
    remaining = [row.pk for row in _active_superusers(lock=True)]
    if remaining == [user.pk]:
        raise ServiceRefused(
            f"{user.username!r} is the last active administrator on this box, so "
            f"it cannot be {because}. Promote another account first, or nobody "
            f"will be able to administer this box."
        )


def create_user(actor, *, username: str, password: str, is_superuser: bool = False,
                source: str = SOURCE_WEB) -> User:
    """Create an account. The audit row records the username and the
    superuser flag, and NEVER the password or any derivative of it."""
    try:
        with transaction.atomic():
            user = User(username=username, is_superuser=is_superuser,
                        is_staff=is_superuser)
            user.set_password(password)
            user.save()
            # INSIDE the same transaction as the row it describes: a
            # failed audit insert must roll the write back, not leave a
            # user row with no audit trail of its own creation.
            audit.record(actor, actions.USER_CREATED, target_type="user",
                         target_key=user.pk, target_label=username, source=source,
                         is_superuser=is_superuser)
    except IntegrityError as exc:
        raise ServiceRefused(f"An account called {username!r} already exists.") from exc
    return user


def set_password(actor, user, raw_password: str, *, source: str = SOURCE_WEB) -> None:
    """Reset somebody else's password.

    `identity.password_reset`, not `identity.password_changed`: the
    second is what a person does to their own, and the distinction is
    the one an operator reading the log actually wants.

    Django invalidates the target's existing sessions on a password
    change through `AbstractBaseUser.get_session_auth_hash`, so nothing
    here sweeps sessions either.
    """
    with transaction.atomic():
        user.set_password(raw_password)
        user.save(update_fields=["password"])
        audit.record(actor, actions.PASSWORD_RESET, target_type="user",
                     target_key=user.pk, target_label=user.username, source=source)


def set_superuser(actor, user, value: bool, *, source: str = SOURCE_WEB) -> None:
    """Promote or demote. Refuses to demote the last active superuser."""
    if user.is_superuser == value:
        # Not an event. An audit trail full of non-changes is a trail
        # nobody reads.
        return
    with transaction.atomic():
        if not value:
            _refuse_if_last_admin(user, because="demoted")
        user.is_superuser = value
        user.is_staff = value
        user.save(update_fields=["is_superuser", "is_staff"])
        audit.record(
            actor,
            actions.SUPERUSER_GRANTED if value else actions.SUPERUSER_REVOKED,
            target_type="user", target_key=user.pk, target_label=user.username, source=source,
        )


def owned_row_counts(principal_key: str) -> dict:
    """Per-table counts of the rows a user owns, walking the registry
    (`identity.ownership.owned_models`, which resolves each spec's model
    string and skips one whose app is not installed on this box)."""
    return {
        spec.label: model.objects.filter(
            owner_kind="user", owner_key=principal_key).count()
        for spec, model in owned_models()
    }


def resolve_owner_target(username: str, *, must_be_admin: bool = False) -> User:
    """The active account named by `username`, or a `ServiceRefused`
    explaining why not -- the shared first half of `adopt_open_rows`'s
    and `reassign_owner`'s target resolution: no such account, or a
    deactivated one, refuses identically for both commands.

    `must_be_admin`: adds `adopt_open_rows`'s own extra refusal.
    Adoption gives one account every unowned row on the box, which is
    an administrator's decision; `reassign_owner --to` moves rows to
    any active account, admin or not, since a member can already own
    rows today.
    """
    user = User.objects.filter(username=username).first()
    if user is None:
        raise ServiceRefused(f"There is no account called {username!r} on this box.")
    if not user.is_active:
        raise ServiceRefused(f"{username!r} is deactivated, so it cannot own rows.")
    if must_be_admin and not user.is_superuser:
        raise ServiceRefused(
            f"{username!r} is not a superuser. Adoption gives one account every "
            f"row on this box, which is an administrator's decision."
        )
    return user


def deactivate_user(actor, user, *, source: str = SOURCE_WEB) -> dict:
    """Deactivate an account. USERS ARE NEVER DELETED.

    Four effects, and the third is the one that needs stating:
    1. refuses if `user` is the last active superuser;
    2. sets `is_active = False`;
    3. OWNED ROWS ARE LEFT IN PLACE, and the per-table counts come back
       so the operator knows to run `manage.py reassign_owner`. A
       deleted user with owned rows is an orphan nobody can reason
       about;
    4. writes one audit event.

    IDEMPOTENT, like `reactivate_user`'s own early return: deactivating
    an already-inactive user is not a second event, just the current
    owned-row counts back.

    SESSIONS DIE FOR FREE, and that is why no session-sweeping code
    exists here: `ModelBackend.get_user` calls `user_can_authenticate`,
    which is False for an inactive user, so `request.user` becomes
    anonymous on the very next request that presents the old cookie.
    Writing a `Session`-table sweep on top of that would be a second,
    weaker copy of a mechanism Django maintains.

    3b. every `EntitlementGrant` naming this USER is deleted -- owner
        decision 25. A grant held through a GROUP survives, because it
        is the group's, not this account's.
    """
    if not user.is_active:
        return owned_row_counts(str(user.pk))
    with transaction.atomic():
        _refuse_if_last_admin(user, because="deactivated")
        user.is_active = False
        user.save(update_fields=["is_active"])
        # Owner decision 25: grants are dropped. USER grants only --
        # a GROUP grant belongs to the group, and removing it here would
        # revoke an entitlement from everybody else in it.
        EntitlementGrant.objects.filter(user=user).delete()
        # `owned_row_counts` and `audit.record` both happen INSIDE the
        # same transaction as the write they describe, for the same
        # reason `create_user` does: a failed audit insert must roll
        # the deactivation back, not leave a deactivated user with no
        # audit row.
        counts = owned_row_counts(str(user.pk))
        audit.record(actor, actions.USER_DEACTIVATED, target_type="user",
                     target_key=user.pk, target_label=user.username, source=source,
                     owned_rows=counts)
    return counts


def reactivate_user(actor, user, *, source: str = SOURCE_WEB) -> None:
    """The same action in reverse, minus any grants -- a dropped grant
    is a decision somebody made, and restoring it silently would undo
    that decision without anybody choosing to."""
    if user.is_active:
        return
    with transaction.atomic():
        user.is_active = True
        user.save(update_fields=["is_active"])
        audit.record(actor, actions.USER_REACTIVATED, target_type="user",
                     target_key=user.pk, target_label=user.username, source=source)


def _refuse_a_switch_away_from_open() -> None:
    """The three conditions, in one place, with one operator-readable
    message each.

    A CHECK THAT RUNS ONLY AT STARTUP IS NOT ENOUGH, which is why this
    exists alongside `identity/checks.py`. The image starts with
    `migrate && <server>`, so `manage.py check` runs once per boot;
    flipping the posture on a RUNNING box with `DEBUG=1` or a default
    `SECRET_KEY` would change nothing until the next restart -- which is
    precisely the window in which the operator believes accounts are on.

    `manage.py identity_posture` reaches this same function, so the
    break-glass path is not a way around any of the three.
    """
    if not _active_superusers().exists():
        raise ServiceRefused(
            "This box has no active superuser, so switching away from the open "
            "posture would lock everybody out. Run `manage.py createsuperuser` "
            "first."
        )
    if settings.DEBUG:
        raise ServiceRefused(
            "DEBUG is on. A box with accounts must not render tracebacks -- with "
            "its settings and environment in them -- to any visitor. Set DEBUG=0, "
            "together with a real SECRET_KEY and this box's ALLOWED_HOSTS, then "
            "recreate the containers before switching posture. The full order is "
            "in docs/OPERATIONS.md under “Leaving the open posture”."
        )
    if settings.SECRET_KEY == settings.DEV_SECRET_KEY:
        raise ServiceRefused(
            "This box is still using the shipped development signing key. That "
            "key signs every session cookie, and it is published in a public "
            "repository -- anybody who can read it could mint a session for any "
            "account here. Set a real SECRET_KEY and recreate the containers "
            "before switching posture."
        )


def set_posture(actor, *, posture: str | None = None, library_posture: str | None = None,
                admin_sees_content: bool | None = None,
                session_idle_minutes: int | None = None,
                source: str = SOURCE_WEB) -> IdentitySettings:
    """Write the posture row, with one audit event per AUDITED field
    changed -- `posture`, `library_posture` and `admin_sees_content`.
    `session_idle_minutes` carries no catalogue action of its own (it is
    an operational tuning knob, not a change to who can see what) and is
    applied silently -- said here so this docstring does not quietly
    promise more than the audit trail records.

    A SWITCH, NOT A MIGRATION: this writes columns on one row and
    nothing else. It does not create, delete or rewrite any user; it
    does not touch `owner_kind`/`owner_key` on any row (that is
    adoption, and it is a separate, named, audited command).

    Switching TO `open` is refused by none of the three conditions --
    reducing a posture is always allowed, and an operator whose box is
    misconfigured must always be able to make it less strict.

    An explicit `library_posture` passed IN THE SAME CALL as a switch
    TO `personal` (or while the box already IS `personal`) is IGNORED,
    and the reset to `open` is what wins: `personal` renders no
    library-posture control, so honouring a caller's `locked` first and
    then immediately resetting it would write two contradictory audit
    rows for a state the platform has no page to reach or leave.
    """
    if library_posture is not None and library_posture not in _LIBRARY_POSTURES:
        raise ServiceRefused(f"{library_posture!r} is not a library posture on this platform.")

    row = IdentitySettings.get_solo()
    events: list[tuple[str, dict]] = []

    if posture is not None and posture != row.posture:
        if posture not in POSTURES:
            raise ServiceRefused(f"{posture!r} is not a posture on this platform.")
        if posture != POSTURE_OPEN:
            _refuse_a_switch_away_from_open()
        events.append((actions.POSTURE_CHANGED, {"to": posture}))
        row.posture = posture

    if row.posture == POSTURE_PERSONAL:
        # THE ONE PLACE A POSTURE BRANCH LIVES, and it lives in a write
        # path on purpose. `personal` renders no library-posture
        # control, so a box arriving there -- or already sitting there
        # -- would otherwise be able to hold a lock with no page to
        # lift it. This is checked AFTER the posture write above and
        # BEFORE the explicit `library_posture` branch below runs, so a
        # caller asking for `personal` and `locked` in the same call is
        # authoritatively reset to `open` rather than honoured and then
        # immediately reset -- one audit row, not two contradictory
        # ones. The read paths in `identity/access.py` stay
        # posture-free.
        if row.library_posture != LIBRARY_OPEN:
            row.library_posture = LIBRARY_OPEN
            events.append((actions.LIBRARY_POSTURE_CHANGED,
                           {"to": LIBRARY_OPEN, "reason": "posture is personal"}))
    elif library_posture is not None and library_posture != row.library_posture:
        row.library_posture = library_posture
        events.append((actions.LIBRARY_POSTURE_CHANGED, {"to": library_posture}))

    if admin_sees_content is not None and admin_sees_content != row.admin_sees_content:
        row.admin_sees_content = admin_sees_content
        events.append((actions.ADMIN_CONTENT_ACCESS_CHANGED, {"to": admin_sees_content}))

    if session_idle_minutes is not None:
        # M5 (Coherence Wave B review): DELIBERATELY UNAUDITED -- see
        # this function's own docstring above ("an operational tuning
        # knob, not a change to who can see what"), recorded before S3
        # (Coherence Wave B) closed the OTHER four settings surfaces'
        # audit gap. S3's four-surface ruling never named this field --
        # it is not one of RagSettings/JobSettings/ModelConnection/
        # RoleBinding -- so `identity/contracts/actions.py`'s
        # "house default is audited" comment describes the rule S3
        # closed for THOSE four, not a retroactive claim on this
        # pre-existing, separately-argued exception.
        try:
            minutes = int(session_idle_minutes)
        except (TypeError, ValueError) as exc:
            raise ServiceRefused(
                f"{session_idle_minutes!r} is not a valid number of minutes."
            ) from exc
        row.session_idle_minutes = max(0, minutes)

    with transaction.atomic():
        row.save()
        # INSIDE the transaction that writes the row: a failed audit
        # insert must roll the posture write back, not leave a changed
        # row with no record of who changed it or why.
        for action, detail in events:
            audit.record(actor, action, target_type="posture", target_key=row.pk,
                         source=source, **detail)
    return row


def _actor_user(actor):
    """The `User` row `actor` names, or None.

    THE SAME `isdecimal()` GUARD `identity/access.py::_user_row` applies,
    for the same reason: `Principal.key` for a user is the primary key AS
    A STRING, and a key that is not a decimal string -- from a
    hand-written payload, or a row written against an older schema --
    would reach `filter(pk=...)` and raise `ValueError` inside a
    request. A never-500 surface cannot afford that, and "nobody" is the
    correct reading of an unparseable identity for a `created_by`/
    `granted_by` column that is nullable anyway.
    """
    if getattr(actor, "kind", None) != "user":
        return None
    key = actor.key
    if not isinstance(key, str) or not key.isdecimal():
        return None
    return User.objects.filter(pk=int(key)).first()


def may_administer_entitlement(principal, entitlement_id: int) -> bool:
    """Whether `principal` may grant, revoke or label with this
    entitlement: an administrator, or an OWNER of this one.

    ONE PREDICATE, THREE COLUMNS. `identity.services.grant`/`revoke`,
    `tools.rag.views.document_labels_bulk` and
    `agents.chat.views.tools.tool_entitlements` all ask exactly this
    question, and three hand-written copies of it are how two surfaces
    come to disagree about who may label what. The other two columns
    cannot import this module (it is column-private), so they ask
    `identity.access.is_admin` + `::owned_entitlement_ids` -- the same
    two facts, in the sanctioned seam -- and this function is the
    identity-side spelling of it.
    """
    return is_admin(principal) or entitlement_id in owned_entitlement_ids(principal)


def _refuse_unless_admin(principal, *, because: str) -> None:
    if not is_admin(principal):
        raise ServiceRefused(
            f"Only an administrator of this box may {because}. An entitlement's "
            f"owner may grant it, revoke it and label with it, and nothing else."
        )


def create_entitlement(actor, *, name: str, description: str = "",
                       source: str = SOURCE_WEB) -> Entitlement:
    """Create a named permission. ADMINISTRATOR ONLY -- an owner of one
    entitlement has no standing to mint another."""
    _refuse_unless_admin(actor, because="create an entitlement")
    clean = (name or "").strip()
    if not clean:
        raise ServiceRefused("An entitlement needs a name.")
    creator = _actor_user(actor)
    try:
        with transaction.atomic():
            row = Entitlement.objects.create(name=clean, description=description.strip(),
                                             created_by=creator)
            audit.record(actor, actions.ENTITLEMENT_CREATED, target_type="entitlement",
                         target_key=row.pk, target_label=row.name, source=source)
    except IntegrityError as exc:
        raise ServiceRefused(
            f"An entitlement called {clean!r} already exists. Names are compared "
            f"without regard to case, so 'Finance' and 'finance' are one entitlement."
        ) from exc
    return row


def rename_entitlement(actor, entitlement, name: str, *, source: str = SOURCE_WEB) -> None:
    """ADMINISTRATOR ONLY. A rename changes what every grant, label and
    audit line READS as, which is a library-wide fact."""
    _refuse_unless_admin(actor, because="rename an entitlement")
    clean = (name or "").strip()
    if not clean:
        raise ServiceRefused("An entitlement needs a name.")
    if clean == entitlement.name:
        # Not an event. An audit trail full of non-changes is a trail
        # nobody reads.
        return
    was = entitlement.name
    try:
        with transaction.atomic():
            entitlement.name = clean
            entitlement.save(update_fields=["name"])
            audit.record(actor, actions.ENTITLEMENT_RENAMED, target_type="entitlement",
                         target_key=entitlement.pk, target_label=clean, source=source,
                         **{"from": was, "to": clean})
    except IntegrityError as exc:
        # RESTORED, NOT LEFT AS `clean`: the transaction rolled the
        # database back, but Django does not roll an in-memory instance
        # back with it, and a caller that re-renders `entitlement` after
        # catching this (Task 3's edit view) must see the name this row
        # actually holds, not the rejected one.
        entitlement.name = was
        raise ServiceRefused(f"An entitlement called {clean!r} already exists.") from exc


def entitlement_delete_counts(entitlement) -> dict[str, int]:
    """What deleting `entitlement` would take with it, per kind --
    `{"Grants": 2, "Document labels": 14, "Tool labels": 1}`.

    THE DELETE CONFIRMATION NAMES THESE FIRST (spec section 12.1).
    Deleting an entitlement is a superuser action precisely because of
    the second entry: every document it was the last label on becomes
    UNLABELLED and follows the library posture, which silently WIDENS
    access. A confirmation that did not say so would be a confirmation
    of the wrong thing.
    """
    return _cascade_counts_for(entitlement, commit=False)


def entitlement_reach(entitlement) -> dict[str, int]:
    """What this entitlement touches, per kind — for the entitlement
    page's reach panel.

    THE SAME FUNCTION THE DELETE CONFIRMATION COUNTS WITH
    (`entitlement_delete_counts`), aliased rather than reimplemented so
    the two can never disagree (spec section 22.34). It is a one-line
    alias on purpose: the moment it grows a body of its own, it has
    become the second counter this decision exists to prevent.
    """
    return entitlement_delete_counts(entitlement)


def set_entitlement_axis(actor, entitlement, axis_key: str, *, add, remove
                         ) -> tuple[str, dict[str, int]]:
    """ADMINISTRATOR ONLY. Add and/or remove `entitlement` on the rows of
    one registered axis, and answer `(label, {"added": n, "removed": n})`.

    THE MIRROR IMAGE OF `delete_entitlement`. That one asks every
    registered CASCADE "what do you lose when this goes away"; this one
    asks one registered AXIS "which of your rows carry this, and change
    it" -- the same dotted-path registry mechanism, resolved by
    `identity/axes.py`, because `identity/` may name nothing in the
    columns that own the join tables (import-law rule 4).

    ADMINISTRATOR ONLY, UNLIKE GRANT AND REVOKE, and the ruling is worth
    stating rather than inferring from this line. `identity-entitlement-
    edit` is the one class-R page in this column precisely so an
    entitlement OWNER can reach it, and spec section 7.4 names their
    three capabilities: grant it, revoke it, and label DOCUMENTS with
    it. A tool, an agent, a flow and a model set are box inventory, not
    somebody's library shelf -- `models/registry/labels.py`'s own
    docstring already says so for the sets page, and `agents/chat/views/
    tools.py`'s says it for tools ("a tool label has no entitlement
    owner"). Offering an owner a control whose POST would refuse them is
    the thing this codebase does not do, so `identity/views.py` renders
    these panels for an administrator only -- and this guard is what
    makes that a rule rather than a template detail.

    `actor_user` IS DERIVED, not passed: `_actor_user` already answers
    the `User` row a `Principal` names, and it is what fills the
    `labelled_by`/`attached_by` provenance columns the columns' own
    writers take.
    """
    _refuse_unless_admin(actor, because="change what an entitlement covers")
    spec = axis_for(axis_key)
    if spec is None or not spec.editable:
        # F7's flash-and-redirect shape at the view, from a refusal that
        # names what was submitted -- the same reading an unknown
        # `action` gets one layer up.
        raise ServiceRefused(f"{axis_key!r} is not an editable kind on this box.")
    summary = apply_axis(spec, entitlement.pk, add=add, remove=remove,
                         actor=actor, actor_user=_actor_user(actor))
    return spec.label, summary


def _cascade_counts_for(entitlement, *, commit: bool) -> dict[str, int]:
    """`{label: count}` for every kind an entitlement delete takes with
    it -- grants (this column, by `CASCADE`) plus each registered
    cross-column cascade.

    ONE FUNCTION, TWO CALLERS, so `"Grants"` -- the string the delete
    confirmation renders -- is written once. Two copies of a label the
    page prints is two things to keep in agreement for no gain.
    """
    counts = {"Grants": entitlement.grants.count()}
    counts.update(run_cascades(entitlement.pk) if commit
                  else cascade_counts(entitlement.pk))
    return counts


def delete_entitlement(actor, entitlement, *, source: str = SOURCE_WEB) -> dict[str, int]:
    """ADMINISTRATOR ONLY. Removes the entitlement, its grants, and --
    through the registered cascades -- its document and tool labels,
    re-stamping every affected document's chunk metadata in the SAME
    transaction.

    The cascades run BEFORE `entitlement.delete()`, deliberately: the
    re-stamp has to see a document with its label already gone, and
    Django's own `CASCADE` collector gives no hook between "rows
    deleted" and "transaction committed". The database `CASCADE` stays in
    place as the net -- a shell that deletes an `Entitlement` row
    directly still leaves no orphans, only a stale chunk cache, and
    `manage.py relabel_chunks` is the documented repair.
    """
    _refuse_unless_admin(actor, because="delete an entitlement")
    # BOTH CAPTURED BEFORE THE DELETE. Django's `Collector.delete()` sets
    # `instance.pk = None` on every deleted instance, and
    # `identity/audit.py` writes `str(target_key)[:255]` -- so reading
    # `entitlement.pk` after the delete records the literal string
    # "None", and an audit row that cannot name its target is not an
    # audit row. `delete_group` below captures its pk for the same reason.
    label, pk = entitlement.name, entitlement.pk
    with transaction.atomic():
        counts = _cascade_counts_for(entitlement, commit=True)
        entitlement.delete()
        audit.record(actor, actions.ENTITLEMENT_DELETED, target_type="entitlement",
                     target_key=pk, target_label=label, source=source,
                     removed=counts)
    return counts


def _refuse_unless_may_administer(actor, entitlement_id: int) -> None:
    if not may_administer_entitlement(actor, entitlement_id):
        raise ServiceRefused(
            "You may only grant and revoke entitlements you own. Ask an "
            "administrator, or an owner of this entitlement, to make the change."
        )


def grant(actor, entitlement, *, user=None, group=None,
          role: str = EntitlementGrant.Role.MEMBER,
          source: str = SOURCE_WEB) -> EntitlementGrant:
    """Grant `entitlement` to exactly one of `user`/`group`.

    ADMINISTRATOR, OR AN OWNER OF THIS ENTITLEMENT -- delegation without
    a second role table, which is the whole of owner decision 15.

    The user-XOR-group rule is refused HERE as well as by the database
    constraint: an `IntegrityError` surfacing out of a page as a 500 is a
    never-500 violation, and "you named both" is a sentence a form can
    show.
    """
    _refuse_unless_may_administer(actor, entitlement.pk)
    if bool(user) == bool(group):
        raise ServiceRefused("A grant names exactly one account or one group, never both.")
    if role not in EntitlementGrant.Role.values:
        raise ServiceRefused(f"{role!r} is not a grant role on this platform.")
    granted_by = _actor_user(actor)
    try:
        with transaction.atomic():
            row = EntitlementGrant.objects.create(
                entitlement=entitlement, user=user, group=group, role=role,
                source=EntitlementGrant.Source.MANUAL, granted_by=granted_by)
            audit.record(actor, actions.GRANT_ADDED, target_type="entitlement",
                         target_key=entitlement.pk, target_label=entitlement.name,
                         source=source, role=role,
                         subject=("user" if user else "group"),
                         subject_key=(user.pk if user else group.pk),
                         subject_label=(user.username if user else group.name))
    except IntegrityError as exc:
        subject = user.username if user else group.name
        raise ServiceRefused(
            f"{subject!r} already holds {entitlement.name!r}. Change the role "
            f"instead of granting it twice."
        ) from exc
    return row


def set_grant_role(actor, grant_row, role: str, *, source: str = SOURCE_WEB) -> None:
    """Promote a member to owner, or demote an owner to member. AN
    UPDATE, not a second row -- which is why the unique constraints do
    not include `role`."""
    _refuse_unless_may_administer(actor, grant_row.entitlement_id)
    if role not in EntitlementGrant.Role.values:
        raise ServiceRefused(f"{role!r} is not a grant role on this platform.")
    if grant_row.role == role:
        return
    with transaction.atomic():
        was = grant_row.role
        grant_row.role = role
        grant_row.save(update_fields=["role"])
        audit.record(actor, actions.GRANT_ROLE_CHANGED, target_type="entitlement",
                     target_key=grant_row.entitlement_id,
                     target_label=grant_row.entitlement.name, source=source,
                     **{"from": was, "to": role})


def revoke(actor, grant_row, *, source: str = SOURCE_WEB) -> None:
    """Remove one grant. ADMINISTRATOR, OR AN OWNER OF THAT
    ENTITLEMENT."""
    _refuse_unless_may_administer(actor, grant_row.entitlement_id)
    subject = grant_row.user.username if grant_row.user_id else grant_row.group.name
    with transaction.atomic():
        entitlement = grant_row.entitlement
        grant_row.delete()
        audit.record(actor, actions.GRANT_REVOKED, target_type="entitlement",
                     target_key=entitlement.pk, target_label=entitlement.name,
                     source=source, subject_label=subject)


# --- groups: Django's own, membership only ------------------------------

def create_group(actor, *, name: str, source: str = SOURCE_WEB):
    """ADMINISTRATOR ONLY. `auth.Group`, unchanged.

    This platform never reads `Group.permissions` -- Django's permission
    catalogue is a named non-goal -- so nothing subclasses `Group`. Its
    admin is unregistered outright (H40, `identity/admin.py`): this
    function's door, the identity page, is the only one, and it audits.
    A group here is a bag of accounts a grant can name.
    """
    from django.contrib.auth.models import Group
    _refuse_unless_admin(actor, because="create a group")
    clean = (name or "").strip()
    if not clean:
        raise ServiceRefused("A group needs a name.")
    try:
        with transaction.atomic():
            group = Group.objects.create(name=clean)
            audit.record(actor, actions.GROUP_CREATED, target_type="group",
                         target_key=group.pk, target_label=clean, source=source)
    except IntegrityError as exc:
        raise ServiceRefused(f"A group called {clean!r} already exists.") from exc
    return group


def delete_group(actor, group, *, source: str = SOURCE_WEB) -> None:
    """ADMINISTRATOR ONLY. Its grants and its shares go with it, by
    `CASCADE` on both foreign keys (owner decision 25)."""
    _refuse_unless_admin(actor, because="delete a group")
    label = group.name
    with transaction.atomic():
        pk = group.pk
        group.delete()
        audit.record(actor, actions.GROUP_DELETED, target_type="group",
                     target_key=pk, target_label=label, source=source)


def add_group_member(actor, group, user, *, source: str = SOURCE_WEB) -> None:
    """ADMINISTRATOR ONLY, and IDEMPOTENT: adding somebody who is already
    a member is not a second event."""
    _refuse_unless_admin(actor, because="change a group's membership")
    if user.groups.filter(pk=group.pk).exists():
        return
    with transaction.atomic():
        user.groups.add(group)
        audit.record(actor, actions.GROUP_MEMBER_ADDED, target_type="group",
                     target_key=group.pk, target_label=group.name, source=source,
                     subject_key=user.pk, subject_label=user.username)


def remove_group_member(actor, group, user, *, source: str = SOURCE_WEB) -> None:
    """The same in reverse, equally idempotent."""
    _refuse_unless_admin(actor, because="change a group's membership")
    if not user.groups.filter(pk=group.pk).exists():
        return
    with transaction.atomic():
        user.groups.remove(group)
        audit.record(actor, actions.GROUP_MEMBER_REMOVED, target_type="group",
                     target_key=group.pk, target_label=group.name, source=source,
                     subject_key=user.pk, subject_label=user.username)
