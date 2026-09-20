"""The identity pages.

Server-rendered, zero JS, extending `foundation/templates/_shell.html`,
matching every other page on the box.

DJANGO'S OWN VIEWS DO THE WORK. `LoginView`, `LogoutView` and
`PasswordChangeView` are subclassed for exactly two reasons: to write
the audit event, and to render this platform's templates. Password
hashing, validation, session invalidation on password change and the
`is_active` refusal are all Django's, maintained by somebody else --
this phase adds only what Django lacks.

`users`, `user_create`, `user_edit` and `settings_page` add the two
admin pages (T9). EVERY MUTATION THEY MAKE GOES THROUGH
`identity.services` -- the last-admin guard, the three posture
refusals and every audit row live there, and a view that reached the
model directly would be a second, unguarded door to exactly the writes
that have guards. `@require_admin` (`identity/gate.py`) sits on each as
a belt beside `IdentityGateMiddleware`'s braces.

Nothing here CONSTRUCTS a `Principal` -- `identity/contracts/
principals.py` and `identity/request.py` are the only two files allowed
to (the AST guard in `foundation/ops/tests/test_import_law.py`), so the
audited principal is always read through `principal_for_request`,
called AFTER Django's own `form_valid` has updated `request.user`.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.models import Group
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy

# THE ONE THING THIS COLUMN IMPORTS FROM ANOTHER, and it is the shell it
# renders inside rather than a peer's logic: four of these pages are
# settings pages, and `settings_redirect` is the settings chrome's own
# rule for redirect-after-POST -- it carries the assistant panel's open
# flag when the submitting form carried one, so a save on a settings
# page does not shut a panel the operator had open. `foundation/` is the
# platform column every other column may import (`AGENTS.md`'s import
# law); the panel's own column, `agents/`, is closed to this one, which
# is exactly why the rule lives in `foundation/` at all.
from foundation.settings_area import settings_redirect
from identity import audit, services, throttle
from identity.access import effective_entitlements, grant_subjects, is_admin, owned_entitlement_ids
from identity.access import posture as current_posture
from identity.axes import (
    AxisRefused, axis_doors, axis_for, axis_labels, axis_panels,
    reach_by_entitlement,
)
from identity.contracts import actions
from identity.contracts.postures import POSTURE_PERSONAL
from identity.contracts.principals import ANONYMOUS
from identity.forms import GrantForm, NameForm, PostureForm, SetPasswordForm, UserCreateForm
from identity.gate import require_admin
from identity.models import Entitlement, EntitlementGrant, IdentitySettings, User
from identity.request import principal_for_request, settings_row_for


# IDENTICAL FOR EVERY USERNAME -- real, misspelled, or never created.
# Django's generic authentication error is what keeps this page free of
# a username-enumeration oracle, and a lockout message that only
# appeared for accounts that exist would hand it straight back. The
# `{minutes}` figure is no exception: `throttle.WINDOW` is the same
# fixed window for any locked-out username -- real or not -- so filling
# it in adds no distinguishing signal (review round 1). Read directly
# off the constant, not off `throttle.seconds_remaining`: that function
# reports nothing finer than this same WINDOW while locked out (its own
# docstring), so a caller that already knows the account is locked out
# (as `LoginView.post` does, having just called `locked_out`) gains
# nothing from a second COUNT query to recompute it.
_LOCKED_OUT_MESSAGE = (
    "Too many sign-in attempts. Try again in about {minutes} minutes."
)


class LoginView(auth_views.LoginView):
    """Sign in, and record both outcomes.

    `redirect_authenticated_user` is deliberately LEFT OFF (Django's
    default): turning it on makes the login page a redirect oracle that
    leaks whether a session is valid, and this page has nothing to gain
    from it.
    """

    template_name = "identity/login.html"

    # S7: refused BEFORE `AuthenticationForm` runs, so a locked-out
    # username costs no password hash -- which is both the CPU-exhaustion
    # half of the finding and the timing oracle a post-hash refusal would
    # leave in place.
    #
    # A REFUSAL IS NOT A FAILURE and is deliberately NOT audited as one:
    # counting it would extend the window every time the attacker
    # knocked, and the owner of a hammered username would never get back
    # in. The attempts that CAUSED the lockout are already in the log.
    def post(self, request, *args, **kwargs):
        username = (request.POST.get("username") or "").strip()
        if throttle.locked_out(username):
            # UNBOUND, and that is the whole point of this branch rather
            # than a stylistic choice. `add_error` touches `form.errors`,
            # which runs `full_clean()` -> `AuthenticationForm.clean()`
            # -> `authenticate()`. On a BOUND form (`self.get_form()`
            # binds `request.POST`) that is exactly the password hash and
            # the timing oracle this refusal exists to avoid -- and it
            # would also add Django's generic "Please enter a correct
            # username and password" beside our message, so the page
            # would say two contradictory things at once. `full_clean`
            # returns immediately on `if not self.is_bound`.
            #
            # `full_clean` returning early also means it never sets
            # `cleaned_data` (`django/forms/forms.py`'s `full_clean` only
            # does that past the `is_bound` guard) -- and `add_error`
            # unconditionally reads `self.cleaned_data` to see whether a
            # stale field value needs clearing. An unbound form has no
            # stale value to clear, so an empty dict is the correct
            # answer, not a workaround for one.
            #
            # `throttle.WINDOW` directly, not `throttle.seconds_remaining`
            # (final fix wave): `locked_out` above already ran the one
            # COUNT query this request needs, and `seconds_remaining`
            # reports nothing finer than the same fixed WINDOW anyway
            # (its own docstring: an upper bound, never an exact
            # countdown) -- calling it here would spend a second,
            # redundant COUNT query to recompute a constant. `max(1, ...)`
            # is gone too: `WINDOW` is fifteen minutes, so the floor never
            # binds; it only ever protected against a `WINDOW` shorter
            # than a minute, which is not this platform's configuration.
            minutes = int(throttle.WINDOW.total_seconds()) // 60
            form = self.get_form_class()(request=request, initial={"username": username})
            form.cleaned_data = {}
            form.add_error(None, _LOCKED_OUT_MESSAGE.format(minutes=minutes))
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        # `super().form_valid` has already called `auth_login`, so
        # `self.request.user` is the newly signed-in user and
        # `principal_for_request` reads it rather than this view minting
        # a `Principal` of its own.
        user = form.get_user()
        principal = principal_for_request(self.request)
        audit.record(principal, actions.LOGIN,
                     target_type="user", target_key=user.pk, target_label=user.username)
        return response

    def form_invalid(self, form):
        # The SUBMITTED username, and nothing else. Never the password,
        # never a hash of it, never the request body. This row exists so
        # a brute-force attempt is visible in the log; `identity.throttle`
        # (S7) is the reader that turns a run of these into a lockout,
        # enforced above in `post` before the form is ever bound.
        #
        # CANONICALISED before it is written (review round 1): the read
        # side (`throttle.locked_out`) canonicalises before matching, so
        # a write that stored the raw string -- trailing whitespace, an
        # NFKC-equivalent spelling -- would silently never count toward
        # that username's own lockout. One key, both sides.
        #
        # CANONICALISE, THEN TRUNCATE TO 255 (whole-branch review, final
        # wave; was the other way around) -- same order `throttle.
        # locked_out` now uses, and for the same reason: truncating the
        # raw string to 255 chars BEFORE stripping/NFKC-normalising it
        # breaks on a username with more than 255 chars of leading
        # whitespace, where the truncated prefix is nothing but
        # whitespace and collapses to `""` while the read side (given
        # the SAME input) resolves the real username underneath it --
        # two different keys for one attempt.
        submitted = throttle.canonical_username(form.data.get("username") or "")[:255]
        audit.record(ANONYMOUS, actions.LOGIN_FAILED, target_label=submitted)
        return super().form_invalid(form)


class LogoutView(auth_views.LogoutView):
    """POST-only in the installed Django, which is correct: a GET logout
    is a URL anybody can put in an image tag."""

    def post(self, request, *args, **kwargs):
        principal = principal_for_request(request)
        username = getattr(request.user, "username", "")
        response = super().post(request, *args, **kwargs)
        if principal.kind == "user":
            audit.record(principal, actions.LOGOUT, target_type="user",
                         target_key=principal.key, target_label=username)
        return response


class PasswordChangeView(auth_views.PasswordChangeView):
    """A person's own password.

    Django invalidates every other session for this account on success
    (`AbstractBaseUser.get_session_auth_hash`) and keeps this one
    through `update_session_auth_hash`. Nothing here duplicates that.
    """

    template_name = "identity/password_change.html"
    success_url = reverse_lazy("identity-password-change-done")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        principal = principal_for_request(self.request)
        audit.record(principal, actions.PASSWORD_CHANGED,
                     target_type="user", target_key=user.pk, target_label=user.username)
        return response


class PasswordChangeDoneView(auth_views.PasswordChangeDoneView):
    template_name = "identity/password_change_done.html"


# --- the users page ------------------------------------------------------

# `identity.services.set_password`/`set_superuser`/`deactivate_user`/
# `reactivate_user`, keyed by the one `action` field `user_edit` dispatches
# on. `rename` is NOT included: there is no `identity.services` function
# (and no `AUDIT_ACTIONS` entry) backing a username change, and this task's
# file list does not modify `identity/services.py` -- wiring it up here
# would be either a silent, unaudited write straight to the model (the
# second door every other mutation on this page refuses to open) or scope
# this task was not given. An unrecognised action, `rename` included,
# answers 400 rather than a silent no-op.
_USER_ACTIONS = ("deactivate", "reactivate", "promote", "demote", "set_password")


def _flash_form_errors(request, form) -> None:
    """One `messages.error` per field error, in ONE format.

    Round-2 FIX-NOW 4: `user_create` and `user_edit`'s `set_password`
    branch each rolled their own version of this loop, and they had
    already drifted -- one prefixed the field name (`"password: This
    password is too short"`), the other did not (`"This password is too
    short"`), so the SAME kind of mistake read with a different amount
    of context depending which form on the page made it. Field-prefixed,
    always: a one-field form costs nothing extra saying so, and a
    multi-field form (`UserCreateForm`'s username/password) needs it to
    be useful at all.
    """
    for field, errors in form.errors.items():
        for error in errors:
            messages.error(request, f"{field}: {error}")


@require_admin
def users(request):
    """List every account, and the create-account form.

    `identity.access.posture()`, not the request's own gate, decides
    whether the create form offers the superuser checkbox: in `personal`
    every account IS an administrator (owner decision), so the page does
    not offer a choice it does not have.

    EACH ROW ALSO CARRIES `effective_entitlements(account)` (spec section
    22.34's read-only effective-access panel): every entitlement that
    account holds, direct or via a group, and what it unlocks. ONE QUERY
    PER ACCOUNT, not one query with a Python regroup -- this page is
    opened rarely, by an operator, and a box with a handful of accounts
    gains nothing from the regroup's extra complexity that a plain loop
    does not already give it for free (the same "one query per row over
    per-row loops" trade-off `tool_entitlement_ids`/`agent_entitlement_
    ids` already accept elsewhere in this codebase).

    THE REACH OF EACH HELD ENTITLEMENT is `entitlement_reach` -- the SAME
    cascade-registry read the entitlement page's own reach panel uses,
    so "what does holding Finance unlock" never has a second answer.
    Memoised PER ENTITLEMENT (not per account) in `_reach_cache` below:
    two accounts sharing an entitlement must not pay for its cascade
    queries twice in the one request that lists them both.
    """
    accounts = list(User.objects.order_by("username"))
    # ONE CALL PER ACCOUNT, held once: `effective_entitlements(account)` is
    # computed here and read back below for both the entitlement-id
    # collection and the per-row list -- calling it a second time per
    # account would make the docstring's "one query per account" a lie
    # (T16 follow-up review, finding 1).
    held = {account.pk: effective_entitlements(account) for account in accounts}
    entitlement_ids = {row["id"] for rows in held.values() for row in rows}
    entitlements_by_id = {
        e.pk: e for e in Entitlement.objects.filter(pk__in=entitlement_ids)
    }
    _reach_cache: dict[int, dict] = {}

    def _reach(entitlement_id):
        if entitlement_id not in _reach_cache:
            _reach_cache[entitlement_id] = services.entitlement_reach(
                entitlements_by_id[entitlement_id])
        return _reach_cache[entitlement_id]

    rows = [
        {
            "user": account,
            "entitlements": [
                {**row, "reach": _reach(row["id"])}
                for row in held[account.pk]
            ],
        }
        for account in accounts
    ]

    form = UserCreateForm()
    return render(request, "identity/users.html", {
        "rows": rows,
        "form": form,
        "posture": current_posture(),
    })


@require_admin
def user_create(request):
    """POST-only. On a form error -- a blank username, a password Django's
    own validators reject, a duplicate username -- this redirects back to
    the users page with `messages.error`, the same never-500/never-silent
    shape every mutation on that page uses."""
    form = UserCreateForm(request.POST)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return settings_redirect(request, "identity-users")

    # In `personal` every account is an administrator, whatever the
    # (unrendered, so never submitted) checkbox says.
    is_superuser = (
        True if current_posture() == POSTURE_PERSONAL
        else form.cleaned_data["is_superuser"]
    )
    actor = principal_for_request(request)
    try:
        services.create_user(
            actor, username=form.cleaned_data["username"],
            password=form.cleaned_data["password"], is_superuser=is_superuser,
        )
    except services.ServiceRefused as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, f"Created {form.cleaned_data['username']!r}.")
    return settings_redirect(request, "identity-users")


@require_admin
def user_edit(request, pk):
    """One `action` field, dispatched to the matching
    `identity.services` function. 404 for an unknown user id; an unknown
    action is a flash and a redirect back to the Accounts page -- a form
    that quietly did nothing would read as success, and a raw 400 would
    dump the operator out of the settings chrome to be told so."""
    user = get_object_or_404(User, pk=pk)
    action = request.POST.get("action", "")
    if action not in _USER_ACTIONS:
        # F7 (Coherence Wave C, carried from Wave B): flash-and-redirect,
        # not a raw 400 -- the house convention every other mutation on
        # this platform uses (`agents/chat/views/tools.py:103-111`,
        # `agents/chat/views/access.py:113-119`), never a bare plain-text
        # page with no shell, no sidebar and no flash. Still not a silent
        # no-op: the flash says exactly what the old 400 body said, so an
        # operator whose form submitted a stale or tampered action still
        # learns why nothing happened -- they just are not dumped out of
        # the chrome to read it. STATED ONCE HERE (N2, Wave C review) for
        # the three dispatchers in this module and the one in
        # `models/registry/views.py::model_set_edit`, which cross-refer
        # to it rather than carrying a fourth copy to keep in step.
        messages.error(request, f"{action!r} is not a recognised action.")
        return settings_redirect(request, "identity-users")

    actor = principal_for_request(request)

    if action == "set_password":
        # `identity.services.set_password`
        # runs no validation of its own (a plain reset, not a policy
        # check), and `AbstractBaseUser.set_password("")` happily
        # produces a USABLE, EMPTY password. Validated here, through
        # the same Django validators `UserCreateForm` runs on account
        # creation, BEFORE the service is ever called.
        form = SetPasswordForm(request.POST, target_user=user)
        if not form.is_valid():
            _flash_form_errors(request, form)
            return settings_redirect(request, "identity-users")
        try:
            services.set_password(actor, user, form.cleaned_data["password"])
        except services.ServiceRefused as exc:
            messages.error(request, str(exc))
        else:
            messages.info(request, f"Reset the password for {user.username!r}.")
        return settings_redirect(request, "identity-users")

    try:
        if action == "deactivate":
            counts = services.deactivate_user(actor, user)
            owned = ", ".join(f"{label}: {count}" for label, count in counts.items() if count)
            if owned:
                # Only mention `reassign_owner`
                # when there is something to reassign -- a member with
                # nothing owned is not told to run a command against an
                # empty set.
                messages.info(
                    request,
                    f"Deactivated {user.username!r}. Owned rows to reassign "
                    f"({owned}) -- run `manage.py reassign_owner` to move "
                    "them to another account.",
                )
            else:
                messages.info(request, f"Deactivated {user.username!r}.")
        elif action == "reactivate":
            services.reactivate_user(actor, user)
            messages.info(request, f"Reactivated {user.username!r}.")
        elif action == "promote":
            services.set_superuser(actor, user, True)
            messages.info(request, f"{user.username!r} is now an administrator.")
        elif action == "demote":
            services.set_superuser(actor, user, False)
            messages.info(request, f"{user.username!r} is no longer an administrator.")
    except services.ServiceRefused as exc:
        messages.error(request, str(exc))
    return settings_redirect(request, "identity-users")


# --- the posture page ------------------------------------------------------

@require_admin
def settings_page(request):
    """Posture, library posture, session window, and the
    administrator-content toggle -- one `PostureForm`, bound to
    `identity.services.set_posture`.

    A `ServiceRefused` -- the three switch-away-from-open refusals, or
    an unknown posture/library-posture value -- is reported with
    `messages.error`, and so is a form-invalid POST; either way this
    redirects back to the same page rather than re-rendering it, the
    same redirect-and-flash shape its sibling `groups` below uses (and
    every other mutation in this codebase, `tools/rag/views.py`) --
    never a 500 and never a silent no-op. (F7, Coherence Wave B: a
    form-invalid POST used to fall through to a RE-RENDER instead, a
    docstring/behaviour mismatch this fixes along with the behaviour --
    the prior wording here claimed the redirect shape while the code
    two lines below it did something else.)

    H22 review round 1 (F-1/D-1): `debug` is passed alongside `posture`
    so the template can echo `identity.W003` on the one page an
    operator is looking at when they might act on it -- a boot-time
    `manage.py check` warning is invisible to anybody who never reads
    server logs.
    """
    row = IdentitySettings.get_solo()
    if request.method == "POST":
        form = PostureForm(request.POST)
        if form.is_valid():
            actor = principal_for_request(request)
            try:
                services.set_posture(actor, **form.cleaned_data)
            except services.ServiceRefused as exc:
                messages.error(request, str(exc))
            else:
                messages.info(request, "Settings saved.")
            return settings_redirect(request, "identity-settings")
        # F7 (Coherence Wave B): REDIRECTS now, like `groups` below --
        # `_flash_form_errors` (consolidation round 3) added the flash
        # but left the fall-through-to-render shape underneath it
        # unchanged, so a refresh after an invalid submit used to re-POST
        # the same bad data instead of a clean GET.
        _flash_form_errors(request, form)
        return settings_redirect(request, "identity-settings")
    else:
        form = PostureForm(initial={
            "posture": row.posture,
            "library_posture": row.library_posture,
            "admin_sees_content": row.admin_sees_content,
            "session_idle_minutes": row.session_idle_minutes,
        })
    return render(request, "identity/settings.html", {
        "form": form, "posture": row.posture, "debug": settings.DEBUG,
    })


# --- the groups page -------------------------------------------------------

_GROUP_ACTIONS = ("delete", "add_member", "remove_member")


@require_admin
def groups(request):
    """GET lists every group with its members and its grants; POST
    creates one. Membership and deletion are `group_edit` below.

    `auth.Group` UNCHANGED -- this platform uses groups for membership
    only and never reads `Group.permissions`, so nothing here subclasses
    Django's own model. Its admin is unregistered (H40,
    `identity/admin.py`), which makes this page the one door to a
    group's create and delete, and the only one that audits them.
    """
    if request.method == "POST":
        form = NameForm(request.POST)
        if not form.is_valid():
            _flash_form_errors(request, form)
            return settings_redirect(request, "identity-groups")
        try:
            services.create_group(principal_for_request(request),
                                  name=form.cleaned_data["name"])
        except services.ServiceRefused as exc:
            messages.error(request, str(exc))
        else:
            messages.info(request, f"Created {form.cleaned_data['name']!r}.")
        return settings_redirect(request, "identity-groups")
    return render(request, "identity/groups.html", {
        "groups": Group.objects.prefetch_related("user_set", "entitlement_grants__entitlement")
                               .order_by("name"),
        "users": User.objects.filter(is_active=True).order_by("username"),
        "form": NameForm(),
    })


@require_admin
def group_edit(request, pk):
    """POST-only. One `action` field, dispatched. 404 for an unknown
    group; an unknown action is a flash and a redirect back to the Groups
    page -- a form that quietly did nothing would read as success, and a
    raw 400 would dump the operator out of the settings chrome to be told
    so."""
    group = get_object_or_404(Group, pk=pk)
    action = request.POST.get("action", "")
    if action not in _GROUP_ACTIONS:
        # F7's flash-and-redirect, for the reason stated in full at
        # `identity/views.py::user_edit` (N2, Wave C review).
        messages.error(request, f"{action!r} is not a recognised action.")
        return settings_redirect(request, "identity-groups")
    actor = principal_for_request(request)
    try:
        if action == "delete":
            services.delete_group(actor, group)
            messages.info(request, f"Deleted {group.name!r}.")
            return settings_redirect(request, "identity-groups")
        user = get_object_or_404(User, pk=_int_or_404(request.POST.get("user", "")))
        if action == "add_member":
            services.add_group_member(actor, group, user)
            messages.info(request, f"Added {user.username!r} to {group.name!r}.")
        else:
            services.remove_group_member(actor, group, user)
            messages.info(request, f"Removed {user.username!r} from {group.name!r}.")
    except services.ServiceRefused as exc:
        messages.error(request, str(exc))
    return settings_redirect(request, "identity-groups")


def _int_or_404(raw: str) -> int:
    """A POST body's integer, or a 404.

    NOT a bare `int(raw)`: the value arrives from a form body, so a
    non-numeric one would raise `ValueError` inside the view -- a 500 on
    a never-500 surface, reachable by anybody who can post a form.
    """
    if not str(raw).isdecimal():
        raise Http404("no such row")
    return int(raw)


# --- the entitlements pages -----------------------------------------------

# THE DECLARED VOCABULARY of this page's POST, cited by name from two
# other columns (`agents/visibility.py`, `agents/chat/views/
# workstreams.py`) as the shape their own dispatchers copy. It is the
# SINGLE GATE: `_entitlement_action` tests membership here first and
# dispatches `axis` only after this tuple has admitted it, so no entry
# is decorative and an action missing from it is refused rather than
# silently doing nothing.
_ENTITLEMENT_ACTIONS = ("rename", "delete", "grant", "revoke", "set_role", "axis")


@require_admin
def entitlements(request):
    """GET lists every entitlement with its full reach; POST creates one.
    ADMINISTRATOR ONLY -- an owner of one entitlement has no standing to
    mint another, so this page is class S in full.

    THE MANAGE-ALL SCREEN. A box runs to fifty entitlements, so the list
    has to answer two questions at a glance: which one am I looking for
    (`?q=`, a plain GET filter over name and description, so the page
    stays one shareable URL per state and the back button works), and
    what does each one actually touch (one reach column per registered
    axis, plus the grant count).

    THE QUERY COUNT IS FLAT IN THE NUMBER OF ROWS, and that is the whole
    design of `identity.axes.reach_by_entitlement`: one aggregate per
    axis over the whole join table, folded to a dict, rather than five
    counts per row. Pinned by an equality test at one entitlement versus
    thirty (`identity/tests/test_entitlement_pages.py`), because this is
    exactly the shape that reads fine at three rows and falls over at
    fifty.
    """
    if request.method == "POST":
        form = NameForm(request.POST)
        if not form.is_valid():
            _flash_form_errors(request, form)
            return _entitlements_redirect(request)
        try:
            services.create_entitlement(
                principal_for_request(request), name=form.cleaned_data["name"],
                description=form.cleaned_data["description"])
        except services.ServiceRefused as exc:
            messages.error(request, str(exc))
        else:
            messages.info(request, f"Created {form.cleaned_data['name']!r}.")
        return _entitlements_redirect(request)

    query = (request.GET.get("q") or "").strip()
    # `.order_by("name")` EXPLICITLY, and it is a fix rather than a
    # flourish: `.annotate()` with an aggregate CLEARS a model's
    # `Meta.ordering` (Django drops it so the implicit GROUP BY cannot
    # be wrong), so this list has been coming back in whatever order
    # PostgreSQL happened to return rows ever since the grant count was
    # added. Unnoticed at three entitlements; unusable at fifty, which
    # is what this page is now for.
    found = Entitlement.objects.annotate(grant_count=Count("grants")).order_by("name")
    if query:
        # NAME OR DESCRIPTION, case-insensitively: an operator looking
        # for the finance entitlement may remember what it is FOR rather
        # than what it is called, and the description column is where
        # that sentence already lives.
        found = found.filter(Q(name__icontains=query)
                             | Q(description__icontains=query))
    rows = list(found)
    reach = reach_by_entitlement([row.pk for row in rows])
    return render(request, "identity/entitlements.html", {
        # ZIPPED HERE, not in the template: `{{ row.reach }}` is a plain
        # list per row in the same order as `axis_labels`, so the table
        # body is one nested loop and the heading order and the cell
        # order cannot drift apart.
        "rows": [{"entitlement": row, "reach": reach[row.pk]} for row in rows],
        "axis_labels": axis_labels(),
        "q": query,
        "form": NameForm(),
    })


def _entitlements_redirect(request):
    """Back to the list, keeping the operator's search.

    `q` RIDES THE POST BODY, not the form's `action`. Every POST form on
    a settings page must carry `action="...?assistant=1"` and nothing
    else (`identity/tests/test_route_matrix.py`'s sweep asserts the
    action ENDS with it), so a second query parameter cannot go there --
    and a create that dumped the operator back to an unfiltered list
    would lose the search they were working in.
    """
    query = (request.POST.get("q") or request.GET.get("q") or "").strip()
    url = reverse("identity-entitlements")
    if query:
        url = f"{url}?{urlencode({'q': query})}"
    return settings_redirect(request, url)


def entitlement_edit(request, pk):
    """One entitlement: its description, its grants, and the forms that
    change them.

    NOT `@require_admin`. This is the ONE identity page an entitlement
    OWNER may reach, which is why `identity/routes.py` classifies it `R`
    rather than `S` (spec section 11.3, and this plan's decision 5): the
    middleware admits any signed-in caller and the rule lives here.

    404, NEVER 403, for a caller with no standing over this row -- a 403
    would confirm that an entitlement with this id exists, which is
    exactly the enumeration the class-R rule exists to prevent. Rename
    and delete are refused by `identity.services` with a readable message
    rather than by this view, so the owner sees WHY rather than a bare
    refusal.
    """
    # ONE `IdentitySettings` READ for the whole request -- `row` is
    # `IdentityGateMiddleware`'s own already-fetched row
    # (`identity.request.settings_row_for`) -- threaded through
    # `principal_for_request`, `is_admin` and `owned_entitlement_ids`
    # below rather than each re-fetching the singleton off its own
    # no-argument form. The same per-request-reuse norm `identity.access`
    # documents (`sees_all_content`'s docstring). Round-2 review FIX 3:
    # this used to call `is_admin(principal)` three times and
    # `owned_entitlement_ids` once, none of them sharing a row -- and
    # `principal_for_request` itself re-read the singleton a second time
    # by being called with no `settings_row` at all.
    row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=row)
    entitlement = get_object_or_404(Entitlement, pk=pk)
    admin = is_admin(principal, settings_row=row)
    if not (admin or entitlement.pk in owned_entitlement_ids(principal, settings_row=row)):
        raise Http404("no such entitlement")

    if request.method == "POST":
        return _entitlement_action(request, principal, entitlement)

    return render(request, "identity/entitlement.html", {
        "entitlement": entitlement,
        "grants": entitlement.grants.select_related("user", "group").all(),
        # `grant_subjects`, NOT `share_subjects`: a grant form must offer
        # the caller themselves. An administrator with the content setting
        # off reads nothing labelled, so granting themselves the
        # entitlement is the only way for them to read a document under
        # it -- and `share_subjects` excludes the caller, because sharing
        # a row with yourself IS a no-op.
        "subjects": grant_subjects(principal),
        # RENDERED, not dead weight: the template's role `<select>` comes
        # from `{{ grant_form.role }}` (round-2 review FIX 2), the same
        # form-owned-field norm `groups.html`/`entitlements.html` already
        # follow, so the member/owner vocabulary lives in this one form's
        # `choices` and nowhere else. The subject `<select>` stays
        # hand-written in the template -- it is built from `grant_subjects`
        # data, which no `Form` field expresses.
        "grant_form": GrantForm(initial={"role": "member"}),
        # ONLY WHEN THEY WILL BE RENDERED. `entitlement_delete_counts`
        # runs every registered cross-column cascade in count mode -- two
        # extra queries into two other columns -- and the template shows
        # them inside `{% if is_admin %}`. Computing them for an owner
        # who will never see them is work with no reader.
        "delete_counts": services.entitlement_delete_counts(entitlement) if admin else {},
        # COMPUTED FOR EVERY CALLER, unlike `delete_counts`: an
        # entitlement OWNER reaching this page is exactly somebody who
        # should see what their entitlement covers (spec section
        # 22.34's reach panel), and `entitlement_reach` IS
        # `entitlement_delete_counts` -- the same cascade-registry read,
        # so the two can never disagree.
        "reach": services.entitlement_reach(entitlement),
        # ONE TRANSFER PANEL PER EDITABLE AXIS, rendered from the
        # registry generically: a sixth labelled kind later is a
        # REGISTRATION in that column's own `apps.py`, not an edit to
        # this view and not a new template block.
        #
        # ADMINISTRATOR ONLY, like `delete_counts` above and for a
        # sharper version of the same reason. An entitlement OWNER's
        # three capabilities are grants, revocations and DOCUMENT labels
        # (spec section 7.4); a tool, an agent, a flow and a model set
        # are box inventory. `identity.services.set_entitlement_axis`
        # refuses an owner outright, and a page must not offer a control
        # that answers "no" -- so the panels are not merely hidden here,
        # they are never built.
        "axis_panels": axis_panels(entitlement.pk) if admin else [],
        # A DOOR PER AXIS THAT NAMES ONE, FROM THE REGISTRY (fix round
        # 2, P2-I1). An axis counted here but edited on its own column's
        # page -- document labels today -- registers a `link`, and this
        # renders the heading, the sentence and the URL that column
        # supplied. It used to be a hand-written section plus a
        # `reverse("rag-documents")` right here, which was the only
        # place in this column naming another column's route and made
        # the claim eleven lines above it false.
        #
        # FOR EVERY CALLER, unlike the panels: labelling documents is an
        # entitlement owner's own capability, so the library door is as
        # much theirs as an administrator's.
        "axis_doors": axis_doors(entitlement.pk),
        "is_admin": admin,
    })


def _entitlement_action(request, principal, entitlement):
    """The POST half of `entitlement_edit`, split out so the GET path
    reads as a page rather than as the tail of a dispatcher."""
    action = request.POST.get("action", "")
    back = redirect("identity-entitlement-edit", pk=entitlement.pk)
    if action not in _ENTITLEMENT_ACTIONS:
        # F7's flash-and-redirect, for the reason stated in full at
        # `identity/views.py::user_edit` (N2, Wave C review).
        messages.error(request, f"{action!r} is not a recognised action.")
        return back
    if action == "axis":
        # BELOW THE MEMBERSHIP TEST, deliberately (fix round 1, M4). Its
        # own function rather than a sixth branch -- it is the only
        # action landing back on an ANCHOR rather than the page top, and
        # the only one whose refusals are two registries deep -- but it
        # is dispatched only AFTER `_ENTITLEMENT_ACTIONS` has admitted
        # it, so that tuple is the single gate it claims to be. Read the
        # other way round, `"axis"` in the tuple was decorative and
        # quietly load-bearing: inline this branch below one day and an
        # action missing from the tuple would fall through every `elif`
        # to a bare `return back` -- a POST that silently does nothing,
        # which is the failure mode F7 exists to prevent.
        return _entitlement_axis_action(request, principal, entitlement)
    try:
        if action == "rename":
            services.rename_entitlement(principal, entitlement,
                                        request.POST.get("name", ""))
            messages.info(request, "Renamed.")
        elif action == "delete":
            counts = services.delete_entitlement(principal, entitlement)
            removed = ", ".join(f"{label}: {n}" for label, n in counts.items())
            messages.info(request, f"Deleted {entitlement.name!r} ({removed}).")
            return settings_redirect(request, "identity-entitlements")
        elif action == "grant":
            form = GrantForm(request.POST)
            if not form.is_valid():
                _flash_form_errors(request, form)
                return back
            kind, subject_pk = form.cleaned_data["subject"]
            target = (get_object_or_404(User, pk=subject_pk) if kind == "user"
                      else get_object_or_404(Group, pk=subject_pk))
            services.grant(principal, entitlement,
                           user=target if kind == "user" else None,
                           group=target if kind == "group" else None,
                           role=form.cleaned_data["role"])
            messages.info(request, "Granted.")
        else:
            row = _grant_of(entitlement, request.POST.get("grant", ""))
            if action == "revoke":
                services.revoke(principal, row)
                messages.info(request, "Revoked.")
            else:
                services.set_grant_role(principal, row, request.POST.get("role", ""))
                messages.info(request, "Role changed.")
    except services.ServiceRefused as exc:
        messages.error(request, str(exc))
    return back


_AXIS_OPERATIONS = ("add", "remove")


def _entitlement_axis_action(request, principal, entitlement):
    """`action=axis`: move rows of one registered axis on or off this
    entitlement, and land back on the panel that was edited.

    ONE FORM SPANS BOTH PANES, so a submit carries every ticked box in
    BOTH of them; the button pressed (`op`) says which side to honour.
    Honouring only that side is what stops a stray tick in the other
    pane -- left over from a filter, or from a mis-click -- from acting.

    THE ANCHOR IS THE POINT OF THE REDIRECT. An entitlement with five
    axes is a long page, and a save that returned an operator to the top
    of it would make every edit cost a scroll. `#axis-<key>` is the id
    `_transfer_panel.html` gives its own section, and it is appended
    only for an axis that actually RENDERS one -- an unrecognised key,
    and a registered-but-counted-only axis such as document labels,
    both land on the page top with the refusal flashed, rather than on a
    fragment identifier this page never emits.

    F7'S FLASH-AND-REDIRECT for every refusal (`identity/views.py::
    user_edit` has the reasoning in full): a tampered `op`, an
    unregistered axis, an id outside that axis's own catalogue, and an
    owner who is not an administrator all read back as a sentence on the
    page they came from, never a bare 400.
    """
    key = request.POST.get("axis", "")
    # `spec.editable`, NOT merely "registered" (fix round 1, M3): a
    # COUNTED-ONLY axis is registered -- `axis_for` answers for it --
    # but renders no panel, so anchoring a refusal at `#axis-rag.
    # documents` would send the operator to a fragment identifier this
    # page never emits. Only an axis with a panel has an anchor to land
    # on; everything else lands on the page top with the refusal flashed.
    spec = axis_for(key)
    anchor = f"#axis-{key}" if spec is not None and spec.editable else ""
    back = settings_redirect(
        request,
        reverse("identity-entitlement-edit", args=[entitlement.pk]) + anchor)
    operation = request.POST.get("op", "")
    if operation not in _AXIS_OPERATIONS:
        messages.error(request, f"{operation!r} is not a recognised operation.")
        return back
    submitted = set(request.POST.getlist(operation))
    try:
        label, summary = services.set_entitlement_axis(
            principal, entitlement, key,
            add=submitted if operation == "add" else set(),
            remove=submitted if operation == "remove" else set())
    except (services.ServiceRefused, AxisRefused) as exc:
        messages.error(request, str(exc))
        return back
    messages.info(
        request,
        f"{label}: {summary['added']} added, {summary['removed']} removed.")
    return back


def _grant_of(entitlement, raw: str) -> EntitlementGrant:
    """The grant `raw` names, PROVEN to belong to `entitlement`, or 404.

    TWO REFUSALS, both 404, and neither is optional. `raw` arrives from a
    POST body, so a non-numeric value would reach `get(pk=...)` and raise
    `ValueError` -- a 500 on a never-500 surface. And without the
    belongs-to check, an owner of Finance could revoke a Legal grant by
    guessing a sequential id: an IDOR that reads as a legitimate action
    in the audit log. 404 for both, never 403, because a 403 would
    confirm that some other entitlement's grant carries that id.
    """
    return get_object_or_404(
        EntitlementGrant.objects.select_related("entitlement", "user", "group"),
        pk=_int_or_404(raw), entitlement=entitlement)
