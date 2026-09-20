"""The guarded writes.

THE THREE `set_posture` REFUSALS ARE EXERCISED INDEPENDENTLY. A test
that switched with no admin AND `DEBUG` on would pass whichever
condition fired first and prove nothing about the other -- so each case
below satisfies the two conditions it is not testing.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from identity import services
from identity.contracts import actions
from identity.contracts.postures import (
    LIBRARY_LOCKED, LIBRARY_OPEN, POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
)
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.models import AuditEvent, EntitlementGrant, IdentitySettings
from identity.tests._helpers import (
    grant, make_admin, make_entitlement, make_group, make_user, posture, user_principal,
)

pytestmark = pytest.mark.django_db

REAL_KEY = "a-real-key-for-this-test-only"


@pytest.fixture(autouse=True)
def _a_real_key_and_no_debug(settings):
    """Two of the three `set_posture` conditions satisfied by default,
    so a test that does not care about them is not accidentally
    testing them."""
    settings.DEBUG = False
    settings.SECRET_KEY = REAL_KEY


@pytest.fixture
def _isolated_ownership_registry():
    """Snapshot and restore `identity.contracts.ownership`'s registry,
    matching `identity/tests/test_ownership.py`'s own fixture -- NOT
    autouse, because only the one test below that actually registers a
    real table needs it, and every other test in this module must see
    the registry other tasks' `AppConfig.ready()` methods have already
    populated by the time the suite runs, not an empty one."""
    from identity.contracts import ownership
    saved = dict(ownership._OWNED)
    ownership._OWNED.clear()
    yield
    ownership._OWNED.clear()
    ownership._OWNED.update(saved)


class TestCreateUser:
    def test_it_writes_the_row_and_one_audit_event(self):
        user = services.create_user(OPEN_PRINCIPAL, username="ann", password="s3cret-value")
        assert user.check_password("s3cret-value")
        assert AuditEvent.objects.filter(action=actions.USER_CREATED,
                                         target_key=str(user.pk)).count() == 1

    def test_it_never_records_the_password_anywhere_in_the_audit_row(self):
        user = services.create_user(OPEN_PRINCIPAL, username="ann", password="s3cret-value")
        row = AuditEvent.objects.get(action=actions.USER_CREATED, target_key=str(user.pk))
        assert "s3cret-value" not in f"{row.detail}{row.target_label}{row.actor_label}"

    def test_a_duplicate_username_is_a_refusal_not_an_integrity_error(self):
        services.create_user(OPEN_PRINCIPAL, username="ann", password="x1234567")
        with pytest.raises(services.ServiceRefused) as exc:
            services.create_user(OPEN_PRINCIPAL, username="ann", password="x1234567")
        assert "ann" in str(exc.value)


class TestSetPassword:
    def test_it_never_records_the_raw_password_anywhere_in_the_audit_row(self):
        admin, member = make_admin(), make_user()
        services.set_password(user_principal(admin), member, "a-new-s3cret-value")
        row = AuditEvent.objects.get(action=actions.PASSWORD_RESET,
                                     target_key=str(member.pk))
        assert "a-new-s3cret-value" not in f"{row.detail}{row.target_label}{row.actor_label}"

    def test_it_writes_one_audit_event(self):
        admin, member = make_admin(), make_user()
        services.set_password(user_principal(admin), member, "a-new-s3cret-value")
        assert AuditEvent.objects.filter(action=actions.PASSWORD_RESET,
                                         target_key=str(member.pk)).count() == 1
        assert member.check_password("a-new-s3cret-value")

    def test_an_existing_session_stops_working_after_a_reset(self, client):
        """Django invalidates a session on a password change through
        `AbstractBaseUser.get_session_auth_hash` -- the same fact
        `TestDeactivate` states for deactivation, and for the same
        reason no session-sweeping code is written here either."""
        admin = make_admin()
        member = make_user(username="bob", password="not-a-real-password")
        assert client.login(username="bob", password="not-a-real-password")
        services.set_password(user_principal(admin), member, "a-different-value")
        from django.contrib.auth import get_user
        request = type("R", (), {"session": client.session})()
        assert get_user(request).is_authenticated is False


class TestSetSuperuser:
    def test_promoting_and_demoting_each_write_their_own_action(self):
        admin, other = make_admin(), make_admin()
        services.set_superuser(user_principal(admin), other, False)
        assert AuditEvent.objects.filter(action=actions.SUPERUSER_REVOKED).exists()
        services.set_superuser(user_principal(admin), other, True)
        assert AuditEvent.objects.filter(action=actions.SUPERUSER_GRANTED).exists()

    def test_the_last_active_superuser_cannot_be_demoted(self):
        """One role, one column, and a box that can always be
        administered. The message names why."""
        admin = make_admin()
        with pytest.raises(services.ServiceRefused) as exc:
            services.set_superuser(user_principal(admin), admin, False)
        assert "last" in str(exc.value).lower()
        admin.refresh_from_db()
        assert admin.is_superuser is True

    def test_an_inactive_superuser_does_not_count_towards_the_guard(self):
        """"At least one ACTIVE superuser" is the invariant. A
        deactivated admin cannot log in, so it cannot be the one that
        keeps the box administrable."""
        live, dormant = make_admin(), make_admin(is_active=False)
        assert dormant.is_superuser and not dormant.is_active
        with pytest.raises(services.ServiceRefused):
            services.set_superuser(user_principal(live), live, False)

    def test_a_no_op_write_is_not_audited(self):
        """Setting a value to what it already is is not an event. An
        audit trail full of non-changes is a trail nobody reads."""
        admin, other = make_admin(), make_admin()
        before = AuditEvent.objects.count()
        services.set_superuser(user_principal(admin), other, True)
        assert AuditEvent.objects.count() == before


class TestDeactivate:
    def test_it_deactivates_and_audits_and_reports_owned_row_counts(
            self, _isolated_ownership_registry):
        """Owned rows are LEFT IN PLACE, and the counts come back so the
        operator knows to run `reassign_owner`. A deleted user with
        owned rows is an orphan nobody can reason about.

        A REAL registered table, not `isinstance(counts, dict)`: an
        empty dict is also a dict, and would pass that assertion
        whether or not `owned_row_counts` ever looked at a single row.
        """
        from agents.models import Conversation
        from agents.tests._helpers import make_agent
        from identity.contracts.ownership import OwnedRows, register_owned_rows

        register_owned_rows(
            OwnedRows("agents.conversation", "Conversations", "agents.Conversation"))
        admin, member = make_admin(), make_user()
        agent = make_agent()
        Conversation.objects.create(agent=agent, owner_kind="user",
                                    owner_key=str(member.pk))

        counts = services.deactivate_user(user_principal(admin), member)
        member.refresh_from_db()
        assert member.is_active is False
        assert counts["Conversations"] == 1
        assert AuditEvent.objects.filter(action=actions.USER_DEACTIVATED).exists()

        # The row SURVIVES deactivation -- it is left in place, not
        # deleted, and it still counts afterwards too.
        assert Conversation.objects.filter(
            owner_kind="user", owner_key=str(member.pk)).count() == 1
        assert services.owned_row_counts(str(member.pk))["Conversations"] == 1

    def test_the_last_active_superuser_cannot_be_deactivated(self):
        admin = make_admin()
        with pytest.raises(services.ServiceRefused):
            services.deactivate_user(user_principal(admin), admin)

    def test_deactivating_an_already_inactive_user_is_not_a_second_event(self):
        """IDEMPOTENT, like `reactivate_user`'s own early return: a
        second deactivation is not a second audit row."""
        admin, member = make_admin(), make_user(is_active=False)
        before = AuditEvent.objects.count()
        counts = services.deactivate_user(user_principal(admin), member)
        assert isinstance(counts, dict)
        assert AuditEvent.objects.count() == before

    def test_reactivation_is_the_same_action_in_reverse(self):
        admin, member = make_admin(), make_user(is_active=False)
        services.reactivate_user(user_principal(admin), member)
        member.refresh_from_db()
        assert member.is_active is True
        assert AuditEvent.objects.filter(action=actions.USER_REACTIVATED).exists()

    def test_a_deactivated_user_stops_being_authenticated_on_the_next_request(self, client):
        """SESSIONS DIE FOR FREE, and that is why no session-sweeping
        code is written: Django's `ModelBackend.get_user` calls
        `user_can_authenticate`, which is False for an inactive user, so
        `request.user` becomes anonymous on the very next request that
        presents the old cookie. A sweep on top would be a second,
        weaker copy of a mechanism Django maintains."""
        admin = make_admin()
        member = make_user(username="ann", password="not-a-real-password")
        assert client.login(username="ann", password="not-a-real-password")
        services.deactivate_user(user_principal(admin), member)
        from django.contrib.auth import get_user
        request = type("R", (), {"session": client.session})()
        assert get_user(request).is_authenticated is False


class TestSetPosture:
    def test_switching_to_personal_writes_one_column_and_one_audit_row(self):
        make_admin()
        row = services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
        assert row.posture == POSTURE_PERSONAL
        assert AuditEvent.objects.filter(action=actions.POSTURE_CHANGED).count() == 1

    def test_switching_rewrites_no_owner_column_and_no_user_row(self):
        """"A switch, not a migration": one column and one audit row,
        and nothing else moves."""
        admin = make_admin()
        before = (admin.username, admin.is_superuser, admin.is_active)
        services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_ENTERPRISE)
        admin.refresh_from_db()
        assert (admin.username, admin.is_superuser, admin.is_active) == before

    def test_it_refuses_with_no_active_superuser(self, settings):
        """Condition 1 ALONE: `DEBUG` off and a real key are supplied by
        the module fixture, so only the missing admin can be the
        reason."""
        make_user()                      # a member, not an admin
        with pytest.raises(services.ServiceRefused) as exc:
            services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
        assert "superuser" in str(exc.value).lower()
        assert IdentitySettings.get_solo().posture == POSTURE_OPEN

    def test_it_refuses_with_debug_on(self, settings):
        """Condition 2 ALONE: an active admin exists and the key is
        real. `DEBUG=True` renders tracebacks with settings and
        environment to any visitor, which is a disclosure a posture with
        accounts must not permit."""
        make_admin()
        settings.DEBUG = True
        with pytest.raises(services.ServiceRefused) as exc:
            services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
        assert "debug" in str(exc.value).lower()

    def test_the_debug_refusal_names_the_whole_runbook_not_one_condition(self, settings):
        """H22/F-1: the refusal's direction was always right -- it must
        keep refusing -- but naming only "DEBUG=0 and restart" leaves an
        operator one refusal short of a working box, since the same
        function also refuses with no active superuser. The message now
        points at the whole ordered runbook, not just the condition that
        happened to fire."""
        make_admin()
        settings.DEBUG = True
        with pytest.raises(services.ServiceRefused) as exc:
            services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
        message = str(exc.value)
        assert "DEBUG" in message
        assert "docs/OPERATIONS.md" in message      # the way out, not just the refusal

    def test_it_refuses_with_the_shipped_default_secret_key(self, settings):
        """Condition 3 ALONE. That key signs every session cookie; a box
        whose signing key is published in a public repository has
        accounts in name only, because anybody who can read the
        repository can mint a session for any account on it."""
        from config.settings import DEV_SECRET_KEY
        make_admin()
        settings.SECRET_KEY = DEV_SECRET_KEY
        with pytest.raises(services.ServiceRefused) as exc:
            services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
        assert "key" in str(exc.value).lower()
        # An .env change reaches a container only when the container is
        # recreated; "restart" named an operation that would leave the
        # old key in the running process. Pinned so the remedy this
        # message gives an operator stays the one that works.
        assert "recreate the containers" in str(exc.value)
        assert "restart" not in str(exc.value)

    @pytest.mark.parametrize("broken", ["no_admin", "debug", "key"])
    def test_switching_TO_open_is_refused_by_none_of_them(self, settings, broken):
        """Reducing a posture is always allowed. An operator whose box
        is misconfigured must always be able to make it LESS strict."""
        from config.settings import DEV_SECRET_KEY
        admin = make_admin()
        with posture(POSTURE_PERSONAL):
            if broken == "no_admin":
                admin.is_active = False
                admin.save()
            elif broken == "debug":
                settings.DEBUG = True
            else:
                settings.SECRET_KEY = DEV_SECRET_KEY
            services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_OPEN)
            assert IdentitySettings.get_solo().posture == POSTURE_OPEN

    def test_moving_to_personal_resets_a_locked_library_and_audits_the_reset(self):
        """`personal` renders no library-posture control, so a box
        arriving there from a locked `enterprise` would otherwise hold a
        lock with no page to lift it. THE ONE PLACE A POSTURE BRANCH
        LIVES, and it lives in a WRITE path on purpose -- the read paths
        in `identity/access.py` stay posture-free."""
        make_admin()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
            row = IdentitySettings.get_solo()
            assert row.library_posture == LIBRARY_OPEN
        assert AuditEvent.objects.filter(
            action=actions.LIBRARY_POSTURE_CHANGED).exists()

    def test_a_compound_switch_to_personal_ignores_an_explicit_library_posture(self):
        """A caller asking for `personal` AND `locked` in the SAME call
        is asking for a state the platform has no page to reach or
        leave. The reset to `open` wins -- honouring `locked` first and
        then immediately resetting it would land the row correctly by
        accident while writing two CONTRADICTORY audit rows for one
        call; this proves there is exactly one, and it says `open`."""
        make_admin()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL,
                                 library_posture=LIBRARY_LOCKED)
            row = IdentitySettings.get_solo()
            assert row.posture == POSTURE_PERSONAL
            assert row.library_posture == LIBRARY_OPEN
        events = list(AuditEvent.objects.filter(action=actions.LIBRARY_POSTURE_CHANGED))
        assert len(events) == 1
        assert events[0].detail["to"] == LIBRARY_OPEN

    def test_an_explicit_library_posture_is_ignored_while_already_personal(self):
        """The invariant -- `personal` implies an open library -- holds
        even outside a transition: a caller cannot re-lock the library
        by calling `set_posture(library_posture=...)` alone while the
        box sits in `personal`, with no `posture` argument at all."""
        make_admin()
        with posture(POSTURE_PERSONAL, library_posture=LIBRARY_OPEN):
            services.set_posture(OPEN_PRINCIPAL, library_posture=LIBRARY_LOCKED)
            assert IdentitySettings.get_solo().library_posture == LIBRARY_OPEN

    def test_an_invalid_library_posture_is_refused(self):
        make_admin()
        with pytest.raises(services.ServiceRefused) as exc:
            services.set_posture(OPEN_PRINCIPAL, library_posture="not-a-real-value")
        assert "library posture" in str(exc.value).lower()
        assert IdentitySettings.get_solo().library_posture == LIBRARY_OPEN

    def test_the_content_toggle_is_audited_with_its_new_value(self):
        """It is the one setting that changes what an administrator can
        READ, so "when did this box start letting admins read
        conversations" must be answerable from the log rather than from
        memory."""
        make_admin()
        services.set_posture(OPEN_PRINCIPAL, admin_sees_content=True)
        row = AuditEvent.objects.get(action=actions.ADMIN_CONTENT_ACCESS_CHANGED)
        assert row.detail == {"to": True}

    def test_turning_the_content_toggle_on_is_refused_by_nothing(self, settings):
        """A widening the operator is entitled to make; the audit row is
        the record that they made it."""
        settings.DEBUG = True
        services.set_posture(OPEN_PRINCIPAL, admin_sees_content=True)
        assert IdentitySettings.get_solo().admin_sees_content is True

    def test_a_non_integer_session_idle_minutes_is_refused_not_a_bare_valueerror(self):
        """A programming-error-shaped bare `ValueError` from `int(...)`
        would look identical to every OTHER `ValueError` a caller might
        raise by accident; `ServiceRefused` is the one this module ever
        means to raise on purpose."""
        make_admin()
        with pytest.raises(services.ServiceRefused):
            services.set_posture(OPEN_PRINCIPAL, session_idle_minutes="not-a-number")


@pytest.mark.django_db(transaction=True)
class TestTheLastAdminGuardUnderConcurrency:
    """ITS OWN CLASS, with its own database mark.

    `transaction=True` gives each test a real, committing database
    rather than the module-level mark's wrapping transaction -- which
    is required here and nowhere else in this module: two threads
    cannot see each other's uncommitted rows, so under the ordinary
    mark the second thread would see NO superusers, take a different
    branch, and the test would pass for the wrong reason. It is also
    much slower (it truncates tables between tests), which is why it is
    quarantined here rather than applied to the whole module.
    """

    def test_the_last_admin_guard_serialises_two_concurrent_demotions(self):
        """"At least one row satisfying a predicate" is not expressible
        as a CheckConstraint, so two simultaneous demotions could each
        see two admins and each proceed. The guard runs inside
        `transaction.atomic()` with `select_for_update()`, which
        serialises them and makes the second see one admin and refuse.

        Exercised with two real connections rather than mocked: a guard
        that is only correct single-threaded is a guard that fails on
        the one day it matters."""
        import threading
        from django.db import connections
        first, second = make_admin(), make_admin()
        actor = user_principal(first)
        errors: list[Exception] = []

        def demote(target_pk):
            try:
                target = get_user_model().objects.get(pk=target_pk)
                services.set_superuser(actor, target, False)
            except services.ServiceRefused as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=demote, args=(pk,))
                   for pk in (first.pk, second.pk)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert get_user_model().objects.filter(
            is_active=True, is_superuser=True).count() >= 1
        assert len(errors) == 1


class TestDeactivationDropsGrants:
    def test_deactivating_removes_every_grant_that_account_held(self):
        """Owner decision 25: grants are dropped. Reactivation does NOT
        restore them -- a dropped grant is a decision somebody made, and
        restoring it silently would undo that decision without anybody
        choosing to."""
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            services.deactivate_user(user_principal(admin), member)
            assert EntitlementGrant.objects.filter(user=member).count() == 0
            services.reactivate_user(user_principal(admin), member)
        assert EntitlementGrant.objects.filter(user=member).count() == 0

    def test_a_group_grant_survives_a_members_deactivation(self):
        """The grant is the GROUP'S, not this account's. Removing it
        would revoke an entitlement from everybody else in the group."""
        admin = make_admin()
        member = make_user()
        group = make_group(name="analysts")
        member.groups.add(group)
        finance = make_entitlement(name="Finance")
        grant(finance, group=group)
        with posture(POSTURE_ENTERPRISE):
            services.deactivate_user(user_principal(admin), member)
        assert EntitlementGrant.objects.filter(group=group).count() == 1
