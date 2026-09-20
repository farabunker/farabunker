"""`/admin/` after the user-model swap.

Django's `AdminSite.register` silently ignores a swapped-out model, so
`django.contrib.auth.admin`'s registration became inert and `/admin/`
would show Groups only. This re-registers the user model -- but NOT with
the stock `UserAdmin`, which would open two holes at once: its
`save_model` calls `obj.save()` directly, bypassing the last-admin
guard, and its fieldsets expose the `Permission` catalogue, which is a
named non-goal.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_PERSONAL
from identity.models import AuditEvent, User
from identity.tests._helpers import make_admin, make_user, posture, sign_in

pytestmark = pytest.mark.django_db


def _admin_form(user, **overrides) -> dict:
    """A COMPLETE change-form body.

    `UserAdmin`'s fieldsets include `date_joined` and `last_login`, and
    Django renders each as an `AdminSplitDateTime` -- two inputs,
    `<name>_0` (date) and `<name>_1` (time). `date_joined` is REQUIRED,
    so a body that omits it re-renders the form with a validation error
    and `save_model` is never called -- which would make every
    assertion below pass by never reaching the code they are about.

    Checkboxes are omitted rather than sent as "off": Django reads an
    absent checkbox as False, and sending the string "off" would be
    read as TRUE.
    """
    fields = {
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "date_joined_0": user.date_joined.strftime("%Y-%m-%d"),
        "date_joined_1": user.date_joined.strftime("%H:%M:%S"),
    }
    if user.last_login:
        fields["last_login_0"] = user.last_login.strftime("%Y-%m-%d")
        fields["last_login_1"] = user.last_login.strftime("%H:%M:%S")
    fields.update(overrides)
    return fields


class TestBreakGlassGoesThroughTheSameGuards:
    def test_demoting_the_last_superuser_through_the_form_is_refused(self, client):
        """The same message the page gives. An admin form that could
        reach a protected column without its guard is a guard with a
        second door."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(
                reverse("admin:identity_user_change", args=[admin.pk]),
                _admin_form(admin, is_active="on"), follow=True,
            )
        admin.refresh_from_db()
        assert admin.is_superuser is True
        assert "last active administrator" in response.content.decode()

    def test_deactivating_through_the_form_writes_the_audit_row(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("admin:identity_user_change", args=[member.pk]),
                        _admin_form(member), follow=True)
        member.refresh_from_db()
        assert member.is_active is False
        assert AuditEvent.objects.filter(action=actions.USER_DEACTIVATED).exists()

    def test_creating_a_user_through_the_add_form_audits_a_creation(self, client):
        """`ModelAdmin.save_model`'s own bare `obj.save()` for a new row
        writes no `identity.user_created` row at all -- this surface
        must create through `identity.services.create_user`, exactly
        like the users page's own create form."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(
                reverse("admin:identity_user_add"),
                {"username": "ann", "password1": "a-real-enough-value",
                 "password2": "a-real-enough-value"},
                follow=True,
            )
        assert response.status_code == 200
        new_user = User.objects.get(username="ann")
        assert new_user.check_password("a-real-enough-value")
        assert new_user.is_superuser is False
        assert AuditEvent.objects.filter(
            action=actions.USER_CREATED, target_key=str(new_user.pk)).exists()

    def test_creating_through_the_add_form_in_personal_posture_makes_an_administrator(
            self, client):
        """T9 review round 2 bundle: the add form has no `is_superuser`
        field at all, so `obj.is_superuser` coming out of it is always
        the model default (False) -- this used to create a MEMBER even
        in the `personal` posture, contradicting the users page's own
        rule (`identity.views.user_create`) that every account there is
        an administrator."""
        admin = make_admin()
        with posture(POSTURE_PERSONAL):
            sign_in(client, admin)
            client.post(
                reverse("admin:identity_user_add"),
                {"username": "ann", "password1": "a-real-enough-value",
                 "password2": "a-real-enough-value"},
                follow=True,
            )
        new_user = User.objects.get(username="ann")
        assert new_user.is_superuser is True
        assert new_user.is_staff is True


class TestTheAddFormHasNoUnusablePasswordOffRamp:
    """T9 review round 2 IMPORTANT: the stock `UserAdmin.add_form`
    (`AdminUserCreationForm`) declares a `usable_password` radio and
    makes `password1`/`password2` OPTIONAL so its "Disabled" branch can
    skip them -- a POST with `usable_password=false` and a blank
    `password1` validated clean, and `save_model` hashed the empty
    string, producing an account anyone could sign into with a blank
    password. `identity/admin.py` swaps `add_form` for plain
    `django.contrib.auth.forms.UserCreationForm`, which has no such
    field and keeps `password1`/`password2` required."""

    def test_usable_password_false_creates_no_account_at_all(self, client):
        """The crafted payload a reviewer would send: `usable_password`
        is not a field on this form, so it is simply ignored, and the
        blank `password1`/`password2` fail Django's own required-field
        validation -- no account with a blank password is EVER
        reachable, because no account is created at all."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(
                reverse("admin:identity_user_add"),
                {"username": "eve", "usable_password": "false",
                 "password1": "", "password2": ""},
                follow=True,
            )
        assert not User.objects.filter(username="eve").exists()

    def test_an_empty_password_is_refused_with_usable_password_true_too(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(
                reverse("admin:identity_user_add"),
                {"username": "eve2", "usable_password": "true",
                 "password1": "", "password2": ""},
                follow=True,
            )
        assert response.status_code == 200
        assert not User.objects.filter(username="eve2").exists()

    def test_the_usable_password_field_is_not_on_the_add_form(self, client):
        """`django/contrib/admin/base.html` links a static
        `unusable_password_field.css` UNCONDITIONALLY, so the bare
        substring "usable_password" is not a safe check here -- the
        actual FORM CONTROL name attribute is."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("admin:identity_user_add")).content.decode()
        assert 'name="usable_password"' not in body


class TestIsStaffIsReadOnly:
    """T9 review round 2 bundle: `identity.services.create_user` and
    `set_superuser` both keep `is_staff` a MIRROR of `is_superuser` --
    there is no independent "staff, not superuser" role on this
    platform. Before this fix, a change-form POST touching `is_staff`
    ALONE (leaving `is_superuser` unchanged) fell straight through to
    `super().save_model`'s bare `obj.save()`: no guard, no audit row,
    and a state `identity.services` never produces on its own."""

    def test_it_cannot_be_set_independently_of_is_superuser(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(
                reverse("admin:identity_user_change", args=[member.pk]),
                _admin_form(member, is_active="on", is_staff="on"), follow=True,
            )
        member.refresh_from_db()
        assert response.status_code == 200
        assert member.is_staff is False
        assert member.is_superuser is False

    def test_it_has_no_editable_checkbox_on_the_change_form(self, client):
        from identity.admin import IdentityUserAdmin
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("admin:identity_user_change", args=[admin.pk])).content.decode()
        assert 'name="is_staff"' not in body
        assert "is_staff" in IdentityUserAdmin.readonly_fields


class TestUsersAreNeverDeleted:
    def test_delete_permission_is_always_false(self, client):
        """Users are never deleted (`identity.services.deactivate_
        user`'s own docstring) -- `delete_model`/`delete_selected`
        would reach `obj.delete()` directly, with no last-admin guard,
        no owned-row accounting and no audit row."""
        from identity.admin import IdentityUserAdmin
        assert IdentityUserAdmin(User, None).has_delete_permission(None) is False

    def test_the_delete_view_refuses_a_signed_in_administrator(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(
                reverse("admin:identity_user_delete", args=[admin.pk]),
                {"post": "yes"},
            )
        assert response.status_code == 403
        assert User.objects.filter(pk=admin.pk).exists()

    def test_delete_selected_is_not_offered_as_a_change_list_action(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("admin:identity_user_changelist")).content.decode()
        assert "delete_selected" not in body

    def test_the_per_user_password_change_url_is_removed(self, client):
        """`DjangoUserAdmin.get_urls` mounts `<id>/password/` ->
        `user_change_password`, which calls `form.save()` directly --
        bypassing `identity.services.set_password` and writing no audit
        row. Removed outright (see `identity/admin.py::get_urls`'s own
        docstring for why this over routing it through the service).

        `follow=True`: with the named route gone, `ModelAdmin`'s own
        backwards-compatibility catch-all (`<path:object_id>/` ->
        redirect to `<path:object_id>/change/`) matches the request
        path wholesale, with `object_id="{pk}/password"` -- no real
        user has that pk, so `change_view` bounces again, to the admin
        index. The exact bounce path is Django's own implementation
        detail; the proof that matters is that the chain never lands on
        a page offering `password1`/`password2`
        (`AdminPasswordChangeForm`'s own fields) -- i.e. it never
        reaches `user_change_password` at all."""
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.get(f"/admin/identity/user/{admin.pk}/password/", follow=True)
        assert response.status_code == 200
        body = response.content.decode()
        assert "password1" not in body
        assert "password2" not in body


class TestPermissionsAreUnreachable:
    def test_user_permissions_is_in_neither_the_fieldsets_nor_the_form(self, client):
        """Django's per-user permission catalogue is a second grant
        mechanism beside the entitlements IA-2 adds -- a named
        non-goal."""
        from identity.admin import IdentityUserAdmin
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("admin:identity_user_change", args=[admin.pk])).content.decode()
        assert "user_permissions" not in body
        flattened = str(IdentityUserAdmin.fieldsets)
        assert "user_permissions" not in flattened
