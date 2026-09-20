"""The five tables identity owns.

MODULE-LEVEL `pytest.mark.django_db`: every assertion here touches the
database. No `FARABUNKER_FEATURES` override and no `reverse()`, so the
vision-flag rule does not apply.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.utils import IntegrityError

from identity.contracts.postures import (
    LIBRARY_OPEN, POSTURE_OPEN, SESSION_IDLE_MINUTES_DEFAULT,
)
from identity.models import AuditEvent, IdentitySettings
from identity.tests._helpers import make_user

pytestmark = pytest.mark.django_db


class TestUser:
    def test_the_project_user_model_is_ours(self):
        """`AUTH_USER_MODEL` cannot be changed once rows reference it,
        which is why this model exists NOW with no extra fields at all:
        the cost of adding a field later to a model we own is one
        migration, while the cost of swapping the model later is
        database surgery on a box holding a document library."""
        assert get_user_model()._meta.label == "identity.User"
        assert get_user_model()._meta.db_table == "identity_user"

    def test_it_adds_no_fields_of_its_own(self):
        """Deliberate. A field here would be a field IA-2 has to reason
        about; the empty subclass is the whole point.

        `"id"` is in the difference because `AbstractUser` is ABSTRACT:
        Django adds the implicit `AutoField` primary key when a
        CONCRETE model is built, so the parent's `get_fields()` has no
        pk to compare against. `"logentry"` is the reverse accessor
        `django.contrib.admin.LogEntry` creates by pointing at
        `AUTH_USER_MODEL`. `"entitlements_created"`, `"entitlement_
        grants"` and `"grants_made"` are the same kind of artefact,
        added by IA-2 Task 2: the reverse accessors `Entitlement.
        created_by`, `EntitlementGrant.user` and `EntitlementGrant.
        granted_by` create by pointing AT `AUTH_USER_MODEL`.
        `"shares_received"` and `"shares_made"` are Task 4's own pair of
        the same artefact: `agents.models.Share.user` and `Share.
        shared_by` point AT `AUTH_USER_MODEL` too. `"model_sets_created"`
        is IA-2 T14's own: `models.registry.models.ModelSet.created_by`
        points AT `AUTH_USER_MODEL` the same way. `"authored_turns"` is
        Security round 3's own (commit `2e9a473`): `agents.models.
        Turn.author` points AT `AUTH_USER_MODEL` to attribute a shared
        conversation's turns, the same kind of reverse accessor as
        every other name here. `ToolEntitlement.
        labelled_by`, `ModelSetMember.added_by` and `ModelSetEntitlement.
        attached_by` do NOT add one each -- all three use
        `related_name="+"`, which asks Django not to create one at all.
        None of the nine named here is a field this model declares, and
        asserting `<=` rather than `==` keeps the test honest if Django
        or a later phase adds another such artefact."""
        from django.contrib.auth.models import AbstractUser
        inherited = {f.name for f in AbstractUser._meta.get_fields()}
        ours = {f.name for f in get_user_model()._meta.get_fields()}
        assert ours - inherited <= {
            "id", "logentry", "entitlements_created", "entitlement_grants", "grants_made",
            "shares_received", "shares_made", "model_sets_created", "authored_turns",
        }

    def test_a_created_user_is_active_and_not_a_superuser_by_default(self):
        user = get_user_model().objects.create_user(username="ann", password="x")
        assert user.is_active is True
        assert user.is_superuser is False


class TestIdentitySettings:
    def test_get_solo_creates_the_row_on_first_read(self):
        """Same shape `tools.rag.models.RagSettings.get_solo` uses --
        `get_or_create(pk=1)`, never raising `DoesNotExist`. No seed
        migration, so a backup restored from before this phase behaves
        identically to a fresh install."""
        assert not IdentitySettings.objects.exists()
        row = IdentitySettings.get_solo()
        assert row.pk == 1
        assert IdentitySettings.objects.count() == 1
        assert IdentitySettings.get_solo().pk == 1
        assert IdentitySettings.objects.count() == 1

    def test_the_defaults_are_the_decision(self):
        """`open` posture is today's behaviour; `admin_sees_content`
        defaults FALSE because administering is not reading."""
        row = IdentitySettings.get_solo()
        assert row.posture == POSTURE_OPEN
        assert row.library_posture == LIBRARY_OPEN
        assert row.admin_sees_content is False
        assert row.session_idle_minutes == SESSION_IDLE_MINUTES_DEFAULT


class TestAuditEvent:
    def test_it_carries_no_foreign_key_to_a_user(self):
        """An audit row a cascade could delete is not an audit row. Two
        strings and a denormalised label, exactly like
        `ToolInvocation`'s principal columns -- which is also why
        `0001_initial` needs no `swappable_dependency`."""
        relations = [f.name for f in AuditEvent._meta.get_fields() if f.is_relation]
        assert relations == []

    def test_a_row_that_already_has_a_primary_key_may_not_be_saved_again(self):
        """Append-only, ENFORCED rather than described. `save()` is not
        the whole guard -- a queryset `.update()`/`.delete()` never calls
        it -- which is why the AST guard in `test_column_boundaries.py`
        forbids `AuditEvent.objects` outside `identity/audit.py`
        entirely."""
        row = AuditEvent.objects.create(actor_kind="open", actor_key="box",
                                        action="identity.login")
        row.action = "identity.logout"
        with pytest.raises(ValueError) as exc:
            row.save()
        assert "append-only" in str(exc.value).lower()

    def test_an_action_outside_the_catalogue_is_refused(self):
        """A typo'd action name is a construction error here, not a
        category that silently splits an audit report in two."""
        with pytest.raises(ValueError) as exc:
            AuditEvent.objects.create(actor_kind="open", actor_key="box",
                                      action="identity.did_a_thing")
        assert "identity.did_a_thing" in str(exc.value)

    def test_it_reads_newest_first(self):
        for action in ("identity.login", "identity.logout"):
            AuditEvent.objects.create(actor_kind="open", actor_key="box", action=action)
        assert [r.action for r in AuditEvent.objects.all()] == [
            "identity.logout", "identity.login"]


class TestEntitlement:
    """The named permission everything in IA-2 is granted against."""

    def test_the_name_is_case_insensitively_unique(self):
        from identity.models import Entitlement
        Entitlement.objects.create(name="Finance")
        with pytest.raises(IntegrityError):
            Entitlement.objects.create(name="finance")

    def test_deleting_the_creator_leaves_the_entitlement(self):
        """SET_NULL, not CASCADE: deleting a user must never delete an
        entitlement. Users are deactivated rather than deleted anyway --
        the null branch exists for the hypothetical shell deletion, not
        as a normal path."""
        from identity.models import Entitlement
        creator = make_user()
        row = Entitlement.objects.create(name="Finance", created_by=creator)
        creator.delete()
        row.refresh_from_db()
        assert row.created_by is None


class TestEntitlementGrant:
    """A grant names a user XOR a group, never both and never neither."""

    def test_a_grant_naming_both_a_user_and_a_group_is_refused(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        with pytest.raises(IntegrityError):
            EntitlementGrant.objects.create(
                entitlement=entitlement, user=make_user(),
                group=Group.objects.create(name="analysts"))

    def test_a_grant_naming_neither_is_refused(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        with pytest.raises(IntegrityError):
            EntitlementGrant.objects.create(entitlement=entitlement)

    def test_one_user_grant_per_entitlement(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        user = make_user()
        EntitlementGrant.objects.create(entitlement=entitlement, user=user)
        with pytest.raises(IntegrityError):
            EntitlementGrant.objects.create(entitlement=entitlement, user=user)

    def test_two_group_grants_for_one_entitlement_are_refused(self):
        """The partial unique earns its keep here: NULLs do not collide in
        Postgres, so a plain UniqueConstraint(entitlement, user, group)
        would happily store the same group grant a thousand times."""
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        group = Group.objects.create(name="analysts")
        EntitlementGrant.objects.create(entitlement=entitlement, group=group)
        with pytest.raises(IntegrityError):
            EntitlementGrant.objects.create(entitlement=entitlement, group=group)

    def test_the_role_is_a_column_not_a_second_row(self):
        """Promoting a member to owner is an UPDATE. That is why the
        unique constraints do not include `role`: two rows for one
        (entitlement, user) pair would make "does this person hold E"
        ambiguous."""
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        user = make_user()
        row = EntitlementGrant.objects.create(entitlement=entitlement, user=user)
        row.role = EntitlementGrant.Role.OWNER
        row.save(update_fields=["role"])
        assert EntitlementGrant.objects.filter(entitlement=entitlement, user=user).count() == 1

    def test_source_defaults_to_manual_and_sso_is_declared(self):
        """`source` is written by nothing today. It is present from day
        one because it is the column an SSO reconciliation joins on, and
        backfilling that distinction later is impossible."""
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        row = EntitlementGrant.objects.create(entitlement=entitlement, user=make_user())
        assert row.source == EntitlementGrant.Source.MANUAL
        assert EntitlementGrant.Source.SSO.value == "sso"

    def test_deleting_the_entitlement_takes_its_grants(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        EntitlementGrant.objects.create(entitlement=entitlement, user=make_user())
        entitlement.delete()
        assert EntitlementGrant.objects.count() == 0

    def test_deleting_the_group_takes_its_grants(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        group = Group.objects.create(name="analysts")
        EntitlementGrant.objects.create(entitlement=entitlement, group=group)
        group.delete()
        assert EntitlementGrant.objects.count() == 0
