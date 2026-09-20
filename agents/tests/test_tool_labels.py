"""`agents/labels.py` -- reading and writing tool labels, and the
entitlement-delete cascade this column registers.
"""
from __future__ import annotations

import pytest
from django.db import connection

from agents.labels import set_tool_labels, tool_entitlement_ids, tool_labels_cascade
from agents.models import ToolEntitlement
from agents.tests._helpers import (
    make_admin, make_entitlement, make_user, posture, reset_settings, seed_sweep_posture,
    user_principal,
)
from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestReadingLabels:
    def test_one_query_maps_every_labelled_key_to_its_entitlements(self):
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        assert tool_entitlement_ids() == {"rag.search": frozenset({finance.pk, legal.pk})}

    def test_an_unlabelled_install_maps_nothing(self):
        assert tool_entitlement_ids() == {}


class TestWritingLabels:
    def test_setting_labels_writes_the_difference_and_audits_both_directions(self):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_tool_labels(actor, "rag.search", {finance.pk, legal.pk})
            assert AuditEvent.objects.filter(action=actions.TOOL_LABELLED).count() == 2
            set_tool_labels(actor, "rag.search", {finance.pk})
        assert set(ToolEntitlement.objects.values_list("entitlement_id", flat=True)) == {finance.pk}
        assert AuditEvent.objects.filter(action=actions.TOOL_UNLABELLED).count() == 1

    def test_setting_the_same_labels_twice_writes_no_second_event(self):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_tool_labels(actor, "rag.search", {finance.pk})
            set_tool_labels(actor, "rag.search", {finance.pk})
        assert AuditEvent.objects.filter(action=actions.TOOL_LABELLED).count() == 1

    def test_the_current_labels_read_happens_inside_the_transaction(self, monkeypatch):
        """`_set_labels` takes `read_current` as a callable and calls it as
        the first statement inside its own `transaction.atomic()` block, so
        the read and the writes it diffs against share one transaction --
        never a read taken before the block opens."""
        import agents.labels as labels_module

        admin = make_admin()
        finance = make_entitlement(name="Finance")
        seen_in_atomic_block = []
        real_labels_for = labels_module.labels_for

        def spy(tool_key):
            seen_in_atomic_block.append(connection.in_atomic_block)
            return real_labels_for(tool_key)

        monkeypatch.setattr(labels_module, "labels_for", spy)
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_tool_labels(actor, "rag.search", {finance.pk})
        assert seen_in_atomic_block == [True]


class TestLabelledBy:
    """`labelled_by` has no writer anywhere else in this plan -- the
    caller passes a real `identity` `User` instance (or nothing), and
    this module never imports `identity.models` to get one itself."""

    def test_passing_labelled_by_stamps_it_on_created_rows(self):
        admin = make_admin()
        editor = make_user(username="editor")
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_tool_labels(actor, "rag.search", {finance.pk}, labelled_by=editor)
        row = ToolEntitlement.objects.get(tool_key="rag.search", entitlement=finance)
        assert row.labelled_by_id == editor.pk

    def test_omitting_labelled_by_leaves_it_null(self):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_tool_labels(actor, "rag.search", {finance.pk})
        row = ToolEntitlement.objects.get(tool_key="rag.search", entitlement=finance)
        assert row.labelled_by_id is None


class TestTheCascade:
    def test_counting_never_writes_and_running_removes(self):
        finance = make_entitlement(name="Finance")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        assert tool_labels_cascade(finance.pk, commit=False) == 1
        assert ToolEntitlement.objects.count() == 1
        assert tool_labels_cascade(finance.pk, commit=True) == 1
        assert ToolEntitlement.objects.count() == 0
