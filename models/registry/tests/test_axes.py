"""This column's entitlement axis: registered from `ready()`, and
attaching/detaching through `models/registry/labels.py` rather than
around it.

Same ANTI-VACUOUS pop-first registration shape `agents/tests/
test_axes.py` uses, for the same reason.
"""
from __future__ import annotations

import pytest
from django.apps import apps

from identity.contracts import actions
from identity.contracts import axes as axes_module
from identity.contracts.axes import AxisRow
from identity.models import AuditEvent
from identity.testing import make_entitlement, make_user, user_principal
from models.registry.axes import (
    model_set_counts, model_set_ids_for, model_set_rows, set_model_set_axis,
)
from models.registry.models import ModelSet, ModelSetEntitlement

pytestmark = pytest.mark.django_db


class TestReadyRegistersTheModelSetAxis:
    def test_ready_registers_it(self):
        apps.get_app_config("inference").ready()
        axes_module._AXES.pop("inference.model_sets", None)
        assert "inference.model_sets" not in {
            spec.key for spec in axes_module.all_entitlement_axes()}

        apps.get_app_config("inference").ready()
        by_key = {spec.key: spec for spec in axes_module.all_entitlement_axes()}
        assert by_key["inference.model_sets"].label == "Model sets"
        assert by_key["inference.model_sets"].editable is True

    def test_running_ready_twice_registers_one_axis_not_two(self):
        apps.get_app_config("inference").ready()
        apps.get_app_config("inference").ready()
        keys = [spec.key for spec in axes_module.all_entitlement_axes()]
        assert keys.count("inference.model_sets") == 1

    def test_every_registered_handler_really_resolves(self):
        from django.utils.module_loading import import_string

        apps.get_app_config("inference").ready()
        spec = {s.key: s for s in axes_module.all_entitlement_axes()}[
            "inference.model_sets"]
        for path in (spec.counts, spec.rows, spec.ids_for, spec.set_for):
            assert callable(import_string(path)), path


class TestTheModelSetAxis:
    def test_the_catalogue_is_every_set_name_ordered(self):
        ModelSet.objects.create(name="Zulu")
        alpha = ModelSet.objects.create(name="Alpha")
        assert model_set_rows()[0] == AxisRow(str(alpha.pk), "Alpha")
        assert [row.name for row in model_set_rows()] == ["Alpha", "Zulu"]

    def test_active_ids_and_counts_read_the_table(self):
        finance = make_entitlement(name="Finance")
        model_set = ModelSet.objects.create(name="Alpha")
        ModelSetEntitlement.objects.create(model_set=model_set, entitlement=finance)
        assert model_set_ids_for(finance.pk) == {str(model_set.pk)}
        assert model_set_counts() == {finance.pk: 1}

    def test_attach_and_detach_go_through_the_labels_writers_and_audit(self):
        actor = user_principal(make_user())
        finance = make_entitlement(name="Finance")
        model_set = ModelSet.objects.create(name="Alpha")
        assert set_model_set_axis(finance.pk, add={str(model_set.pk)}, remove=set(),
                                  actor=actor) == {"added": 1, "removed": 0}
        assert AuditEvent.objects.filter(action=actions.MODELSET_ATTACHED).count() == 1
        assert set_model_set_axis(finance.pk, add=set(), remove={str(model_set.pk)},
                                  actor=actor) == {"added": 0, "removed": 1}
        assert AuditEvent.objects.filter(action=actions.MODELSET_DETACHED).count() == 1
        assert not ModelSetEntitlement.objects.filter(entitlement=finance).exists()

    def test_a_resubmitted_state_writes_nothing_and_says_so(self):
        """`attach`/`detach` are already idempotent; the SUMMARY is what
        needed the read, because an operator reads that line to learn
        what happened."""
        actor = user_principal(make_user())
        finance = make_entitlement(name="Finance")
        model_set = ModelSet.objects.create(name="Alpha")
        set_model_set_axis(finance.pk, add={str(model_set.pk)}, remove=set(),
                           actor=actor)
        AuditEvent.objects.all().delete()
        assert set_model_set_axis(finance.pk, add={str(model_set.pk)}, remove=set(),
                                  actor=actor) == {"added": 0, "removed": 0}
        assert AuditEvent.objects.count() == 0

    def test_the_counts_stay_flat(self, django_assert_num_queries):
        for index in range(5):
            entitlement = make_entitlement(name=f"E{index}")
            ModelSetEntitlement.objects.create(
                model_set=ModelSet.objects.create(name=f"S{index}"),
                entitlement=entitlement)
        with django_assert_num_queries(1):
            counts = model_set_counts()
        assert sorted(counts.values()) == [1, 1, 1, 1, 1]
