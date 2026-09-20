"""The model sets pages: the console's own connection-sets edge, and
the sets page's entitlement-attachment edge.

Both routes are class S (`identity/routes.py`) -- a model connection is
box inventory, not somebody's library row, so `IdentityGateMiddleware`
refuses a non-admin before either view runs; neither imports
`identity.gate` (the Task 6 precedent).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from django.utils.html import escape

from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember
from models.registry.tests._helpers import (
    grant, make_admin, make_chat_connection, make_entitlement, make_user, posture,
    reset_settings, seed_sweep_posture, sign_in,
)

from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestConnectionSets:
    """POST /inference/connections/<pk>/sets/ -- the membership edge, on
    the console itself."""

    def test_an_admin_saves_the_memberships(self, client):
        connection = make_chat_connection()
        model_set = ModelSet.objects.create(name="Chat models")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(
                reverse("inference-connection-sets", args=[connection.pk]),
                {"sets": [str(model_set.pk)]},
            )
        assert response.status_code == 302
        assert set(ModelSetMember.objects.filter(connection=connection)
                   .values_list("model_set_id", flat=True)) == {model_set.pk}

    def test_a_member_gets_403(self, client):
        connection = make_chat_connection()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(
                reverse("inference-connection-sets", args=[connection.pk]), {"sets": []})
        assert response.status_code == 403

    def test_a_non_numeric_set_id_is_refused_without_a_500(self, client):
        connection = make_chat_connection()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(
                reverse("inference-connection-sets", args=[connection.pk]),
                {"sets": ["not-a-number"]},
            )
        assert response.status_code == 302
        assert ModelSetMember.objects.filter(connection=connection).count() == 0

    def test_an_unknown_connection_id_is_404(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(
                reverse("inference-connection-sets", args=[999999]), {"sets": []})
        assert response.status_code == 404

    def test_a_well_formed_but_unknown_set_id_is_refused_without_a_500(self, client):
        """T14 review finding 2: `isdigit()` alone let a numeric-but-
        nonexistent set id reach `ModelSetMember.objects.create(
        model_set_id=...)`, an insert the deferred FK does not catch
        until commit -- a reproducible 500 in production even though no
        test posting only well-formed ids ever saw it."""
        connection = make_chat_connection()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(
                reverse("inference-connection-sets", args=[connection.pk]),
                {"sets": ["999999"]},
            )
        assert response.status_code == 302
        assert ModelSetMember.objects.filter(connection=connection).count() == 0

    def test_the_memberships_round_trip(self, client):
        """Submitting a DIFFERENT set list replaces the old one -- the
        write is the DIFFERENCE (`set_memberships_for`), not an append."""
        connection = make_chat_connection()
        one, two = ModelSet.objects.create(name="One"), ModelSet.objects.create(name="Two")
        ModelSetMember.objects.create(model_set=one, connection=connection)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            client.post(reverse("inference-connection-sets", args=[connection.pk]),
                       {"sets": [str(two.pk)]})
        assert set(ModelSetMember.objects.filter(connection=connection)
                   .values_list("model_set_id", flat=True)) == {two.pk}


class TestMembershipProvenance:
    """W-3 (IA-2 walkthrough finding): `ModelSetMember.added_by` (and
    `ModelSetEntitlement.attached_by`) were never stamped when a
    membership or an attachment was saved from the console/sets page --
    mirroring the `labelled_by` ruling `agents/labels.py::set_tool_labels`
    already follows: the writer accepts a caller-supplied `User`
    instance, threaded from `identity.request.user_for_request(request)`,
    and stamps it on create."""

    def test_a_membership_is_stamped_with_the_signed_in_user(self, client):
        connection = make_chat_connection()
        model_set = ModelSet.objects.create(name="Chat models")
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("inference-connection-sets", args=[connection.pk]),
                       {"sets": [str(model_set.pk)]})
        member = ModelSetMember.objects.get(connection=connection, model_set=model_set)
        assert member.added_by_id == admin.pk

    def test_the_writer_leaves_it_null_when_the_caller_passes_none(self):
        from models.registry.labels import set_memberships_for
        from identity.testing import user_principal

        connection = make_chat_connection()
        model_set = ModelSet.objects.create(name="Under test")
        set_memberships_for(user_principal(make_admin()), connection, {model_set.pk})
        member = ModelSetMember.objects.get(connection=connection, model_set=model_set)
        assert member.added_by is None

    def test_an_attachment_is_stamped_with_the_signed_in_user(self, client):
        model_set = ModelSet.objects.create(name="Under test")
        finance = make_entitlement(name="Finance")
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("inference-model-set-edit", args=[model_set.pk]),
                       {"action": "attach", "entitlement": str(finance.pk)})
        attachment = ModelSetEntitlement.objects.get(model_set=model_set, entitlement=finance)
        assert attachment.attached_by_id == admin.pk

    def test_the_attach_writer_leaves_it_null_when_the_caller_passes_none(self):
        from models.registry.labels import attach
        from identity.testing import user_principal

        model_set = ModelSet.objects.create(name="Under test")
        finance = make_entitlement(name="Finance")
        attach(user_principal(make_admin()), model_set, finance.pk)
        attachment = ModelSetEntitlement.objects.get(model_set=model_set, entitlement=finance)
        assert attachment.attached_by is None


class TestModelSetsPage:
    """GET/POST /inference/sets/ -- create a set; POST
    /inference/sets/<pk>/ -- rename, delete, attach, detach."""

    def test_an_admin_creates_a_set(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("inference-model-sets"), {"name": "Finance models"})
        assert response.status_code == 302
        assert ModelSet.objects.filter(name="Finance models").exists()

    def test_a_member_gets_403(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("inference-model-sets"))
        assert response.status_code == 403

    def test_a_duplicate_set_name_is_refused_with_a_readable_message(self, client):
        ModelSet.objects.create(name="Under test")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("inference-model-sets"), {"name": "under test"},
                                   follow=True)
        assert response.status_code == 200
        assert b"already exists" in response.content
        assert ModelSet.objects.filter(name="Under test").count() == 1

    def test_an_unknown_set_id_is_404(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("inference-model-set-edit", args=[999999]),
                                   {"action": "rename", "name": "x"})
        assert response.status_code == 404

    def test_an_unrecognised_action_flashes_and_redirects(self, client):
        """F7's class (Coherence Wave C): flash-and-redirect, not a raw
        400 -- the same conversion the three `identity/views.py` action
        dispatchers took in the same pass."""
        model_set = ModelSet.objects.create(name="Under test")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("inference-model-set-edit", args=[model_set.pk]),
                                   {"action": "not-a-real-action"}, follow=True)
        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse("inference-model-sets")
        assert escape("'not-a-real-action' is not a recognised action.") in \
            response.content.decode()
        model_set.refresh_from_db()
        assert model_set.name == "Under test"

    def test_renaming_a_set(self, client):
        model_set = ModelSet.objects.create(name="Under test")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("inference-model-set-edit", args=[model_set.pk]),
                                   {"action": "rename", "name": "Renamed"})
        assert response.status_code == 302
        model_set.refresh_from_db()
        assert model_set.name == "Renamed"

    def test_attaching_and_detaching_an_entitlement(self, client):
        model_set = ModelSet.objects.create(name="Under test")
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            client.post(reverse("inference-model-set-edit", args=[model_set.pk]),
                       {"action": "attach", "entitlement": str(finance.pk)})
        assert ModelSetEntitlement.objects.filter(model_set=model_set,
                                                  entitlement=finance).exists()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            client.post(reverse("inference-model-set-edit", args=[model_set.pk]),
                       {"action": "detach", "entitlement": str(finance.pk)})
        assert not ModelSetEntitlement.objects.filter(model_set=model_set,
                                                       entitlement=finance).exists()

    def test_a_well_formed_but_unknown_entitlement_id_is_refused_without_a_500(
            self, client):
        """T14 review finding 2: `isdigit()` alone let a numeric-but-
        nonexistent entitlement id reach `ModelSetEntitlement.objects.
        create(entitlement_id=...)`, an insert the deferred FK does not
        catch until commit -- a reproducible 500 no test posting only
        real ids ever saw. Validated against `labelling_entitlements`,
        the same set the attach form's own `<select>` is built from."""
        model_set = ModelSet.objects.create(name="Under test")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(
                reverse("inference-model-set-edit", args=[model_set.pk]),
                {"action": "attach", "entitlement": "999999"},
            )
        assert response.status_code == 302
        assert ModelSetEntitlement.objects.filter(model_set=model_set).count() == 0

    def test_the_delete_confirmation_names_both_counts(self, client):
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set,
                                      connection=make_chat_connection())
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("inference-model-sets")).content
        assert b"Models: 1" in body
        assert b"Entitlements: 1" in body

    def test_deleting_a_set_takes_both_edges_with_it(self, client):
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set,
                                      connection=make_chat_connection())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("inference-model-set-edit", args=[model_set.pk]),
                                   {"action": "delete"})
        assert response.status_code == 302
        assert not ModelSet.objects.filter(pk=model_set.pk).exists()
        assert ModelSetMember.objects.count() == 0


class TestModelSetsPageNav:
    """ROUND 17 ADDENDUM (the round-16 nav-link reviewer's own follow-on
    finding): this page marks its PARENT sidebar entry (Models) current
    FROM A CHILD url (`/inference/sets/`, not `inference-console`
    itself) -- `foundation/templates/_shell.html`'s own `.current-
    parent` comment has the full reasoning. Before this fix the
    `Models` entry here was bolded and DEAD: `nav.settings-nav a.
    current`'s own `pointer-events: none` made it unclickable, with no
    other link back to the console anywhere on this page.
    """

    def test_the_models_entry_is_marked_current_parent_not_current(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("inference-model-sets")).content.decode()
        assert f'href="{reverse("inference-console")}" class="current-parent"' in body
        assert f'href="{reverse("inference-console")}" class="current"' not in body

    def test_the_current_parent_rule_carries_no_pointer_events_none(self, client):
        """Non-vacuous companion to the markup pin above: a real `href`
        alone is not enough (`nav.top-nav a.current`'s own pre-round-16
        history is exactly that -- a real `href`, made dead by CSS) --
        this proves the RULE `class="current-parent"` resolves to on
        this rendered page is not the inert one."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("inference-model-sets")).content.decode()
        start = body.index("nav.settings-nav a.current-parent {")
        rule = body[start:body.index("}", start)]
        assert "pointer-events" not in rule
        assert "font-weight: 600;" in rule


class TestTheUnattachedSetStateIsVisible:
    """Decision 28 makes an unattached set restrict nothing. A page that
    said otherwise would tell an administrator their model under test is
    locked while every account can still use it -- and this is the middle
    of the routine workflow, not an edge case."""

    def test_a_set_with_members_and_no_attachment_says_it_restricts_nobody(self, client):
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set,
                                      connection=make_chat_connection())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("inference-model-sets")).content
        assert b"restricts nobody" in body

    def test_the_warning_goes_once_an_entitlement_is_attached(self, client):
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set,
                                      connection=make_chat_connection())
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("inference-model-sets")).content
        assert b"restricts nobody" not in body

    def test_the_console_sentence_is_true_in_both_states(self, client):
        """The partial's own copy, asserted rather than trusted: it must
        carry the "a set with no entitlement attached narrows nothing"
        clause, or it is a sentence that is false for half the
        workflow.

        The console must be in its WARM state (`_registered_connection.
        html` renders only there): a healthy engine, faked the same way
        `test_views.py::_mock_engine` fakes one, so the registry does not
        need a real model server running."""
        make_chat_connection()
        engine = MagicMock()
        engine.is_healthy.return_value = True
        engine.name = "ollama"
        with posture(POSTURE_ENTERPRISE), patch("models.registry.views.ENGINES") as mock_engines:
            mock_engines.values.return_value = [engine]
            sign_in(client, make_admin())
            body = client.get(reverse("inference-console")).content
        assert b"narrows nothing" in body
