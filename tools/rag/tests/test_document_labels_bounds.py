"""A-3 residue (round-3 hardening H33): the bulk-label route's `category`
branch gets the same bound the `ids` branch already had for free.

`36d4b11` (C-05) already closed the other two thirds of A-3 on this branch
-- `is_admin_flag`/`owned_ids` hoisted out of the per-document loop in
`tools.rag.views.document_labels_bulk`, and `services.
documents_targeted_for_labelling` prefetching `entitlement_labels` on both
branches. What was left: the `ids` branch is implicitly capped at 1000
rows by the framework's own POST field limit, but the `category` branch --
which takes PRECEDENCE over `ids` -- had no cap of its own, so one member
holding a single entitlement could drive an unbounded loop of label writes
and audit rows against the largest shelf on the box.

NEW MODULE (round-3 constraint 26): `tools/rag/tests/` is a whole
directory another session edits this round; a task needing rag tests adds
a new module rather than appending to an existing one.
`test_document_label_page.py:906-918` (`TestBulkLabellingIsFlatInTheDocumentCount`,
C-05's own pin) is read here for its shape, not edited.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity.contracts.postures import POSTURE_ENTERPRISE
from tools.rag import services
from tools.rag.models import Category, DocumentEntitlement
from tools.rag.tests._helpers import (
    grant, make_admin, make_document, make_entitlement, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


def _shelf(count, **overrides):
    """`count` documents on one freshly-made `Category` shelf."""
    category = Category.objects.create(name=f"shelf-{Category.objects.count()}")
    documents = [make_document(category=category, **overrides) for _ in range(count)]
    return category, documents


class TestA3TheCategoryBranchIsBounded:
    """The residue: `services.documents_targeted_for_labelling`'s
    `category` branch, which the view resolves BEFORE the per-document
    loop even starts (`document_labels_bulk`, the `targets = services.
    documents_targeted_for_labelling(...)` line)."""

    def test_a_shelf_above_the_ceiling_is_refused_by_name(self, client, monkeypatch):
        # PATCH THE CONSTANT, do not build 1001 rows: constraint 3 runs
        # this suite four times, and a four-thousand-row fixture buys
        # nothing the behaviour test below does not already prove.
        monkeypatch.setattr(services, "MAX_BULK_LABEL_TARGETS", 3)
        entitlement = make_entitlement(name="Big")
        member = make_user()
        grant(entitlement, user=member, role="owner")
        category, _documents = _shelf(4)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"action": "apply", "entitlements": [str(entitlement.pk)],
                                    "category": category.name}, follow=True)
        assert "select rows instead" in response.content.decode()
        assert DocumentEntitlement.objects.count() == 0

    def test_a_shelf_at_the_ceiling_still_works(self, client, monkeypatch):
        monkeypatch.setattr(services, "MAX_BULK_LABEL_TARGETS", 3)
        entitlement = make_entitlement(name="Big")
        admin = make_admin()
        category, documents = _shelf(3)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"action": "apply", "entitlements": [str(entitlement.pk)],
                                    "category": category.name}, follow=True)
        assert "Added labels to 3 document(s)." in response.content.decode()
        assert DocumentEntitlement.objects.count() == 3
        for document in documents:
            assert document.entitlement_labels.count() == 1

    def test_the_shipped_ceiling_is_the_number_the_docs_name(self):
        """The one un-patched assertion: the constant itself, pinned
        once, so patching it everywhere else cannot hide a change to it.
        `tools/rag/README.md`'s bulk-label section names this same
        number."""
        assert services.MAX_BULK_LABEL_TARGETS == 1000

    def test_the_category_branch_wins_when_a_post_carries_both(self, client):
        """The PRECEDENCE this whole finding rests on. `documents_
        targeted_for_labelling` checks `category` first and returns from
        that branch, so a POST carrying both targets the shelf and
        ignores the id list entirely -- which is why capping the `ids`
        branch alone (the framework already does) left the door open,
        and why the cap had to go on `category`."""
        admin = make_admin()
        entitlement = make_entitlement(name="Both")
        category, shelved = _shelf(3)
        unshelved = [make_document() for _ in range(2)]
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(
                reverse("rag-document-labels-bulk"),
                {"action": "apply", "entitlements": [str(entitlement.pk)],
                 "category": category.name,
                 "documents": [str(document.pk) for document in unshelved]},
                follow=True,
            )
        assert "Added labels to 3 document(s)." in response.content.decode()
        assert DocumentEntitlement.objects.count() == 3
        for document in shelved:
            assert document.entitlement_labels.count() == 1
        for document in unshelved:
            assert document.entitlement_labels.count() == 0

    def test_the_frameworks_field_cap_is_the_number_the_id_branch_leans_on(self):
        """`MAX_BULK_LABEL_TARGETS` is 1000 to MATCH the bound the `ids`
        branch already gets for free -- Django refuses a POST carrying
        more than `DATA_UPLOAD_MAX_NUMBER_FIELDS` fields, so `len(ids)`
        can never exceed it. That number is Django's own default and
        this platform does not override it; if either side ever moves,
        the two doors stop agreeing and this test is where that shows
        up, rather than in an operator's refusal message."""
        from django.conf import settings as django_settings

        assert django_settings.DATA_UPLOAD_MAX_NUMBER_FIELDS == 1000
        assert services.MAX_BULK_LABEL_TARGETS == django_settings.DATA_UPLOAD_MAX_NUMBER_FIELDS

    def test_the_id_branch_is_unchanged(self, client):
        """It already has the framework's 1000-field bound; this task
        must not add a second, different number to the same door."""
        admin = make_admin()
        entitlement = make_entitlement(name="Ids")
        documents = [make_document() for _ in range(5)]
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"action": "apply", "entitlements": [str(entitlement.pk)],
                                    "documents": [str(document.pk) for document in documents]},
                                   follow=True)
        assert "Added labels to 5 document(s)." in response.content.decode()
        assert DocumentEntitlement.objects.count() == 5

    @pytest.mark.parametrize("count", [1, 25])
    def test_the_category_branch_is_flat_in_shelf_size(
            self, client, count, django_assert_num_queries):
        """C-05's hoist and prefetch are pinned on the `ids` branch at
        `test_document_label_page.py::TestBulkLabellingIsFlatInTheDocumentCount`
        -- read, not edited (constraint 26). This is that same pin's
        `category`-branch twin, so the new `count()` bound this task adds
        cannot quietly turn the category branch's own loop back into one
        that scales with shelf size: one extra query for the bound check,
        same total regardless of whether the shelf holds 1 row or 25.

        EVERY TARGET ALREADY CARRIES THE ENTITLEMENT BEING APPLIED, on
        purpose -- same reason the `ids`-branch pin does this: `updated ==
        current` for every document, so `set_document_labels`'s own
        per-document write never runs, and the only per-document cost
        this pin can see is the read-side one C-05 is about.
        """
        admin = make_admin()
        entitlement = make_entitlement(name="Flat")
        category, documents = _shelf(count)
        for document in documents:
            DocumentEntitlement.objects.create(document=document, entitlement=entitlement)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            with django_assert_num_queries(_CATEGORY_BRANCH_BASELINE_QUERIES):
                response = client.post(reverse("rag-document-labels-bulk"),
                                       {"action": "apply",
                                        "entitlements": [str(entitlement.pk)],
                                        "category": category.name})
        assert response.status_code == 302


# One extra query over `test_document_label_page.py`'s own
# `_BULK_LABEL_BASELINE_QUERIES` (16, the `ids` branch): the `count()`
# this task's bound checks before the queryset is ever returned. Named
# once so both parametrized shelf sizes assert the same number, and a
# reviewer can see what moves it.
_CATEGORY_BRANCH_BASELINE_QUERIES = 17
