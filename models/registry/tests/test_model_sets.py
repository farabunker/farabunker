"""Model sets: the group of connections an entitlement attaches to.

TWO EDGES, EDITED INDEPENDENTLY. That is the whole design: adding a
model to a set reaches every entitlement already attached, and attaching
an entitlement reaches every model already in. Neither edge knows about
the other's far end.
"""
from __future__ import annotations

import pytest
from django.db import transaction
from django.db.utils import IntegrityError

from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember
from models.registry.tests._helpers import make_chat_connection, make_entitlement

pytestmark = pytest.mark.django_db


class TestModelSet:
    def test_the_name_is_case_insensitively_unique(self):
        ModelSet.objects.create(name="Under test")
        # `transaction.atomic()` inside the `raises` block, same as
        # `tools/rag/tests/test_categories.py::
        # TestCategoryCaseInsensitiveUniqueness` -- this file's later
        # module-level `_settings` autouse fixture runs a query in its
        # OWN teardown (`reset_settings()`), so the expected IntegrityError
        # must be confined to a savepoint rather than poisoning the whole
        # per-test transaction.
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ModelSet.objects.create(name="under test")


class TestTheTwoEdges:
    def test_one_membership_per_set_and_connection(self):
        model_set = ModelSet.objects.create(name="Under test")
        connection = make_chat_connection()
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ModelSetMember.objects.create(model_set=model_set, connection=connection)

    def test_one_attachment_per_set_and_entitlement(self):
        model_set = ModelSet.objects.create(name="Under test")
        finance = make_entitlement(name="Finance")
        ModelSetEntitlement.objects.create(model_set=model_set, entitlement=finance)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ModelSetEntitlement.objects.create(model_set=model_set, entitlement=finance)

    def test_deleting_the_set_takes_both_edges(self):
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set,
                                      connection=make_chat_connection())
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement())
        model_set.delete()
        assert ModelSetMember.objects.count() == 0
        assert ModelSetEntitlement.objects.count() == 0

    def test_deleting_the_connection_takes_only_its_membership(self):
        model_set = ModelSet.objects.create(name="Under test")
        connection = make_chat_connection()
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement())
        connection.delete()
        assert ModelSetMember.objects.count() == 0
        assert ModelSet.objects.count() == 1
        assert ModelSetEntitlement.objects.count() == 1


from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL
from models.registry.access import UNRESTRICTED_MODEL_ACCESS, ModelAccess, model_access_for
from models.registry.tests._helpers import (
    grant, make_admin, make_user, posture, reset_settings, seed_sweep_posture,
    user_principal,
)


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


def _set_with(connection, *entitlements, name=None):
    """A set holding `connection`, attached to `entitlements`."""
    model_set = ModelSet.objects.create(name=name or f"set-{connection.pk}")
    ModelSetMember.objects.create(model_set=model_set, connection=connection)
    for entitlement in entitlements:
        ModelSetEntitlement.objects.create(model_set=model_set, entitlement=entitlement)
    return model_set


class TestTheValue:
    def test_the_default_is_unrestricted_and_allows_everything(self):
        assert UNRESTRICTED_MODEL_ACCESS.allows(1) is True

    def test_an_absent_pk_is_in_no_set_and_therefore_allowed(self):
        access = ModelAccess(required={7: frozenset({1})}, held=frozenset(),
                             unrestricted=False)
        assert access.allows(9) is True
        assert access.allows(7) is False

    def test_holding_any_one_reaching_entitlement_is_enough(self):
        access = ModelAccess(required={7: frozenset({1, 2})}, held=frozenset({2}),
                             unrestricted=False)
        assert access.allows(7) is True


class TestModelAccessFor:
    def test_open_posture_is_unrestricted_and_runs_no_permission_query(
            self, django_assert_num_queries):
        # PINNED EXPLICITLY: this file's autouse `_settings` fixture only
        # seeds POSTURE_OPEN when `FARABUNKER_TEST_POSTURE` is unset --
        # under the enterprise posture sweep it would otherwise inherit
        # enterprise here, defeating a test whose whole point is the open
        # branch's zero-query behaviour (every posture-dependent test
        # pins its own posture, never inherits the sweep's).
        with posture(POSTURE_OPEN):
            with django_assert_num_queries(1):
                access = model_access_for(OPEN_PRINCIPAL)
        assert access.unrestricted is True
        assert access.required == {}

    def test_a_box_with_no_sets_at_all_is_inert(self):
        """The change must cost a box that never creates a set nothing at
        all: every connection is in no set, so nothing is narrowed."""
        connection = make_chat_connection()
        with posture(POSTURE_ENTERPRISE):
            access = model_access_for(user_principal(make_user()))
        assert access.required == {}
        assert access.allows(connection.pk) is True

    def test_a_holder_reaches_a_set_restricted_connection_and_a_non_holder_does_not(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        connection = make_chat_connection()
        _set_with(connection, finance)
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        grant(legal, user=other)
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(holder)).allows(connection.pk) is True
            assert model_access_for(user_principal(other)).allows(connection.pk) is False

    def test_a_connection_in_two_sets_is_reachable_through_either(self):
        """The OR across sets. A model an operator put in both the
        "chat" set and the "under test" set is reachable by a holder of
        either set's entitlement."""
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        connection = make_chat_connection()
        _set_with(connection, finance, name="one")
        _set_with(connection, legal, name="two")
        first, second = make_user(), make_user()
        grant(finance, user=first)
        grant(legal, user=second)
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(first)).allows(connection.pk) is True
            assert model_access_for(user_principal(second)).allows(connection.pk) is True

    def test_a_set_with_no_entitlement_attached_restricts_nothing(self):
        """Otherwise an operator could hide a model from themselves by
        creating a set and forgetting to attach anything, with no page
        saying so."""
        connection = make_chat_connection()
        model_set = ModelSet.objects.create(name="Empty")
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(make_user())).allows(
                connection.pk) is True

    def test_one_membership_reaches_every_attached_entitlement(self):
        """THE OWNER'S FIRST PROPERTY, proved by a count: adding ONE row
        makes a model reachable by every entitlement already attached."""
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        model_set = ModelSet.objects.create(name="Chat models")
        for entitlement in (finance, legal):
            ModelSetEntitlement.objects.create(model_set=model_set,
                                               entitlement=entitlement)
        first, second = make_user(), make_user()
        grant(finance, user=first)
        grant(legal, user=second)
        connection = make_chat_connection()
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(first)).allows(connection.pk) is True
            assert model_access_for(user_principal(second)).allows(connection.pk) is True

    def test_one_attachment_reaches_every_member(self):
        """THE OWNER'S SECOND PROPERTY: attaching ONE entitlement makes
        every model already in the set reachable."""
        finance = make_entitlement(name="Finance")
        model_set = ModelSet.objects.create(name="Chat models")
        connections = [make_chat_connection(), make_chat_connection()]
        for connection in connections:
            ModelSetMember.objects.create(model_set=model_set, connection=connection)
        member = make_user()
        grant(finance, user=member)
        ModelSetEntitlement.objects.create(model_set=model_set, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            access = model_access_for(user_principal(member))
        assert all(access.allows(connection.pk) for connection in connections)

    def test_an_admin_with_the_content_setting_off_is_still_restricted(self):
        """Using somebody's model is USING, not administering. Editing a
        set is administering, and that answers to `is_admin`."""
        connection = make_chat_connection()
        _set_with(connection, make_entitlement(name="Legal"))
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(admin)).allows(connection.pk) is False
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            assert model_access_for(user_principal(admin)).allows(connection.pk) is True

    def test_a_service_principal_reaches_connections_in_no_set_only(self):
        restricted, plain = make_chat_connection(), make_chat_connection()
        _set_with(restricted, make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            access = model_access_for(SERVICE_PRINCIPAL)
        assert access.allows(restricted.pk) is False
        assert access.allows(plain.pk) is True


class TestTheRolePathIsExempt:
    """Spec section 22.32, flagged for the owner. A model resolved
    through a ROLE must not be narrowed: `db_provider` and `role_primary`
    answer for `rag.embed`, `rag.transcribe`, `rag.extract` and the
    default answering bindings, none of which any user selects and none
    of which has a principal at all.
    """

    def test_role_resolution_ignores_a_set_entirely(self):
        from models.contracts.bindings import resolve
        from models.contracts.roles import RAG_EMBED_ROLE
        from models.registry.tests._helpers import bind, make_embed_connection
        connection = make_embed_connection()
        bind(RAG_EMBED_ROLE, connection)
        _set_with(connection, make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert resolve(RAG_EMBED_ROLE) is not None

    def test_the_role_path_takes_no_access_argument_at_all(self):
        """Not "passes unrestricted" -- takes NONE. A parameter would be a
        parameter somebody eventually threads a real value into."""
        import inspect
        from models.registry.bindings import db_provider
        assert "access" not in inspect.signature(db_provider).parameters


class TestReadyRegistersTheModelSetCascade:
    """The cascade registration in `InferenceConfig.ready()` was untested
    production wiring until this pin -- same anti-vacuous shape as
    `agents/tests/test_apps.py::TestReadyRegistersTheToolLabelsCascade`:
    pop the key first, so passing proves `ready()` put it back rather
    than some earlier registration this process already ran.
    """

    def test_ready_registers_the_model_set_cascade(self):
        from django.apps import apps as django_apps

        from identity.contracts import cascades as cascades_module

        django_apps.get_app_config("inference").ready()
        cascades_module._CASCADES.pop("inference.model_sets", None)
        registered = {spec.key for spec in cascades_module.all_entitlement_cascades()}
        assert "inference.model_sets" not in registered

        django_apps.get_app_config("inference").ready()
        by_key = {
            spec.key: spec for spec in cascades_module.all_entitlement_cascades()
        }
        assert "inference.model_sets" in by_key
        assert by_key["inference.model_sets"].handler == \
            "models.registry.labels.model_set_cascade"
