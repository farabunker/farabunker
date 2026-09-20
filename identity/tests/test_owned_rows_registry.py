"""All five owned tables register themselves, and every one resolves.

Lives in `identity/tests/` rather than in each column's, deliberately:
the property being tested is that the REGISTRY is complete, and that is
a fact about the whole tree rather than about any one column.
"""
from __future__ import annotations

import pytest
from django.apps import apps
from django.conf import settings

from identity.contracts.ownership import all_owned_rows

pytestmark = pytest.mark.django_db


def test_every_owned_table_is_registered():
    """Five tables in three columns. A sixth later is a REGISTRATION,
    not an edit to a command that would otherwise silently skip it."""
    keys = {spec.key for spec in all_owned_rows()}
    expected = {"agents.agent", "agents.flow", "agents.conversation", "rag.askrecord"}
    if "vision" in settings.FARABUNKER_FEATURES:
        expected.add("vision.generationjob")
    assert keys == expected


def test_every_registered_model_resolves_through_apps_get_model():
    """Resolved at command time, never imported -- which is what lets
    `identity/` walk tables in columns it may not import."""
    for spec in all_owned_rows():
        model = apps.get_model(spec.model)
        assert {"owner_kind", "owner_key"} <= {f.name for f in model._meta.get_fields()}


def test_the_vision_registration_is_behind_its_feature_flag():
    """It registers inside `tools/vision/apps.py`'s existing early exit,
    so a box with the feature off does not carry a registration naming a
    model whose app registered nothing else either."""
    keys = {spec.key for spec in all_owned_rows()}
    assert ("vision.generationjob" in keys) == ("vision" in settings.FARABUNKER_FEATURES)
