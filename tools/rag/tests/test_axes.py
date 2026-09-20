"""This column's entitlement axis: registered from `ready()`, COUNTED
ONLY.

Same ANTI-VACUOUS pop-first registration shape `agents/tests/
test_axes.py` uses, plus the pin that this one deliberately carries no
editing trio -- a Documents transfer panel listing every document in a
library that scales to thousands is the wrong control, and the
entitlement page's reach panel already names the count.
"""
from __future__ import annotations

import pytest
from django.apps import apps
from django.utils.module_loading import import_string

from identity.contracts import axes as axes_module
from identity.testing import make_entitlement
from tools.rag.axes import document_counts
from tools.rag.models import DocumentEntitlement
from tools.rag.tests._helpers import make_document

pytestmark = pytest.mark.django_db


class TestReadyRegistersTheDocumentsAxis:
    def test_ready_registers_it(self):
        apps.get_app_config("rag").ready()
        axes_module._AXES.pop("rag.documents", None)
        assert "rag.documents" not in {
            spec.key for spec in axes_module.all_entitlement_axes()}

        apps.get_app_config("rag").ready()
        by_key = {spec.key: spec for spec in axes_module.all_entitlement_axes()}
        assert by_key["rag.documents"].label == "Documents"
        assert callable(import_string(by_key["rag.documents"].counts))

    def test_running_ready_twice_registers_one_axis_not_two(self):
        apps.get_app_config("rag").ready()
        apps.get_app_config("rag").ready()
        keys = [spec.key for spec in axes_module.all_entitlement_axes()]
        assert keys.count("rag.documents") == 1

    def test_it_registers_the_library_door(self):
        """FIX ROUND 2, P2-I1. The entitlement page offers the library
        because THIS column registered the way there -- `identity/` names
        `rag-documents` nowhere, which is rule 4's second half."""
        apps.get_app_config("rag").ready()
        spec = {s.key: s for s in axes_module.all_entitlement_axes()}["rag.documents"]
        assert spec.has_door is True
        assert callable(import_string(spec.link))
        assert spec.hint, "the door's own sentence is this column's to supply"

    def test_the_door_is_the_library_filtered_to_that_entitlement(self):
        from django.urls import reverse

        from tools.rag.axes import document_link

        finance = make_entitlement(name="Finance")
        assert document_link(finance.pk) == (
            f"{reverse('rag-documents')}?entitlement={finance.pk}")

    def test_it_is_counted_but_not_editable(self):
        """A DELIBERATE ASYMMETRY, pinned so it stays a decision rather
        than drifting into an oversight: a document label is the one
        labelled kind an entitlement OWNER may write, and the library
        page owns that write with its own posture warnings."""
        apps.get_app_config("rag").ready()
        spec = {s.key: s for s in axes_module.all_entitlement_axes()}["rag.documents"]
        assert spec.editable is False
        assert (spec.rows, spec.ids_for, spec.set_for) == (None, None, None)


class TestTheDocumentCounts:
    def test_they_read_the_label_table(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        DocumentEntitlement.objects.create(document=make_document(), entitlement=finance)
        DocumentEntitlement.objects.create(document=make_document(), entitlement=finance)
        assert document_counts() == {finance.pk: 2}
        assert legal.pk not in document_counts()

    def test_they_stay_flat(self, django_assert_num_queries):
        for index in range(5):
            DocumentEntitlement.objects.create(
                document=make_document(), entitlement=make_entitlement(name=f"E{index}"))
        with django_assert_num_queries(1):
            counts = document_counts()
        assert sorted(counts.values()) == [1, 1, 1, 1, 1]
