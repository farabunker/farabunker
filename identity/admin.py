"""The break-glass user surface -- routed through the same guards the
pages use.

THE ONLY `admin.py` IN THE REPOSITORY. The spec (§15, "Admin surfaces")
keeps `/admin/` a superuser-only break-glass tool, not a product
surface. Nothing new is registered here beyond the user model, and
`AuditEvent` is deliberately NOT registered -- an append-only table
with a delete button is not append-only.
"""
from __future__ import annotations

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.http import HttpResponseRedirect

from identity import services
from identity.access import posture as current_posture
from identity.contracts.actions import SOURCE_ADMIN
from identity.contracts.postures import POSTURE_PERSONAL
from identity.models import User
from identity.request import principal_for_request

# A-2: `auth.Group` IS NOT REACHABLE FROM THE ADMIN. The reason is not
# `Group.permissions` -- those genuinely are inert here, which is what the
# previous comment said and why this door stayed open. It is the two
# reverse foreign keys THIS PLATFORM hung off the model: deleting a group
# cascades away every entitlement grant and every share whose subject was
# that group, and the admin's delete writes none of the audit rows
# `identity.services` writes for the identical change. The identity page
# is the one door, and it audits.
admin.site.unregister(Group)


@admin.register(User)
class IdentityUserAdmin(DjangoUserAdmin):
    """Break-glass, routed through the same guards the pages use.

    `save_model` DELEGATES: a change to `is_superuser` goes through
    `identity.services.set_superuser`, a change to `is_active` through
    `deactivate_user`/`reactivate_user`, and a brand-new account
    through `create_user`, so the last-admin guard, its
    `select_for_update`, and the audit write all apply here exactly as
    they do on the users page. Everything else falls through to
    `super().save_model`. An admin form that could reach a protected
    column without its guard is a guard with a second door -- which is
    also why `has_delete_permission` is pinned False below (users are
    never deleted, see `identity.services.deactivate_user`'s own
    docstring), why the per-user password-change URL Django's own
    `UserAdmin` mounts is removed outright rather than left reachable,
    why `add_form` is swapped for plain `UserCreationForm` (see its own
    comment below), and why `is_staff` is
    read-only (see the comment on
    `readonly_fields` below).

    `user_permissions` is named by no `fieldsets` entry, so Django's
    per-user permission catalogue is not reachable from this surface at
    all -- a named non-goal, because it is model-level and would be a
    second grant mechanism beside entitlements. `filter_horizontal` is
    pinned EMPTY for the same reason: the only two fields it configures
    on a stock `UserAdmin` are `user_permissions` and `groups`, and this
    class offers neither (see its own comment below). `auth.Group` is unregistered outright (module level,
    above) rather than subclassed: the identity page is already the one
    door for a group's create and delete (groups have no rename
    primitive in `identity.services`), each writing the audit row
    `identity.services` writes for it, and the stock admin's own forms
    wrote none of them while also being able to cascade-delete every
    grant and share a group carried. A subclass that behaved would
    still be a second door to a guarded write -- which this module's own
    rule above already names as a guard that does not exist.
    """

    # The stock `UserAdmin.add_form` is
    # `AdminUserCreationForm`, which mixes in `SetUnusablePasswordMixin`
    # -- a `usable_password` radio ("Password-based authentication:
    # Enabled/Disabled") that makes `password1`/`password2` OPTIONAL so
    # the "Disabled" branch can skip them. A POST with
    # `usable_password=false` and a blank `password1` validated clean,
    # and `save_model` hashed the empty string via
    # `services.create_user`, producing an account anyone could sign
    # into with a blank password.
    #
    # Swapped for plain `django.contrib.auth.forms.UserCreationForm`
    # rather than honouring `set_usable_password`: this platform has no
    # alternate authentication backend (no SSO, no LDAP --
    # `identity.User` carries no extra fields, by its own docstring), so
    # "password-based authentication disabled" would not be a safer
    # account, it would be an UNREACHABLE one. Plain `UserCreationForm`
    # has no `usable_password` field at all, keeps `password1`/
    # `password2` REQUIRED, and runs Django's own validators (length,
    # commonness, similarity to the username, all-numeric) through
    # `validate_password_for_user` -- the same validators
    # `identity.forms.UserCreateForm` runs for the users page's own
    # create form. `add_fieldsets` below already names exactly this
    # form's three fields (`username`, `password1`, `password2`), so no
    # template or fieldset change was needed alongside this swap.
    add_form = UserCreationForm

    # ONE MECHANISM, not two, for `user_permissions`. `fieldsets` and
    # `add_fieldsets` are declared in full here and neither names it, so
    # the field is not on the form at all -- there is nothing for a
    # `readonly_fields` entry to protect it FROM, and carrying one as
    # well would leave a reader unsure which of the two was
    # load-bearing. `test_admin.py` asserts its absence against BOTH the
    # rendered page and `get_fieldsets()`, so a Django upgrade that
    # reintroduced it fails there rather than being silently absorbed by
    # a second guard.
    #
    # `is_staff`, by contrast, DOES need `readonly_fields`: it stays in
    # `fieldsets` below (visible, so an operator can see whether an
    # account can reach `/admin/`'s own permission gate) but must not be
    # independently editable. `identity.services.create_user` and
    # `set_superuser` both keep it a MIRROR of `is_superuser`
    # (`is_staff=is_superuser`) -- there is no independent "staff, not
    # superuser" role on this platform. Before this fix, a change-form
    # POST that touched `is_staff` alone (leaving `is_superuser`
    # unchanged) fell straight through to `super().save_model`'s bare
    # `obj.save()`: no guard, no audit row, and a state
    # `identity.services` never produces on its own. `save_model`'s own
    # existing superuser branch already keeps `is_staff` synced
    # (`obj.is_staff = before.is_staff`) whenever `is_superuser`
    # changes, so a service call OF ITS OWN for `is_staff` would just be
    # a second definition of the same rule.
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    readonly_fields = ("is_staff",)
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("username", "password1", "password2")}),
    )
    # EMPTY, not absent. This used to read `("groups",)`, which was dead
    # -- `fieldsets` above names no `groups` field for the widget to
    # widen -- but simply deleting the line does not leave the class
    # without one: `DjangoUserAdmin.filter_horizontal` is
    # `("groups", "user_permissions")`, so the attribute would be
    # inherited naming two fields this admin deliberately renders on no
    # fieldset at all. An explicit empty tuple says the thing the class
    # means, and `test_admin_doors.py` asserts it.
    filter_horizontal = ()

    def get_urls(self):
        """Drop `<id>/password/` (`auth_user_password_change`).

        `DjangoUserAdmin.get_urls` mounts it pointing at
        `user_change_password`, which calls `form.save()` on an
        `AdminPasswordChangeForm` DIRECTLY -- no
        `identity.services.set_password` call and so no audit row.
        REMOVED rather than routed through the service: the users page
        already resets a password through `set_password` with its own
        audit row, and duplicating Django's own password-change-form
        behaviour here (its `set_usable_password`/`unset-password`
        checkbox, its own `log_change` entry, its own redirect) would
        be a second, partial copy of that flow for no functional gain.
        The `ReadOnlyPasswordHashField` widget links to this URL with a
        plain relative `../password/` href, not a `{% url %}` reverse
        lookup, so removing the route 404s a click through that link
        rather than raising anywhere the change form itself renders.
        """
        return [u for u in super().get_urls() if u.name != "auth_user_password_change"]

    def has_delete_permission(self, request, obj=None):
        """USERS ARE NEVER DELETED (`identity.services.deactivate_
        user`'s own docstring) -- deactivation is the only lifecycle
        off-ramp this platform offers. Without this, `delete_model` and
        the built-in `delete_selected` action would reach `obj.delete()`
        directly: no last-admin guard, no owned-row accounting, and no
        audit row. Django's own `get_actions` already drops
        `delete_selected` from the change-list actions whenever this
        answers False, so nothing else needs overriding for that half.
        """
        return False

    def save_model(self, request, obj, form, change):
        actor = principal_for_request(request)
        if not change:
            # ADD: routed through `services.create_user`, so an account
            # created from this surface writes the same
            # `identity.user_created` audit row the users page does,
            # instead of `ModelAdmin.save_model`'s bare `obj.save()`.
            # `form.cleaned_data["password1"]` is the PLAINTEXT the add
            # form validated -- `obj.password` is already hashed by
            # `BaseUserCreationForm.save(commit=False)` (`save_form`
            # runs before `save_model`), and `create_user` hashes its
            # own `password` argument, so passing the hash through
            # would hash it twice and lock the account out immediately.
            #
            # `add_fieldsets` has no
            # `is_superuser` field at all, so `obj.is_superuser` is
            # always the model default (False) coming out of the add
            # form -- creating a MEMBER even in the `personal` posture,
            # where the users page's own create form (`identity.views.
            # user_create`) makes every new account an administrator.
            # Same rule, applied here too.
            is_superuser = (
                True if current_posture() == POSTURE_PERSONAL else obj.is_superuser
            )
            new_user = services.create_user(
                actor, username=obj.username,
                password=form.cleaned_data["password1"],
                is_superuser=is_superuser, source=SOURCE_ADMIN,
            )
            # `obj` IS the `new_object` `ModelAdmin._changeform_view`'s
            # add branch reads `.pk` off afterwards, for `log_addition`
            # and `response_add`'s own redirect -- a `None` pk there is
            # a `NoReverseMatch` inside that redirect. Copy the saved
            # row's identity onto it rather than saving `obj` itself a
            # second time.
            obj.pk = new_user.pk
            obj.password = new_user.password
            obj.is_superuser = new_user.is_superuser
            obj.is_staff = new_user.is_staff
            return
        before = User.objects.get(pk=obj.pk)
        # The two guarded columns first, through their services, so a
        # refusal happens BEFORE anything else on the form is written.
        # `is_staff` is `readonly_fields`-pinned above, so `obj.is_staff`
        # never differs from `before.is_staff` on entry here -- the
        # assignment below only keeps it in sync with a CHANGED
        # `is_superuser`, it never independently sets it.
        if before.is_superuser != obj.is_superuser:
            services.set_superuser(actor, before, obj.is_superuser, source=SOURCE_ADMIN)
            obj.is_superuser = before.is_superuser
            obj.is_staff = before.is_staff
        if before.is_active != obj.is_active:
            if obj.is_active:
                services.reactivate_user(actor, before, source=SOURCE_ADMIN)
            else:
                services.deactivate_user(actor, before, source=SOURCE_ADMIN)
            obj.is_active = before.is_active
        super().save_model(request, obj, form, change)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """`services.ServiceRefused` propagating out of `save_model`
        would be a 500 on `/admin/`. Caught here, reported the same way
        Django itself reports a save error (`self.message_user`, ERROR
        level), and the change form is served again -- the base
        `changeform_view` wraps the whole POST in
        `transaction.atomic()`, so a refusal here has already rolled
        back every write this request attempted, exactly like every
        other refusal on this box.
        """
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except services.ServiceRefused as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return HttpResponseRedirect(request.path)
